"""signals for sftocf plugin"""
import logging

import django.dispatch
from django.dispatch import receiver
from coldfront.core.allocation.signals import allocation_activate

from sftocf.utils import update_allocation_usage_data

logger = logging.getLogger(__name__)

starfish_add_aduser = django.dispatch.Signal()
starfish_remove_aduser = django.dispatch.Signal()
starfish_add_adgroup = django.dispatch.Signal()

@receiver(allocation_activate)
def update_allocation(sender, **kwargs):
    '''update the allocation data when the allocation is activated.'''
    logger.debug('allocation_activate signal received')
    allocation_pk = kwargs['allocation_pk']
    try:
        update_allocation_usage_data(allocation_pk)
    except Exception as e:
        logger.error(f'Error updating allocation usage data for allocation {allocation_pk}: {e}')
        raise
