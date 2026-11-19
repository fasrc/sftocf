"""Unit tests for pure helpers and AllocationQueryMatch (mocked allocations)."""

import tempfile
from operator import itemgetter
from pathlib import Path
from unittest.mock import Mock

from django.test import SimpleTestCase

from sftocf.client import generate_headers
from sftocf.pipelines import AllocationQueryMatch, return_dict_of_groupings
from sftocf.utils import (
    determine_size_fmt,
    get_allocation_path,
    get_resource_starfish_name,
    read_json,
    save_json,
    uniques_and_intersection,
)


class GenerateHeadersTests(SimpleTestCase):
    def test_bearer_token(self):
        h = generate_headers('abc123')
        self.assertEqual(h['Authorization'], 'Bearer abc123')
        self.assertEqual(h['accept'], 'application/json')


class ReturnDictOfGroupingsTests(SimpleTestCase):
    def test_groups_by_key(self):
        rows = [
            {'path': 'a', 'volume': 'v1', 'n': 1},
            {'path': 'a', 'volume': 'v1', 'n': 2},
            {'path': 'b', 'volume': 'v1', 'n': 3},
        ]
        key = itemgetter('path', 'volume')
        grouped = return_dict_of_groupings(rows, key)
        self.assertEqual(len(grouped[('a', 'v1')]), 2)
        self.assertEqual(len(grouped[('b', 'v1')]), 1)


class AllocationQueryMatchNewTests(SimpleTestCase):
    def _make_allocation_mock(self):
        alloc = Mock()
        alloc.pk = 42
        alloc.allocationattribute_set.filter.return_value.first.return_value.value = '/lab/path'
        alloc.project.title = 'test_lab'
        alloc.resources.first.return_value = Mock()
        alloc.resources.first.return_value.name = 'holylfs10/tier1'
        alloc.get_parent_resource.name = 'holylfs10/tier1'
        return alloc

    def test_returns_none_when_no_total_usage(self):
        alloc = self._make_allocation_mock()
        self.assertIsNone(AllocationQueryMatch(alloc, None, []))
        self.assertIsNone(AllocationQueryMatch(alloc, [], []))

    def test_returns_none_when_multiple_total_rows(self):
        alloc = self._make_allocation_mock()
        totals = [
            {'total_size': 100, 'path': '/lab/path', 'volume': 'holylfs10'},
            {'total_size': 200, 'path': '/lab/path', 'volume': 'holylfs10'},
        ]
        self.assertIsNone(AllocationQueryMatch(alloc, totals, []))

    def test_constructed_when_single_total(self):
        alloc = self._make_allocation_mock()
        totals = [{'total_size': 1099511627776}]  # 1 TiB in bytes
        users = [{'username': 'alice', 'size_sum': 100}]
        m = AllocationQueryMatch(alloc, totals, users)
        self.assertIsNotNone(m)
        self.assertEqual(m.total_usage_entry, totals[0])
        self.assertEqual(m.lab, 'test_lab')
        self.assertAlmostEqual(m.total_usage_tib, 1.0, places=3)

    def test_query_usernames(self):
        alloc = self._make_allocation_mock()
        totals = [{'total_size': 100}]
        users = [{'username': 'a'}, {'username': 'b'}]
        m = AllocationQueryMatch(alloc, totals, users)
        self.assertEqual(m.query_usernames, ['a', 'b'])

    def test_users_in_list(self):
        alloc = self._make_allocation_mock()
        totals = [{'total_size': 100}]
        users = [{'username': 'a'}, {'username': 'b'}]
        m = AllocationQueryMatch(alloc, totals, users)
        self.assertEqual(len(m.users_in_list(['a'])), 1)


class GetAllocationPathTests(SimpleTestCase):
    def test_returns_value_when_subdirectory_attribute_present(self):
        alloc = Mock()
        alloc.allocationattribute_set.filter.return_value.first.return_value.value = '/lab/path'
        self.assertEqual(get_allocation_path(alloc), '/lab/path')
        alloc.allocationattribute_set.filter.assert_called_once_with(
            allocation_attribute_type__name='Subdirectory'
        )

    def test_returns_empty_string_when_no_subdirectory_attribute(self):
        alloc = Mock()
        alloc.allocationattribute_set.filter.return_value.first.return_value = None
        self.assertEqual(get_allocation_path(alloc), '')


class UniquesAndIntersectionTests(SimpleTestCase):
    def test_splits_into_three_groups(self):
        only1, both, only2 = uniques_and_intersection(['a', 'b', 'c'], ['b', 'c', 'd'])
        self.assertEqual(set(only1), {'a'})
        self.assertEqual(set(both), {'b', 'c'})
        self.assertEqual(set(only2), {'d'})

    def test_no_overlap(self):
        only1, both, only2 = uniques_and_intersection(['a'], ['b'])
        self.assertEqual(only1, ['a'])
        self.assertEqual(both, [])
        self.assertEqual(only2, ['b'])


class DetermineSizeFmtTests(SimpleTestCase):
    def test_binary_bytes_stays_in_bytes(self):
        self.assertEqual(determine_size_fmt(512), (512, 'B'))

    def test_binary_crosses_into_tib(self):
        value, unit = determine_size_fmt(1099511627776)  # 1 TiB
        self.assertEqual(unit, 'TiB')
        self.assertAlmostEqual(value, 1.0)

    def test_decimal_system_uses_1000_divisor(self):
        value, unit = determine_size_fmt(1_000_000, system='decimal')
        self.assertEqual(unit, 'MB')
        self.assertAlmostEqual(value, 1.0)

    def test_invalid_system_raises(self):
        with self.assertRaises(Exception):
            determine_size_fmt(100, system='nonsense')


class JsonRoundTripTests(SimpleTestCase):
    def test_save_then_read_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'data.json'
            save_json(str(path), {'a': 1, 'b': [1, 2, 3]})
            self.assertEqual(read_json(str(path)), {'a': 1, 'b': [1, 2, 3]})


class GetResourceStarfishNameTests(SimpleTestCase):
    def test_uses_starfish_name_attribute_when_set(self):
        resource = Mock()
        resource.get_attribute.return_value = 'starfish_vol1'
        resource.name = 'holylfs10/tier1'
        self.assertEqual(get_resource_starfish_name(resource), 'starfish_vol1')

    def test_falls_back_to_resource_name_when_unset(self):
        resource = Mock()
        resource.get_attribute.return_value = None
        resource.name = 'holylfs10/tier1'
        self.assertEqual(get_resource_starfish_name(resource), 'holylfs10/tier1')

