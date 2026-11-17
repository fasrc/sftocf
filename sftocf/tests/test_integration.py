"""Database-backed tests (require full ColdFront project + test DB)."""

from pathlib import Path

from django.test import TestCase, override_settings

from django.core.management import call_command

from coldfront.core.resource.models import ResourceAttributeType
from coldfront.core.test_helpers.factories import (
    AAttributeTypeFactory,
    AllocationAttributeFactory,
    AllocationAttributeTypeFactory,
    AllocationFactory,
    AllocationUserStatusChoiceFactory,
    ProjectFactory,
    ResourceFactory,
    UserFactory,
)
from sftocf.utils import AllocationQueryMatch, get_allocation_path

_FIXTURE_JSON_DIR = Path(__file__).resolve().parent.parent / 'fixture_data'


@override_settings(SF_VOLUME_MAPPING='{}')
class AllocationQueryMatchDBTests(TestCase):
    """AllocationQueryMatch with real Allocation models from factory setup.

    Built from the shared ColdFront test factories only (no
    coldfront.core.test_helpers.factories.setup_models or fixture files),
    since those are specific to the fasrc/coldfront fork and aren't present
    on upstream coldfront/coldfront.
    """

    @classmethod
    def setUpTestData(cls):
        pi = UserFactory(username='sdpoisson')
        project = ProjectFactory(pi=pi, title='poisson_lab')
        resource = ResourceFactory(name='holylfs10/tier1', resource_type__name='Storage')
        cls.storage_allocation = AllocationFactory(project=project, justification='storage test')
        cls.storage_allocation.resources.add(resource)
        subdirectory_type = AllocationAttributeTypeFactory(
            name='Subdirectory', attribute_type=AAttributeTypeFactory(name='Text'),
        )
        AllocationAttributeFactory(
            allocation=cls.storage_allocation,
            allocation_attribute_type=subdirectory_type,
            value='C/LABS/poisson_lab',
        )

    def test_storage_allocation_query_match_total_tib(self):
        alloc = self.storage_allocation
        one_tib = 1099511627776
        totals = [{'total_size': one_tib}]
        users = [{'username': 'sdpoisson', 'size_sum': 100}]
        match = AllocationQueryMatch(alloc, totals, users)
        self.assertIsNotNone(match)
        self.assertAlmostEqual(match.total_usage_tib, 1.0, places=3)
        self.assertEqual(match.lab, 'poisson_lab')

    def test_update_user_usage_creates_then_updates_allocation_user(self):
        AllocationUserStatusChoiceFactory(name='Active')
        target_user = UserFactory(username='ljbortkiewicz')
        totals = [{'total_size': 100}]
        users = [{'username': target_user.username, 'size_sum': 50}]
        match = AllocationQueryMatch(self.storage_allocation, totals, users)

        allocationuser = match.update_user_usage(
            target_user, usage_bytes=500, usage=0.5, unit='GB'
        )
        self.assertEqual(allocationuser.usage_bytes, 500)
        self.assertEqual(allocationuser.usage, 0.5)
        self.assertEqual(allocationuser.unit, 'GB')
        self.assertEqual(allocationuser.status.name, 'Active')

        # calling again updates the same row rather than duplicating it
        updated = match.update_user_usage(target_user, usage_bytes=900, usage=0.9, unit='GB')
        self.assertEqual(updated.pk, allocationuser.pk)
        self.assertEqual(updated.usage_bytes, 900)
        self.assertEqual(self.storage_allocation.allocationuser_set.count(), 1)

    def test_update_usage_attr_creates_then_updates_usage_value(self):
        quota_attr_type = AllocationAttributeTypeFactory(
            name='Quota_In_Bytes',
            attribute_type=AAttributeTypeFactory(name='Int'),
            has_usage=True,
        )
        totals = [{'total_size': 100}]
        users = [{'username': 'sdpoisson', 'size_sum': 10}]
        match = AllocationQueryMatch(self.storage_allocation, totals, users)

        usage = match.update_usage_attr(quota_attr_type, 12345)
        self.assertEqual(usage.value, 12345)

        updated = match.update_usage_attr(quota_attr_type, 54321)
        self.assertEqual(updated.pk, usage.pk)
        self.assertEqual(updated.value, 54321)
        self.assertEqual(
            self.storage_allocation.allocationattribute_set.filter(
                allocation_attribute_type=quota_attr_type
            ).count(),
            1,
        )


class AllocationPathDBTests(TestCase):
    """get_allocation_path against real Allocation/AllocationAttribute rows
    (complements the mocked unit tests in test_utils_helpers.py)."""

    @classmethod
    def setUpTestData(cls):
        pi = UserFactory(username='path_pi')
        project = ProjectFactory(pi=pi, title='path_lab')
        cls.allocation = AllocationFactory(project=project, justification='path test')

    def test_returns_empty_string_when_no_subdirectory_attribute(self):
        self.assertEqual(get_allocation_path(self.allocation), '')

    def test_returns_value_when_subdirectory_attribute_exists(self):
        subdirectory_type = AllocationAttributeTypeFactory(
            name='Subdirectory', attribute_type=AAttributeTypeFactory(name='Text'),
        )
        AllocationAttributeFactory(
            allocation=self.allocation,
            allocation_attribute_type=subdirectory_type,
            value='C/LABS/path_lab',
        )
        self.assertEqual(get_allocation_path(self.allocation), 'C/LABS/path_lab')


class SftocfSetupCommandTests(TestCase):
    """The sftocf_setup management command should be safe to run repeatedly."""

    def test_creates_starfish_name_resource_attribute_type_idempotently(self):
        call_command('sftocf_setup')
        self.assertTrue(ResourceAttributeType.objects.filter(name='starfish_name').exists())

        call_command('sftocf_setup')  # run again
        self.assertEqual(
            ResourceAttributeType.objects.filter(name='starfish_name').count(), 1
        )


class FixtureJsonTests(TestCase):
    """Lightweight check that bundled JSON fixtures are readable."""

    def test_sample_fixtures_exist(self):
        for name in ('poisson_lab_holysfdb10.json', 'gordon_lab_holysfdb10.json'):
            path = _FIXTURE_JSON_DIR / name
            self.assertTrue(path.is_file(), msg=f'missing {path}')
