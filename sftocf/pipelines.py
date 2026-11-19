"""Pipelines that reconcile Starfish usage data with Coldfront allocations."""
import logging
from itertools import groupby
from operator import itemgetter

from django.core.cache.backends.filebased import FileBasedCache
from django.utils import timezone

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttributeType,
    AllocationUserStatusChoice,
)

from sftocf.client import StarFishRedash, StarFishServer
from sftocf.conf import DATAPATH, DATESTR, PENDING_ACTIVE_ALLOCATION_STATUSES, svp, username_ignore_list
from sftocf.utils import (
    determine_size_fmt,
    get_allocation_path,
    get_resource_starfish_name,
    id_present_missing_users,
    record_process,
)

logger = logging.getLogger(__name__)

# Caches each lab's REST-collected usage data for a couple of days, so
# re-running the pull command the same (or next) day doesn't re-query
# Starfish for labs it already has fresh data for. A dedicated
# FileBasedCache (rather than django.core.cache.cache) so this doesn't
# depend on the host project's CACHES setting, while still persisting
# across the separate processes a cron-triggered management command runs in
# (unlike Django's default in-memory cache, which would reset every run).
_lab_usage_cache = FileBasedCache(DATAPATH, {})
_LAB_USAGE_CACHE_SECONDS = 60 * 60 * 24 * 2


class AllocationQueryMatch:
    """class to hold Allocations and related query results together."""
    def __new__(cls, allocation, total_usage_entries, user_usage_entries):
        allocation_data = (
            allocation.pk, allocation.project.title, allocation.resources.first()
        )
        msg = None
        if not total_usage_entries:
            msg = f'No starfish allocation usage result for allocation {allocation_data}; deactivation suggested'
        elif len(total_usage_entries) > 1:
            msg = f'too many total_usage_entries for allocation {allocation_data}; investigation required'
        if msg:
            logger.warning(msg)
            return None
        return super().__new__(cls)

    def __init__(self, allocation, total_usage_entries, user_usage_entries):
        self.allocation = allocation
        self.volume = get_resource_starfish_name(allocation.get_parent_resource)
        self.path = get_allocation_path(allocation)
        self.total_usage_entry = total_usage_entries[0]
        self.user_usage_entries = user_usage_entries

    @property
    def lab(self):
        return self.allocation.project.title

    @property
    def total_usage_tib(self):
        return round((self.total_usage_entry['total_size']/1099511627776), 5)

    @property
    def query_usernames(self):
        return [u['username'] for u in self.user_usage_entries]

    def update_user_usage(self, user, usage_bytes, usage, unit):
        """Get or create an allocationuser object with updated usage values."""
        allocationuser, created = self.allocation.allocationuser_set.get_or_create(
            user=user,
            defaults={
                'created': timezone.now(),
                'status': AllocationUserStatusChoice.objects.get(name='Active')
            }
        )
        if created:
            logger.info('allocation user %s created', allocationuser)
        allocationuser.usage_bytes = usage_bytes
        allocationuser.usage = usage
        allocationuser.unit = unit
        allocationuser.save()
        return allocationuser

    def update_usage_attr(self, usage_attribute_type, usage_value):
        usage_attribute, _ = self.allocation.allocationattribute_set.get_or_create(
            allocation_attribute_type=usage_attribute_type
        )
        usage = usage_attribute.allocationattributeusage
        usage.value = usage_value
        usage.save()
        return usage

    def users_in_list(self, username_list):
        """return usage entries for users with usernames in the provided list"""
        return [u for u in self.user_usage_entries if u['username'] in username_list]

def return_dict_of_groupings(dict_list, sort_key):
    """Given a list of dicts, return a dict of lists of dicts grouped by the
    key(s) given in sort_key.
    """
    grouped = groupby(sorted(dict_list, key=sort_key), key=sort_key)
    return {k: list(g) for k, g in grouped}


