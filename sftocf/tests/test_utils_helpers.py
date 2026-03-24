"""Unit tests for pure helpers and AllocationQueryMatch (mocked allocations)."""

from operator import itemgetter
from unittest.mock import Mock

from django.test import SimpleTestCase

from sftocf.utils import (
    AllocationQueryMatch,
    generate_headers,
    return_dict_of_groupings,
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
        alloc.path = '/lab/path'
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

