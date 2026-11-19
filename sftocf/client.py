"""Clients for talking to the Starfish REST and Redash APIs, and the AD glue they use."""
import time
import logging

import requests

from coldfront.core.utils.common import import_from_settings
from coldfront.core.resource.models import Resource
from coldfront.core.project.models import Project

from sftocf.conf import SFURL, STORAGE_RESOURCES
from sftocf.signals import starfish_add_aduser, starfish_remove_aduser, starfish_add_adgroup
from sftocf.utils import get_allocation_path, get_resource_starfish_name, record_process, uniques_and_intersection

logger = logging.getLogger(__name__)


def zone_report():
    """Check of SF zone alignment with pipeline specs.
    Report on:
        Coldfront projects with storage allocations vs. SF zones
        AD groups corresponding to SF zones that don't belong to AD group starfish_groups
        SF zones have all allocations that correspond to Coldfront project allocations
        SF zones that don’t have groups
        SF zones that have users as opposed to groups
    """
    report = {
        'projects_with_allocations_no_zones': [],
        'zones_with_no_projects': [],
        'zones_with_no_groups': [],
        'zones_with_users': [],
    }
    # start by getting all zones
    server = StarFishServer()
    # get list of all zones in server
    zones = server.get_zones()

    # get all projects with at least one storage allocation
    projects = Project.objects.filter(
        allocation__status__name='Active',
        allocation__resources__in=server.get_corresponding_coldfront_resources(),
    ).distinct()
    # check which of these projects have zones
    project_titles = [p.title for p in projects]
    zone_names = [z['name'] for z in zones]
    projs_no_zones, projs_with_zones, zones_no_projs = uniques_and_intersection(
        project_titles, zone_names)
    report['projs_with_zones'] = {
            p['name']:p['id'] for p in [z for z in zones if z['name'] in projs_with_zones]
    }
    report['projects_with_allocations_no_zones'] = projs_no_zones
    report['zones_with_no_projects'] = zones_no_projs
    no_group_zones = [z['name'] for z in zones if not z.get('managing_groups')]
    report['zones_with_no_groups'] = no_group_zones
    user_zones = [z for z in zones if z.get('managers')]
    report['zones_with_users'] = [
        f"{z['name']}: {z['managers']}" for z in user_zones
    ]
    report_nums = {k: len(v) for k, v in report.items()}
    for r in [report, report_nums]:
        print(r)
        logger.warning(r)

def allocation_to_zone(allocation):
    """
    1. Check whether the allocation is in Starfish
    2. If so, check whether a zone exists for the allocation's project.
    3. If not, create a zone for the allocation's project.
    4. Add the allocation to the zone.
    """
    server = StarFishServer()
    resource = allocation.resources.first()
    resource_name = get_resource_starfish_name(resource)
    if not any(sf_res == resource_name for sf_res in server.volumes):
        return None
    project = allocation.project
    zone = server.get_zone_by_name(project.title)
    if zone:
        zone_paths = zone['paths']
        new_path = f"{resource_name}:{get_allocation_path(allocation)}"
        zone_paths.append(new_path)
        zone.update_zone(paths=zone_paths)
    else:
        zone = server.zone_from_project(project.title)
    return zone

def add_zone_manager_to_ad(username):
    try:
        starfish_add_aduser.send_robust(sender='sftocf', username=username)
    except Exception as e:
        print(f'Error adding {username} to starfish_users: {e}')
        logger.exception('Error adding %s to starfish_users: %s', username, e)
        raise

def add_zone_group_to_ad(group_name):
    try:
        starfish_add_adgroup.send_robust(sender='sftocf', groupname=group_name)
    except Exception as e:
        print(f'Error adding {group_name} to starfish_users: {e}')
        logger.exception('Error adding %s to starfish_users: %s', group_name, e)
        raise

def remove_zone_member_from_ad(username):
    try:
        starfish_remove_aduser.send_robust(sender='sftocf', username=username)
    except Exception as e:
        print(f'Error removing {username} from starfish_users: {e}')
        logger.exception('Error removing %s from starfish_users: %s', username, e)
        raise


