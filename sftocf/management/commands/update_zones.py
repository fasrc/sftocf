"""Add, update, and remove Starfish zones
- Add zones/zone paths
    - Collect all projects that have active allocations on Starfish
    - separate projects by whether a zone with the same name currently exists
    - Create zones for all projects that don’t yet exist
    - For the projects that do have zones, ensure that the corresponding zone:
        - has the project AD group in “managing_groups”
        - has all the allocation paths associated with the project
- Remove zones/zone paths
    - Collect all projects with allocations that were deactivated since the last successful run of the DjangoQ task, or in the past week
    - If the project no longer has any active allocations on Starfish, remove the zone
"""
import logging
from requests.exceptions import HTTPError

from django.core.management.base import BaseCommand

from coldfront.config.base import DEBUG
from coldfront.core.project.models import Project, ProjectAttributeType
from coldfront.core.department.models import Department
from sftocf.utils import StarFishServer

logger = logging.getLogger(__name__)
class Command(BaseCommand):
    help = 'Add, update, and remove Starfish zones based on Coldfront projects'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Do not make any changes to Starfish, just print what changes would be slated',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run'] or DEBUG
        if dry_run:
            logger.info('DRY RUN')

        report = {
            'dry_run': dry_run,
            'deleted_zones': [],
            'created_zones': [],
            'allocations_missing_paths': [],
            'added_zone_ids': [],
            'updated_zone_paths': [],
            'updated_zone_groups': []
        }

        sf = StarFishServer()
        starfish_zone_attr_type = ProjectAttributeType.objects.get(name='Starfish Zone')
        # collect all projects that have active allocations on Starfish

        # have zones for departments with more than 10 active projects
        zoned_departments = [
            dept for dept in Department.objects.all()
            if dept.get_projects().filter(status__name='Active').distinct().count() > 10
        ]
        # departments get matched with zones not via attributes, but by comparing the department name to the zone name
        starfish_zoned_department_names = [
            z['name'] for z in sf.get_zones()
            if z['name'] in [f"{d.code}_Labs" for d in zoned_departments]
        ]
        departments_with_zones = [
            d for d in Department.objects.all()
            if f"{d.code}_Labs" in starfish_zoned_department_names
        ]

        for dept in zoned_departments:
            if not dept.code:
                logger.info('no code for department %s', dept.name)
                continue
            if dept not in departments_with_zones:
                if not dry_run:
                    try:
                        zone = sf.zone_from_department(dept)
                    except HTTPError as e:
                        if e.response.status_code == 409:
                            err = f'zone for {dept.code} already exists; breaking'
                        elif e.response.status_code == 402:
                            err = 'zone quota reached; can no longer add any zones.'
                        else:
                            err = f'unclear error prevented creation of zone for department {dept.code}. error: {e.response}'
                        logger.error(err)
                        continue
                    except ValueError as e:
                        err = f"error encountered. If no groups returned, LDAP group doesn't exist: {e}, {dept.code}"
                        logger.error(err)
                        continue
                report['created_zones'].append(f"{dept.code}_Lab")
            else:
                logger.info("updating zone for department %s", dept.code)
                # ensure the zone has all the paths and managing groups
                zone_name = f"{dept.code}_Labs"
                paths = [
                    f"{a.resources.first().name.split('/')[0]}:{a.path}"
                    for p in dept.get_projects().filter(
                        status__name__in=['Active', 'New'],
                        )
                    for a in p.allocation_set.filter(
                        status__name__in=['Active', 'Pending Deactivation'],
                        resources__in=sf.get_corresponding_coldfront_resources()
                    )
                    if a.path
                ]
                sf.update_zone(zone_name, paths=list(paths))


        projects_with_allocations = Project.objects.filter(
            status__name='Active',
            allocation__status__name__in=['Active', 'Pending Deactivation'],
            allocation__resources__in=sf.get_corresponding_coldfront_resources(),
            title__in=sf.get_groups() # confirm the projects have groups in Starfish
        ).distinct()

        projects_with_zones = projects_with_allocations.filter(
            projectattribute__proj_attr_type=starfish_zone_attr_type,
        )
        # for the projects that do have zones, ensure that its zone:
        sf_cf_vols = sf.get_volumes_in_coldfront()
        for project in projects_with_zones:
            zone_id = project.sf_zone
            zone = sf.get_zones(zone_id)

            # has all the allocation paths associated with the project
            storage_allocations = project.allocation_set.filter(
                status__name__in=['Active', 'Pending Deactivation'],
                resources__in=sf.get_corresponding_coldfront_resources(),
            )
            try:
                vol_paths = zone['vol_paths']
            except Exception as e:
                logger.exception('problem identifying vol_paths for %s zone: %s', project.title, e)
                continue
            zone_paths_not_in_cf = [
                p['vol_path'] for p in zone['vol_paths']
                if p['vol_path'].split(':')[0] not in sf_cf_vols
            ]
            # don't update if any paths are missing
            missing_paths = False
            for a in storage_allocations:
                if a.path == '':
                    missing_paths = True
                    report['allocations_missing_paths'].append(a.pk)
                    logger.error('Allocation %s (%s) is missing a path; cannot update zone until this is fixed',
                        a.pk, a)
            if missing_paths:
                continue

            update = False
            paths = [f'{a.resources.first().name.split("/")[0]}:{a.path}' for a in storage_allocations] + zone_paths_not_in_cf
            if not set(paths) == set([p['vol_path'] for p in zone['vol_paths']]):
                update = True
                report['updated_zone_paths'].append({
                    'zone': zone['name'],
                    'old_paths': zone['vol_paths'],
                    'new_paths': paths,
                })

            # has the project AD group in “managing_groups”
            update_groups = zone['members']['groups']
            zone_group_names = [g['groupname'] for g in update_groups]
            if project.title not in zone_group_names:
                update = True
                update_groups.append({'groupname': project.title})
                report['updated_zone_groups'].append({
                    'zone': zone['name'],
                    'old_groups': zone_group_names,
                    'new_groups': zone_group_names + [project.title],
                })
            else:
                update_groups = ()
            if update and not dry_run:
                sf.update_zone(zone['name'], paths=paths, managing_groups=update_groups)
        # if project lacks "Starfish Zone" attribute, create or update the zone and save zone id to ProjectAttribute "Starfish Zone"
        projects_without_zones = projects_with_allocations.exclude(
            projectattribute__proj_attr_type=starfish_zone_attr_type,
        )
        for project in projects_without_zones:
            if not dry_run:
                try:
                    zone = sf.zone_from_project(project)
                    report['created_zones'].append(project.title)
                except HTTPError as e:
                    if e.response.status_code == 409:
                        err = f'zone for {project.title} already exists; adding zoneid to Project'
                        zone = sf.get_zone_by_name(project.title)
                        report['added_zone_ids'].append([project.title, zone['id']])
                    elif e.response.status_code == 402:
                        err = 'zone quota reached; can no longer add any zones.'
                    else:
                        err = f'unclear error prevented creation of zone for project {project.title}. error: {e.response}'
                    logger.error(err)
                    continue
                except ValueError as e:
                    err = f"error encountered. If no groups returned, LDAP group doesn't exist: {e}, {project.title}"
                    logger.error(err)
                    continue
                project.projectattribute_set.get_or_create(
                    proj_attr_type=starfish_zone_attr_type,
                    value=zone['id'],
                )
            else:
                report['created_zones'].append(project.title)

        # check whether to delete zones of projects with no active SF storage allocations
        potential_delete_zone_attr_projs = Project.objects.filter(
            projectattribute__proj_attr_type__name='Starfish Zone'
        ).exclude(
            title__in=[p.title for p in projects_with_allocations]
        )
        for project in potential_delete_zone_attr_projs:
            logger.info(project, project.pk)
            zone = sf.get_zones(project.sf_zone)
            zone_paths_not_in_cf = [
                p['vol_path'] for p in zone['vol_paths']
                if p['vol_path'].split(':')[0] not in sf_cf_vols
            ]
            # delete any zones that have no paths
            if not zone_paths_not_in_cf:
                if not dry_run:
                    try:
                        sf.delete_zone(zone['id'])
                    except ValueError as e:
                        err = f"error encountered when deleting zone {zone['name']}: {e}"
                        logger.error(err)
                        continue
                    # delete projectattribute
                    project.projectattribute_set.get(
                        proj_attr_type=starfish_zone_attr_type,
                    ).delete()
                report['deleted_zones'].append(zone['name'])
                continue
        logger.warning(report)