class UsageDataPipelineBase:
    """Base class for usage data pipeline classes."""

    def __init__(self, volume=None):
        self.connection_obj = self.return_connection_obj()
        if volume:
            self.volumes = [volume]
        else:
            resources = self.connection_obj.get_corresponding_coldfront_resources()
            self.volumes = [get_resource_starfish_name(r) for r in resources]

        self._allocations = None
        # self.collection_filter = self.set_collection_parameters()
        self.sf_user_data = self.collect_sf_user_data()
        self.sf_usage_data = self.collect_sf_usage_data()
        self._allocationquerymatches = None

    def return_connection_obj(self):
        raise NotImplementedError

    def collect_sf_user_data(self):
        raise NotImplementedError

    def collect_sf_usage_data(self):
        raise NotImplementedError

    @property
    def allocations(self):
        if self._allocations:
            return self._allocations
        allocation_statuses = PENDING_ACTIVE_ALLOCATION_STATUSES+['Pending Deactivation']
        self._allocations = Allocation.objects.filter(
            status__name__in=allocation_statuses,
            resources__in=self.connection_obj.get_corresponding_coldfront_resources()
        )
        return self._allocations

    @property
    def allocationquerymatches(self):
        # limit allocations to those in the volumes collected
        if self._allocationquerymatches:
            return self._allocationquerymatches
        allocations = self.allocations.prefetch_related(
            'project','allocationattribute_set', 'allocationuser_set')
        allocation_list = [
            (get_resource_starfish_name(a.get_parent_resource), get_allocation_path(a))
            for a in allocations
        ]
        total_sort_key = itemgetter('path','volume')
        allocation_usage_grouped = return_dict_of_groupings(self.sf_usage_data, total_sort_key)
        missing_allocations = [
            (k,a) for k, a in allocation_usage_grouped if (a, k) not in allocation_list
        ]
        logger.warning('starfish allocations missing in coldfront: %s', missing_allocations)

        user_usage = [user for user in self.sf_user_data if user['path'] is not None]
        user_sort_key = itemgetter('path','volume')
        user_usage_grouped = return_dict_of_groupings(user_usage, user_sort_key)

        missing_users = [u for k, u in user_usage_grouped.items() if k not in allocation_list]

        allocationquerymatch_objs = []
        for allocation in allocations:
            a = (str(get_allocation_path(allocation)), str(get_resource_starfish_name(allocation.get_parent_resource)))
            total_usage_entries = allocation_usage_grouped.get(a, None)
            user_usage_entries = user_usage_grouped.get(a, [])
            allocationquerymatch_objs.append(
                AllocationQueryMatch(allocation, total_usage_entries, user_usage_entries)
            )
        self._allocationquerymatches = [a for a in allocationquerymatch_objs if a]
        return self._allocationquerymatches

    def clean_collected_data(self):
        """clean data
        - flag any users not present in coldfront
        """
        # make master list of all users missing from ifx; don't record them yet,
        # only do that if they appear for our allocations.
        user_usernames = {d['username'] for d in self.sf_user_data}
        user_models, missing_usernames = id_present_missing_users(user_usernames)
        missing_username_list = [d['username'] for d in missing_usernames]
        logger.debug('allocation_usage:\n%s', self.sf_usage_data)

        # identify and remove allocation users that are no longer in the AD group
        for obj in self.allocationquerymatches:
            missing_unames_metadata = [
                {
                    'username': d['username'],
                    'volume': d.get('volume', None),
                    'path': d.get('path', None),
                }
                for d in obj.users_in_list(missing_username_list)
                if d['username'] not in username_ignore_list
            ]
            logger.warning('missing users detected: %s', missing_unames_metadata)
            for i in obj.users_in_list(missing_username_list):
                obj.user_usage_entries.remove(i)
        return self.allocationquerymatches, user_models

    def update_coldfront_objects(self, user_models):
        """update coldfront objects"""
        allocation_attribute_types = AllocationAttributeType.objects.all()

        quota_b_attrtype = allocation_attribute_types.get(name='Quota_In_Bytes')
        quota_tib_attrtype = allocation_attribute_types.get(name='Storage Quota (TiB)')
        quota_tb_attrtype = allocation_attribute_types.get(name='Storage Quota (TB)')
        # 3. iterate across allocations
        for obj in self.allocationquerymatches:
            logger.debug('Updating allocation. lab=%s volume=%s path=%s',
                obj.lab, obj.volume, obj.path
            )
            if obj.allocation.get_parent_resource.quantity_label == 'TB':
                quota_size_attrtype = quota_tb_attrtype
            elif obj.allocation.get_parent_resource.quantity_label == 'TiB':
                quota_size_attrtype = quota_tib_attrtype
            obj.update_usage_attr(quota_b_attrtype, obj.total_usage_entry['total_size'])
            obj.update_usage_attr(quota_size_attrtype, obj.total_usage_tib)

            logger.info('Allocation usage updated. allocation_pk=%s bytes=%s terabytes=%s',
                obj.allocation.pk, obj.total_usage_entry['total_size'], obj.total_usage_tib
            )
            # identify and remove allocation users that are no longer in the AD group
            self.zero_out_absent_allocationusers(obj.query_usernames, obj.allocation)

            for userdict in obj.user_usage_entries:
                user = next(
                    u for u in user_models if userdict['username'].lower() == u.username.lower()
                )
                logger.debug('entering for user: %s', user.username)
                usage_bytes = int(userdict['size_sum'])
                usage, unit = determine_size_fmt(userdict['size_sum'])

                obj.update_user_usage(user, usage_bytes, usage, unit)
                logger.debug('saving %s', userdict)

    def zero_out_absent_allocationusers(self, redash_usernames, allocation):
        """Change usage of AllocationUsers not in StarfishRedash usage stats to 0.
        """
        allocationusers_not_in_redash = allocation.allocationuser_set.exclude(
            user__username__in=redash_usernames
        )
        if allocationusers_not_in_redash:
            logger.info(
                'Users no longer in allocation. allocation_pk=%s users=%s',
                allocation.pk, [user.user.username for user in allocationusers_not_in_redash]
            )
            allocationusers_not_in_redash.update(usage=0, usage_bytes=0)