class StarFishServer:
    """Class for interacting with StarFish REST API.
    """

    def __init__(self, sf_url=SFURL):
        self.name = sf_url
        self.api_url = f'{sf_url}/api/'
        self.token = self.get_auth_token()
        self.headers = generate_headers(self.token)
        self.volumes = self.get_volume_names()

    @record_process
    def get_auth_token(self):
        """Obtain a token through the auth endpoint.
        """
        username = import_from_settings('SFUSER')
        password = import_from_settings('SFPASS')
        auth_url = self.api_url + 'auth/'
        todo = {'username': username, 'password': password}
        response = requests.post(auth_url, json=todo)
        # response.status_code
        response_json = response.json()
        token = response_json['token']
        return token

    # 2A. Generate list of volumes to search, along with top-level paths
    @record_process
    def get_volume_names(self):
        """Generate a list of the volumes available on the server.
        """
        response = self.get_volume_attributes()
        volnames = [i['vol'] for i in response]
        return volnames

    def get_groups(self):
        """get set of group names on starfish"""
        url = self.api_url + 'mapping/group/'
        response = return_get_json(url, self.headers)
        groupnames = {g['name'] for g in response}
        return groupnames

    def get_zones(self, zone_id=''):
        """Get all zones from the API, or the zone with the corresponding ID
        """
        url = self.api_url + f'v2/zones/{zone_id}'
        response = return_get_json(url, self.headers)
        return response

    def get_zone_by_name(self, zone_name):
        """Get a zone by name"""
        zones = self.get_zones()
        return next((z for z in zones if z['name'] == zone_name), None)

    def create_zone(self, zone_name, paths, managers, managing_groups):
        """Create a zone via the API"""
        url = self.api_url + 'v2/zones/'
        data = {
            "name": zone_name,
            "members": {
                'users': managers,
                'groups': managing_groups,
            },
        }
        logger.debug(data)
        response = return_post_json(url, data=data, headers=self.headers)
        logger.debug(response)
        if paths:
            self.update_zone_paths(response['id'], paths)
        return response

    def delete_zone(self, zone_id, zone_name=None):
        """Delete a zone via the API"""
        if not zone_id:
            zone = self.get_zone_by_name(zone_name)
            zone_id = zone['id']
        url = self.api_url + f'v2/zones/{zone_id}?force=True'
        response = requests.delete(url, headers=self.headers)
        if response.status_code not in [204]:
            raise ValueError(f'Cannot delete zone {zone_name} ({zone_id}): {response.text}')
        return response

    def put_zone(self, zone_id, paths=(), managers=(), managing_groups=()):
        """Update a zone via the API"""
        zone_data = self.get_zones(zone_id=zone_id)
        url = self.api_url + f'v2/zones/{zone_id}/'
        data = {'name': zone_data['name']}
        data['paths'] = paths if paths else zone_data['paths']
        data['managers'] = managers if managers else zone_data['managers']
        data['managing_groups'] = managing_groups if managing_groups else zone_data['managing_groups']
        response = return_put_json(url, data=data, headers=self.headers)
        return response

    def update_zone_members(
        self, zone_id, member_users=(), member_groups=(), admin_users=(), admin_groups=()
    ):
        url = self.api_url + f"v2/zones/{zone_id}/members/"
        data = {}
        if member_users or member_groups:
            data["members"] = {}
            if member_users:
                data["members"]['users'] = {'set': member_users}
            if member_groups:
                data["members"]['groups'] = {'set': member_groups}
        if admin_users or admin_groups:
            data["admins"] = {}
            if admin_users:
                data["admins"]['users'] = {'set': admin_users}
            if admin_groups:
                data["admins"]['groups'] = {'set': admin_groups}
        response = return_patch_json(url, data=data, headers=self.headers)
        return response


    def update_zone(self, zone_name, paths=(), managers=(), managing_groups=()):
        """Update a zone via the API

        Returns the list of paths that could not be added to the zone.
        """
        zone_data = self.get_zone_by_name(zone_name)
        zone_id = zone_data['id']
        for group in managing_groups:
            add_zone_group_to_ad(group['groupname'])
        if managing_groups != () or managers != ():
            self.update_zone_members(
                zone_id, member_users=managers, member_groups=managing_groups
            )
        failed_paths = []
        if paths != ():
            failed_paths = self.update_zone_paths(zone_id, paths)
        return failed_paths

    def get_zone_paths(self, zone_id):
        url = self.api_url + f'v2/zones/{zone_id}/zones_roots/'
        response = return_get_json(url, self.headers)
        return response

    def update_zone_paths(self, zone_id, paths):
        """Update paths in a zone via the API

        Returns the list of paths that could not be added to the zone.
        """
        zone = self.get_zones(zone_id=zone_id)
        url = self.api_url + f'v2/zones/{zone_id}/zones_roots/'
        data = {'set': paths}
        failed_paths = []
        try:
            return_patch_json(url, data=data, headers=self.headers)
        except Exception as e:
            logger.warning('Error adding paths to zone %s: %s', zone['name'], e)
            existing_paths = [p['vol_path'] for p in self.get_zone_paths(zone_id)]
            paths_to_remove = [p for p in existing_paths if p not in paths]
            if paths_to_remove:
                logger.warning('Removing paths from zone %s: %s', zone['name'], paths_to_remove)
                new_paths = [p for p in existing_paths if p not in paths_to_remove]
                return_patch_json(url, data={'delete': paths_to_remove}, headers=self.headers)

                existing_paths = new_paths
            for path in paths:
                if path not in existing_paths:
                    try:
                        return_patch_json(url, data={'add': [path]}, headers=self.headers)
                        existing_paths.append(path)
                    except Exception as e:
                        logger.warning(
                            'Error adding path %s to zone %s: %s', path, zone['name'], e)
                        failed_paths.append(path)
        return failed_paths

    def get_zone_user_params(self, zone_id):
        """Get zone user params via the API"""
        url = self.api_url + f'v2/zones/{zone_id}/zone_user_params/'
        response = return_get_json(url, self.headers)
        return response

    def update_zone_user_param(self, zone_id, param_name, param_value):
        """Update zone user params via the API"""
        url = self.api_url + f'v2/zones/{zone_id}/zone_user_params/{param_name}/'
        data = param_value
        response = return_patch_json(url, data=data, headers=self.headers)
        return response

    def zone_from_department(self, department_obj):
        """Create a zone from a department object"""
        zone_name = f"{department_obj.code}_Labs"
        paths = [
            f"{get_resource_starfish_name(a.resources.first())}:{get_allocation_path(a)}"
            for p in department_obj.get_projects().filter(
                status__name__in=['Active', 'New'],
                )
            for a in p.allocation_set.filter(
                status__name='Active',
                resources__in=self.get_corresponding_coldfront_resources()
            )
            if get_allocation_path(a)
        ]
        approvers = list(
            department_obj.members.filter(role='Approver').values_list('user__username', flat=True)
        )
        for approver_name in approvers:
            add_zone_manager_to_ad(approver_name)
        managers = [{'username': u} for u in approvers]
        return self.create_zone(zone_name, paths, managers, [])

    def zone_from_project(self, project_obj):
        """Create a zone from a project object"""
        zone_name = project_obj.title
        paths = [
            f"{get_resource_starfish_name(a.resources.first())}:{get_allocation_path(a)}"
            for a in project_obj.allocation_set.filter(
                status__name__in=['Active', 'Updated', 'Ready for Review'],
                resources__in=self.get_corresponding_coldfront_resources()
            )
            if get_allocation_path(a)
        ]
        managing_groups = [{'groupname': project_obj.title}]
        add_zone_group_to_ad(project_obj.title)
        return self.create_zone(zone_name, paths, [], managing_groups)

    def get_corresponding_coldfront_resources(self):
        matching_pks = [
            r.pk for r in STORAGE_RESOURCES
            if any(volume == get_resource_starfish_name(r) for volume in self.volumes)
        ]
        return Resource.objects.filter(pk__in=matching_pks)

    def get_volumes_in_coldfront(self):
        resource_volume_list = [
            get_resource_starfish_name(r) for r in STORAGE_RESOURCES
        ]
        return [v for v in self.volumes if v in resource_volume_list]

    def get_volume_attributes(self):
        url = self.api_url + 'volume/'
        response = return_get_json(url, self.headers)
        return response

    def get_tags(self):
        url = self.api_url + 'tag/'
        response = return_get_json(url, self.headers)
        return response

    def get_scans(self):
        """Collect scans of all volumes in Coldfront
        """
        volumes = '&'.join([f'volume={v}' for v in self.get_volumes_in_coldfront()])
        url = self.api_url + 'scan/?' + volumes
        response_raw = requests.get(url, headers=self.headers)
        response = response_raw.json()
        return response

    def get_most_recent_scans(self):
        """Narrow scan data to the most recent and last successful scan for each
        Coldfront volume.
        """
        scans_narrowed = []
        scans = self.get_scans()
        volumes = self.get_volumes_in_coldfront()
        for volume in volumes:
            latest_time = max(
                s['creation_time'] for s in scans['scans']
                if s['volume'] == volume
            )
            latest_scan = next(
                s for s in scans['scans']
                if s['creation_time'] == latest_time and s['volume'] == volume
            )
            scans_narrowed.append(latest_scan)
            if latest_scan['state']['is_running'] or latest_scan['state']['is_successful']:
                last_completed_time = max(
                    s['creation_time'] for s in scans['scans']
                    if not s['state']['is_running']
                    and s['state']['is_successful'] and s['volume'] == volume
                )
                last_completed = next(
                    s for s in scans['scans']
                    if s['creation_time'] == last_completed_time
                    and s['volume'] == volume
                )
                scans_narrowed.append(last_completed)
        return scans_narrowed

    @record_process
    def get_subpaths(self, volpath):
        """Generate list of directories in top layer of designated volpath.

        Parameters
        ----------
        volpath : string
            The volume and path.

        Returns
        -------
        subpaths : list of strings
        """
        getsubpaths_url = self.api_url + 'storage/' + volpath
        request = return_get_json(getsubpaths_url, self.headers)
        pathdicts = request['items']
        subpaths = [i['Basename'] for i in pathdicts]
        return subpaths

    def create_query(self, query, group_by, volpath, qformat='parent_path +aggrs.by_uid'):
        """Produce a Query class object.
        Parameters
        ----------
        query : string
        group_by : string
        volpath : string
        sec : integer, optional

        Returns
        -------
        query : Query class object
        """
        query = AsyncQuery(
            self.headers, self.api_url, query, group_by, volpath, qformat=qformat
        )
        data = query.result
        if not data:
            logger.warning('No starfish result for query %s', query)
        elif isinstance(data, dict) and 'error' in data:
            logger.warning('Error in starfish result for query %s:\n%s', query, data)
        return query

    @record_process
    def get_vol_membership(self, volume, voltype):
        url = self.api_url + f'mapping/{voltype}_membership?volume_name=' + volume
        member_list = return_get_json(url, self.headers)
        return member_list

    @record_process
    def get_vol_user_name_ids(self, volume):
        usermap_url = self.api_url + 'mapping/user?volume_name=' + volume
        users = return_get_json(usermap_url, self.headers)
        userdict = {u['uid']: u['name'] for u in users}
        return userdict

    @record_process
    def get_starfish_groups(self):
        url = f'{self.api_url}mapping/user_membership'
        group_dict = return_get_json(url, self.headers)
        group_list = [g['name'] for g in group_dict]
        return group_list


