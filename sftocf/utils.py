import logging

from django.contrib.auth import get_user_model

from sftocf.conf import username_ignore_list

logger = logging.getLogger(__name__)


def return_unit_system(system):
    if system == 'binary':
        units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB']
        divisor = 1024.0
    elif system == 'decimal':
        units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB']
        divisor = 1000.0
    else:
        raise Exception('invalid system')
    return units, divisor

def determine_size_fmt(byte_num, system='binary'):
    """Return the correct human-readable byte measurement.
    """
    units, divisor = return_unit_system(system)
    for u in units:
        unit = u
        if abs(byte_num) < divisor:
            return (round(byte_num, 3), unit)
        byte_num /= divisor
    return (round(byte_num, 3), unit)

def id_present_missing_users(username_list):
    """
    Collect all User entries with usernames in param username_list; return tuple
    of all matching User entries and all usernames with no User entries.
    """
    present_users = get_user_model().objects.filter(username__in=username_list)
    present_usernames = list(present_users.values_list('username', flat=True))
    missing_usernames = [
        {'username': n} for n in username_list
        if n not in present_usernames and n not in username_ignore_list
    ]
    return (present_users, missing_usernames)

def uniques_and_intersection(list1, list2):
    """Vendored from coldfront.core.utils.common, which isn't present on
    every ColdFront fork this plugin targets."""
    intersection = list(set(list1) & set(list2))
    list1_unique = list(set(list1) - set(list2))
    list2_unique = list(set(list2) - set(list1))
    return (list1_unique, intersection, list2_unique)

def get_allocation_path(allocation):
    """Return an allocation's filesystem subdirectory.

    Some ColdFront forks (e.g. fasrc/coldfront) expose this as an
    Allocation.path property; upstream coldfront/coldfront does not, so
    look up the 'Subdirectory' AllocationAttribute directly instead of
    relying on that property being present.
    """
    attr = allocation.allocationattribute_set.filter(
        allocation_attribute_type__name='Subdirectory'
    ).first()
    return attr.value if attr else ''

def record_process(func):
    """Wrapper function for logging"""
    def call(*args, **kwargs):
        funcdata = '{} {}'.format(func.__name__, func.__code__.co_firstlineno)
        logger.debug('\n%s START.', funcdata)
        result = func(*args, **kwargs)
        logger.debug('%s END. output:\n%s\n', funcdata, result)
        return result
    return call

def get_resource_starfish_name(resource):
    """Return the Starfish volume name associated with a Resource.

    Uses the resource's starfish_name ResourceAttribute if one is set,
    otherwise falls back to the resource's name.
    """
    starfish_name = resource.get_attribute('starfish_name', expand=False, typed=False)
    if starfish_name:
        return starfish_name
    return resource.name
