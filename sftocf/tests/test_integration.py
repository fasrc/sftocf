"""Database-backed tests (require full ColdFront project + test DB)."""

from pathlib import Path

from django.test import TestCase, override_settings

from coldfront.core.test_helpers.factories import (
    AAttributeTypeFactory,
    AllocationAttributeFactory,
    AllocationAttributeTypeFactory,
    AllocationFactory,
    ProjectFactory,
    ResourceFactory,
    UserFactory,
)
from sftocf.utils import AllocationQueryMatch

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


class FixtureJsonTests(TestCase):
    """Lightweight check that bundled JSON fixtures are readable."""

    def test_sample_fixtures_exist(self):
        for name in ('poisson_lab_holysfdb10.json', 'gordon_lab_holysfdb10.json'):
            path = _FIXTURE_JSON_DIR / name
            self.assertTrue(path.is_file(), msg=f'missing {path}')