class RedashDataPipeline(UsageDataPipelineBase):
    """Collect data from Redash to update Coldfront Allocations."""

    def return_connection_obj(self):
        # 1. grab data from redash
        return StarFishRedash()

    def collect_sf_user_data(self):
        """Collect starfish data using the Redash API. Return the results."""
        user_usage = self.connection_obj.return_query_results(
            query='path_usage_query', volumes=self.volumes
        )
        for d in user_usage:
            d['username'] = d.pop('user_name')
            d['volume'] = d.pop('vol_name')
            d['path'] = d.pop('lab_path')
        return user_usage

    def collect_sf_usage_data(self):
        allocation_usage = self.connection_obj.return_query_results(
            query='subdirectory', volumes=self.volumes
        )
        for d in allocation_usage:
            d['username'] = d.pop('user_name')
            d['volume'] = d.pop('vol_name')
        return allocation_usage

    def collect_sf_data_for_lab(self, lab_name, volume_name, path):
        """Collect user-level and allocation-level usage data for a specific lab."""
        lab_allocation_data = [
            d for d in self.sf_usage_data
            if d['group_name'] == lab_name and d['volume'] == volume_name
        ]
        if len(lab_allocation_data) > 1:
            lab_allocation_data = [d for d in lab_allocation_data if d['path'] == path]
            if len(lab_allocation_data) == 0:
                raise ValueError(f"cannot identify correct allocation for {lab_name} on {volume_name}")

        try:
            lab_user_data = [d for d in self.sf_user_data if d['volume'] == volume_name and d['path'] == path]
        except IndexError:
            lab_user_data = []
        return lab_user_data, lab_allocation_data


