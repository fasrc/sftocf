from django.core import management

def pull_sf_push_cf(volume=None, pulltype='redash'):
    management.call_command('pull_sf_push_cf', volume=volume, pulltype=pulltype)

def update_zones(dry_run=False):
    management.call_command('update_zones', dry_run=dry_run)

def import_allocation_filepaths():
    management.call_command('import_allocation_filepaths')