class StarFishRedash:
    def __init__(self, sf_url=SFURL):
        self.base_url = f'{sf_url}/redash/api/'
        self.queries = import_from_settings('REDASH_API_KEYS')

    def get_corresponding_coldfront_resources(self):
        volumes = [r['vol'] for r in self.get_vol_stats()]
        matching_pks = [
            r.pk for r in STORAGE_RESOURCES
            if any(volume in get_resource_starfish_name(r) for volume in volumes)
        ]
        return Resource.objects.filter(pk__in=matching_pks)

    def submit_query(self, queryname):
        """submit a query and return a json of the results."""
        query = self.queries[queryname]
        query_url = f'{self.base_url}queries/{query[0]}/results?api_key={query[1]}'
        result = return_get_json(query_url, headers={})
        return result

    def get_vol_stats(self):
        result = self.submit_query('vol_query')
        result = result['query_result']['data']['rows']
        result = [{
            k.replace(' ', '_').replace('(','').replace(')','') : v for k, v in d.items()
        } for d in result]
        resource_names = [get_resource_starfish_name(r) for r in STORAGE_RESOURCES]
        result = [r for r in result if r['vol'] in resource_names]
        return result

    def return_query_results(self, query='path_usage_query', volumes=None):
        """Return query results.
        """
        result = self.submit_query(query)
        if 'query_result' in result and result['query_result']:
            data = result['query_result']['data']['rows']
        else:
            raise ValueError('no query_result value found:\n', result)
        if volumes:
            data = [d for d in data if d['vol_name'] in volumes]
        return data