class RESTDataPipeline(UsageDataPipelineBase):
    """Collect data from Starfish's REST API to update Coldfront Allocations."""

    def return_connection_obj(self):
        return StarFishServer()

    @record_process
    def produce_lab_dict(self):
        """Create dict of lab/volume combinations to collect and the volumes associated with them.
        Parameters
        ----------
        vol : string
            If not None, collect only allocations on the specified volume
        Returns
        -------
        labs_resources: dict
            Structured as follows:
            'lab_name': [('volume', 'path'), ('volume', 'path')]
        """
        pr_objs = self.allocations.only('id', 'project')
        labs_resources = {allocation.project.title: [] for allocation in pr_objs}
        for allocation in pr_objs:
            proj_name = allocation.project.title
            resource = allocation.get_parent_resource
            if resource:
                vol_name = get_resource_starfish_name(resource)
            else:
                message = f'no resource for allocation owned by {proj_name}'
                logger.error(message)
                continue
            if resource not in self.volumes:
                continue
            allocation_path = get_allocation_path(allocation)
            if allocation_path:
                labs_resources[proj_name].append((vol_name, allocation_path))
        return labs_resources

    def return_usage_query_data(self, usage_query):
        try:
            data = usage_query.result
            if not data:
                logger.warning('No starfish result for usage_query %s', usage_query)
        except ValueError as err:
            logger.warning('error with query: %s', err)
            data = None
        return data

    @property
    def items_to_pop(self):
        return ['size_sum_hum', 'rec_aggrs', 'physical_nlinks_size_sum',
            'physical_nlinks_size_sum_hum', 'volume_display_name', 'count', 'fn']

    def collect_sf_user_data(self):
        """Collect starfish data using the REST API. Return the results.

        Each lab's usage data is cached (see _lab_usage_cache above) so
        re-running this the same or next day reuses what was already
        collected instead of re-querying Starfish.
        """
        # 1. produce dict of all labs to be collected & volumes on which their data is located
        lab_res = self.produce_lab_dict()
        labs_resources = [(lab, res) for lab, resources in lab_res.items() for res in resources]

        # 2. serve whatever's already cached; collect the cache misses below
        collected_data = []
        to_collect = []
        for lab, (volume, lab_volpath) in labs_resources:
            cache_key = f'{lab}_{volume}_{lab_volpath}'
            cached = _lab_usage_cache.get(cache_key)
            if cached is None:
                to_collect.append((lab, volume, lab_volpath, cache_key))
                continue
            for user in cached['contents']:
                user.update({
                    'volume': cached['volume'],
                    'path': cached['path'],
                    'project': cached['project'],
                })
                collected_data.append(user)

        # 3. produce set of all volumes to be queried
        vol_set = {t[1] for t in to_collect}
        vols = [vol for vol in vol_set if vol in svp['volumes']]
        for volume in vols:
            projects = [t for t in to_collect if t[1] == volume]
            logger.debug('volume=%s to_collect_subset=%s', volume, projects)

            ### OLD METHOD ###
            for lab, _, lab_volpath, cache_key in projects:
                logger.debug('lab=%s volpath=%s', lab, lab_volpath)
                usage_query = self.connection_obj.create_query(
                    f'groupname={lab} type=f',
                    'volume,username,groupname',
                    f'{volume}:{lab_volpath}',
                )
                data = self.return_usage_query_data(usage_query)
                if data:
                    contents = [d for d in data if d['username'] != 'root']
                    for entry in contents:
                        # entry['size_sum'] = entry['rec_aggrs']['size']
                        # entry['full_path'] = entry['parent_path']+'/'+entry['fn']
                        for item in self.items_to_pop:
                            entry.pop(item, None)
                    record = {
                        'server': self.connection_obj.name,
                        'volume': volume,
                        'path': lab_volpath,
                        'project': lab,
                        'contents': contents,
                    }
                    _lab_usage_cache.set(cache_key, record, _LAB_USAGE_CACHE_SECONDS)
                    for user in contents:
                        user.update({'volume': volume, 'path': lab_volpath, 'project': lab})
                        collected_data.append(user)

        return collected_data

    def collect_sf_usage_data(self):
        """Collect usage data from starfish for all labs in the lab list."""
        # 1. produce dict of all labs to be collected & volumes on which their data is located
        lab_res = self.produce_lab_dict()
        lab_res = [(k, i[0], i[1]) for k, v in lab_res.items() for i in v]
        # 2. produce set of all volumes to be queried
        vol_set = {i[1] for i in lab_res}
        vols = [vol for vol in vol_set if vol in svp['volumes']]
        entries = []
        for volume in vols:
            volumepath = svp['volumes'][volume]
            projects = [t for t in lab_res if t[1] == volume]
            logger.debug('volume=%s to_collect_subset=%s', volume, projects)

            ### OLD METHOD ###
            for tup in projects:
                p = tup[0]
                lab_volpath = volumepath[0] if '_l3' not in p else volumepath[1]
                logger.debug('lab=%s volpath=%s', p, lab_volpath)
                usage_query = self.connection_obj.create_query(
                    f'groupname={p} type=d depth=1',
                    'volume,parent_path,groupname,rec_aggrs.size,fn',
                    f'{volume}:{lab_volpath}',
                    qformat='parent_path +aggrs.by_gid',
                )
                data = self.return_usage_query_data(usage_query)
                if data:
                    if len(data) > 1:
                        logger.error('Too many data entries. project=%s data=%s', p, data)
                        continue
                    entry = data[0]
                    entry.update({
                        'size_sum': entry['rec_aggrs']['size'],
                        'full_path': entry['parent_path']+'/'+entry['fn'],
                        'server': self.connection_obj.name,
                        'volume': volume,
                        'path': lab_volpath,
                        'project': p,
                        'date': DATESTR,
                    })
                    for item in self.items_to_pop:
                        entry.pop(item)
                    entries.append(entry)
        return entries
