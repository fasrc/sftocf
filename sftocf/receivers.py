"""Django signal receivers for the sftocf plugin."""
import logging

from django.contrib.auth import get_user_model
from django.core.cache.backends.filebased import FileBasedCache
from django.dispatch import receiver

from coldfront.core.allocation.signals import allocation_activate
from coldfront.core.allocation.models import AllocationAttributeType
from coldfront.core.resource.models import ResourceAttributeType

from sftocf.client import StarfishClient, StarFishRedash
from sftocf.conf import DATAPATH
from sftocf.pipelines import AllocationQueryMatch, RedashDataPipeline
from sftocf.signals import starfish_set_zone_userparam, starfish_update_volume_information
from sftocf.utils import determine_size_fmt, get_allocation_path, get_resource_starfish_name

logger = logging.getLogger(__name__)

_volume_stats_cache = FileBasedCache(DATAPATH, {})
_VOLUME_STATS_CACHE_SECONDS = 60 * 10


def _get_volume_stats(source):
    """Return {volume_name: {resource_attribute_name: value}}, from cache if fresh."""
    cache_key = f'sftocf_volume_stats_{source}'
    volumes = _volume_stats_cache.get(cache_key)
    if volumes is not None:
        return volumes
    if source == 'rest_api':
        raw_volumes = StarfishClient().get_volume_attributes()
        volumes = {
            v['vol']: {
                'capacity_tb': v['total_capacity'] / (1024 ** 4),
                'free_tb': v['free_space'] / (1024 ** 4),
                'used_tb': (v['total_capacity'] - v['free_space']) / (1024 ** 4),
                'file_count': v.get('number_of_files'),
            } for v in raw_volumes
        }
    elif source == 'redash':
        raw_volumes = StarFishRedash().get_vol_stats()
        volumes = {
            v['vol']: {
                'capacity_tb': v['capacity_TB'],
                'free_tb': v['free_TB'],
                'used_tb': v['used_physical_TB'],
                'file_count': v['number_of_files'],
            } for v in raw_volumes
        }
    else:
        raise ValueError(f'unknown Starfish data source {source!r}')
    _volume_stats_cache.set(cache_key, volumes, _VOLUME_STATS_CACHE_SECONDS)
    return volumes


@receiver(starfish_set_zone_userparam)
def update_starfish_zone_params(sender, **kwargs):
    """Update a zone's zone_user_params.

    Takes as kwargs the zone_id, the user_param name, and the user_param value
    """
    zone_id = kwargs['zone_id']
    param_name = kwargs['param_name']
    param_value = kwargs['param_value']

    server = StarfishClient()
    response = server.set_zone_user_param(zone_id, param_name, param_value)
    return response


@receiver(starfish_update_volume_information)
def update_starfish_volume_information(sender, **kwargs):
    """Update a Resource's capacity/usage ResourceAttributes from Starfish.
    """
    resource = kwargs['resource']
    source = kwargs.get('source') or 'redash'
    volume_name = get_resource_starfish_name(resource)
    attrs = _get_volume_stats(source).get(volume_name)
    if not attrs:
        logger.debug(
            'no Starfish data found for resource; skipping. resource=%s volume=%s source=%s',
            resource.name, volume_name, source
        )
        return
    for attr_name, attr_val in attrs.items():
        if attr_val:
            attr_type_obj = ResourceAttributeType.objects.get(name=attr_name)
            resource.resourceattribute_set.update_or_create(
                resource_attribute_type=attr_type_obj,
                defaults={'value': attr_val}
            )
    return 'sftocf'


@receiver(allocation_activate)
def update_allocation(sender, **kwargs):
    '''update the allocation data when the allocation is activated.'''
    logger.debug('allocation_activate signal received')
    allocation = kwargs['allocation_obj']
    volume_name = get_resource_starfish_name(allocation.resources.first())
    server = StarfishClient()
    if volume_name not in server.volumes:
        logger.warning(
            'Allocation not in Starfish volumes; skipping allocation update. allocation_pk=%s volume=%s',
            allocation.pk, volume_name
        )
        return
    sf_redash_data = RedashDataPipeline(volume=volume_name)
    user_data, allocation_data = sf_redash_data.collect_sf_data_for_lab(
        allocation.project.title, volume_name, get_allocation_path(allocation)
    )
    if not allocation_data:
        logger.warning(
            'No matching allocation found for the given data. project=%s volume=%s',
            allocation.project.title, volume_name
        )
        return

    subdir_type = AllocationAttributeType.objects.get(name='Subdirectory')
    allocation.allocationattribute_set.get_or_create(
        allocation_attribute_type_id=subdir_type.pk,
        value=allocation_data[0]['path']
    )

    allocation_query_match = AllocationQueryMatch(allocation, allocation_data, user_data)

    quota_b_attrtype = AllocationAttributeType.objects.get(name='Quota_In_Bytes')
    quota_size_attrtype = AllocationAttributeType.objects.get(
        name=f'Storage Quota ({allocation.unit_label})'
    )

    allocation_query_match.update_usage_attr(
            quota_b_attrtype, allocation_query_match.total_usage_entry['total_size'])
    allocation_query_match.update_usage_attr(
            quota_size_attrtype, allocation_query_match.total_usage_tib)
    missing_users = []
    for userdict in allocation_query_match.user_usage_entries:
        try:
            user = get_user_model().objects.get(username=userdict['username'])
        except get_user_model().DoesNotExist:
            missing_users.append({
                'username': userdict['username'],
                'volume': userdict.get('volume', None),
                'path': userdict.get('path', None)
            })
            continue
        usage_bytes = int(userdict['size_sum'])
        usage, unit = determine_size_fmt(userdict['size_sum'])
        allocation_query_match.update_user_usage(user, usage_bytes, usage, unit)
    logger.info(
        'Allocation updated with usage data. allocation_pk=%s volume=%s bytes=%s terabytes=%s',
        allocation.pk, volume_name, allocation_query_match.total_usage_entry['total_size'],
        allocation_query_match.total_usage_tib
    )
    if missing_users:
        logger.warning('missing users detected: %s', missing_users)
