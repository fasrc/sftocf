"""Database-backed tests (require full ColdFront project + test DB)."""

from pathlib import Path

from django.test import TestCase, override_settings

from coldfront.core.test_helpers.factories import setup_models
from sftocf.utils import AllocationQueryMatch

UTIL_FIXTURES = [
    'coldfront/core/test_helpers/test_data/test_fixtures/ifx.json',
]

_FIXTURE_JSON_DIR = Path(__file__).resolve().parent.parent / 'fixture_data'


@override_settings(SF_VOLUME_MAPPING='{}')
class AllocationQueryMatchDBTests(TestCase):
    """AllocationQueryMatch with real Allocation models from factory setup."""

    fixtures = UTIL_FIXTURES

    @classmethod
    def setUpTestData(cls):
        setup_models(cls)

    def test_storage_allocation_query_match_total_tib(self):
        alloc = self.storage_allocation
        alloc.path = 'C/LABS/poisson_lab'
        alloc.save(update_fields=['path'])
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
