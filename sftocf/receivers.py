"""Django signal receivers for the sftocf plugin."""
import logging

from django.contrib.auth import get_user_model
from django.dispatch import receiver

from coldfront.core.allocation.signals import allocation_activate
from coldfront.core.allocation.models import AllocationAttributeType

from sftocf.client import StarfishClient
from sftocf.pipelines import AllocationQueryMatch, RedashDataPipeline
from sftocf.utils import determine_size_fmt, get_allocation_path, get_resource_starfish_name

logger = logging.getLogger(__name__)


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