class AsyncQuery:
    def __init__(
        self, headers, api_url, query, group_by, volpath, qformat='parent_path +aggrs.by_uid'
    ):
        self.api_url = api_url
        self.headers = headers
        self.query_id = self.post_async_query(query, group_by, volpath, qformat=qformat)
        self._result = None

    @record_process
    def post_async_query(self, query, group_by, volpath, qformat='parent_path +aggrs.by_uid'):
        """Post an async query to the Starfish API."""
        query_url = self.api_url + 'async/query/'

        params = {
            'volumes_and_paths': volpath,
            'queries': query,
            'format': qformat,
            'sort_by': group_by,
            'group_by': group_by,
            'limit': '100000',
            'force_tag_inherit': 'false',
            'output_format': 'json',
            'delimiter': ',',
            'escape_paths': 'false',
            'print_headers': 'true',
            'size_unit': 'B',
            'humanize_nested': 'false',
            'mount_agent': 'None',
        }
        response = return_post_json(query_url, params=params, headers=self.headers)
        logger.debug('response: %s', response)
        return response['query_id']

    @property
    def result(self):
        if self._result:
            return self._result
        while True:
            query_check_url = self.api_url + 'async/query/' + self.query_id
            response = return_get_json(query_check_url, self.headers)
            if response['is_done'] == True:
                result = self.return_query_result()
                if isinstance(result, dict) and 'error' in result:
                    raise ValueError(f'Error in starfish result:\n{result}')
                self._result = result
                return self._result
            time.sleep(3)

    def return_query_result(self):
        query_result_url = self.api_url + 'async/query_result/' + self.query_id
        response = return_get_json(query_result_url, self.headers)
        return response


def return_get_json(url, headers):
    response = requests.get(url, headers=headers)
    return response.json()

def return_patch_json(url, data, headers):
    response = requests.patch(url, json=data, headers=headers)
    response.raise_for_status()
    return response.json()

def return_put_json(url, data, headers):
    response = requests.put(url, json=data, headers=headers)
    response.raise_for_status()
    return response.json()

def return_post_json(url, params=None, data=None, headers=None):
    response = requests.post(url, params=params, json=data, headers=headers)
    response.raise_for_status()
    return response.json()

def generate_headers(token):
    """Generate 'headers' attribute by using the 'token' attribute.
    """
    return {'accept': 'application/json', 'Authorization': f'Bearer {token}'}
