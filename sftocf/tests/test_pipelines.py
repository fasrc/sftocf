"""Unit tests for RESTDataPipeline's lab-usage cache."""
import tempfile
from unittest.mock import Mock, PropertyMock, patch

from django.core.cache.backends.filebased import FileBasedCache
from django.test import SimpleTestCase

from sftocf import pipelines


def _make_pipeline():
    """A RESTDataPipeline with __init__ (which makes real network calls
    and would itself call collect_sf_user_data) skipped."""
    pipeline = object.__new__(pipelines.RESTDataPipeline)
    pipeline.connection_obj = Mock()
    pipeline.connection_obj.name = 'https://sf.example.com'
    pipeline.volumes = ['vol1']
    return pipeline


class CollectSfUserDataCacheTests(SimpleTestCase):
    """collect_sf_user_data caches each lab's usage data for a couple of
    days so a later call for the same lab/volume/path doesn't re-query
    Starfish, replacing the old ad hoc JSON-file-plus-date-string cache."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_patch = patch.object(
            pipelines, '_lab_usage_cache', FileBasedCache(self._tmpdir.name, {})
        )
        self._cache_patch.start()
        # collect_sf_user_data only re-queries volumes present in svp['volumes'];
        # a cache miss falls through to that check, so it needs 'vol1' in it.
        self._svp_patch = patch.object(pipelines, 'svp', {'volumes': {'vol1': ('/lab',)}})
        self._svp_patch.start()

    def tearDown(self):
        self._svp_patch.stop()
        self._cache_patch.stop()
        self._tmpdir.cleanup()

    def test_cache_miss_queries_api_and_populates_cache(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={'lab_a': [('vol1', '/lab/a')]})
        pipeline.return_usage_query_data = Mock(
            return_value=[{'username': 'alice', 'size_sum': 100}]
        )

        data = pipeline.collect_sf_user_data()

        self.assertEqual(data, [{
            'username': 'alice', 'size_sum': 100,
            'volume': 'vol1', 'path': '/lab/a', 'project': 'lab_a',
        }])
        pipeline.connection_obj.create_query.assert_called_once()
        # return_usage_query_data takes the AsyncQuery object itself (it
        # accesses .result internally), not the already-resolved .result.
        pipeline.return_usage_query_data.assert_called_once_with(
            pipeline.connection_obj.create_query.return_value
        )
        # the cache stores the raw per-user records as returned by the API,
        # before collect_sf_user_data enriches them with volume/path/project
        cached = pipelines._lab_usage_cache.get('lab_a_vol1_/lab/a')
        self.assertEqual(cached['contents'], [{'username': 'alice', 'size_sum': 100}])

    def test_cache_hit_skips_api_query(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={'lab_a': [('vol1', '/lab/a')]})
        pipelines._lab_usage_cache.set('lab_a_vol1_/lab/a', {
            'server': 'https://sf.example.com',
            'volume': 'vol1',
            'path': '/lab/a',
            'project': 'lab_a',
            'contents': [{'username': 'alice', 'size_sum': 100}],
        })

        data = pipeline.collect_sf_user_data()

        self.assertEqual(data, [{
            'username': 'alice', 'size_sum': 100,
            'volume': 'vol1', 'path': '/lab/a', 'project': 'lab_a',
        }])
        pipeline.connection_obj.create_query.assert_not_called()

    def test_expired_cache_entry_triggers_requery(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={'lab_a': [('vol1', '/lab/a')]})
        pipeline.return_usage_query_data = Mock(
            return_value=[{'username': 'bob', 'size_sum': 50}]
        )
        pipelines._lab_usage_cache.set('lab_a_vol1_/lab/a', {
            'server': 'https://sf.example.com',
            'volume': 'vol1',
            'path': '/lab/a',
            'project': 'lab_a',
            'contents': [{'username': 'alice', 'size_sum': 100}],
        }, timeout=0)  # already expired by the time collect_sf_user_data runs

        data = pipeline.collect_sf_user_data()

        self.assertEqual(data, [{
            'username': 'bob', 'size_sum': 50,
            'volume': 'vol1', 'path': '/lab/a', 'project': 'lab_a',
        }])
        pipeline.connection_obj.create_query.assert_called_once()

    def test_no_data_from_api_is_not_cached(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={'lab_a': [('vol1', '/lab/a')]})
        pipeline.return_usage_query_data = Mock(return_value=None)

        data = pipeline.collect_sf_user_data()

        self.assertEqual(data, [])
        self.assertIsNone(pipelines._lab_usage_cache.get('lab_a_vol1_/lab/a'))


class LabVolumePathsTests(SimpleTestCase):
    """_lab_volume_paths() is the piece collect_sf_user_data and
    collect_sf_usage_data share: flatten produce_lab_dict() and filter to
    the volumes configured in SF_VOLUME_MAPPING."""

    def setUp(self):
        self._svp_patch = patch.object(pipelines, 'svp', {'volumes': {'vol1': ('/lab',)}})
        self._svp_patch.start()

    def tearDown(self):
        self._svp_patch.stop()

    def test_flattens_and_filters_to_configured_volumes(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={
            'lab_a': [('vol1', '/lab/a')],
            'lab_b': [('vol2', '/lab/b')],  # vol2 isn't in svp['volumes']
        })

        triples, vols = pipeline._lab_volume_paths()

        self.assertEqual(set(triples), {('lab_a', 'vol1', '/lab/a'), ('lab_b', 'vol2', '/lab/b')})
        self.assertEqual(vols, ['vol1'])


class CollectSfUsageDataTests(SimpleTestCase):
    """collect_sf_usage_data builds one aggregate usage entry per lab from
    the REST API's directory-query results."""

    def setUp(self):
        self._svp_patch = patch.object(pipelines, 'svp', {'volumes': {'vol1': ('/lab',)}})
        self._svp_patch.start()

    def tearDown(self):
        self._svp_patch.stop()

    def test_builds_entry_from_query_result(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={'lab_a': [('vol1', '/lab/a')]})
        pipeline.return_usage_query_data = Mock(return_value=[{
            'rec_aggrs': {'size': 12345},
            'parent_path': '/some/parent',
            'fn': 'lab_a',
            'size_sum_hum': '12K', 'physical_nlinks_size_sum': 1,
            'physical_nlinks_size_sum_hum': '1', 'volume_display_name': 'v', 'count': 1,
        }])

        entries = pipeline.collect_sf_usage_data()

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry['size_sum'], 12345)
        self.assertEqual(entry['full_path'], '/some/parent/lab_a')
        self.assertEqual(entry['volume'], 'vol1')
        self.assertEqual(entry['project'], 'lab_a')
        for stripped in pipeline.items_to_pop:
            self.assertNotIn(stripped, entry)

    def test_multiple_results_for_one_lab_are_skipped(self):
        pipeline = _make_pipeline()
        pipeline.produce_lab_dict = Mock(return_value={'lab_a': [('vol1', '/lab/a')]})
        pipeline.return_usage_query_data = Mock(return_value=[{'fn': 'a'}, {'fn': 'b'}])

        self.assertEqual(pipeline.collect_sf_usage_data(), [])


class ReturnUsageQueryDataTests(SimpleTestCase):
    """return_usage_query_data takes the AsyncQuery-like object itself, not
    its already-resolved .result: it accesses .result internally and needs
    to catch the ValueError that property can raise. Passing an
    already-resolved list/dict here would raise AttributeError instead,
    which is the bug this fixes."""

    def test_returns_the_resolved_result(self):
        pipeline = _make_pipeline()
        usage_query = Mock(result=[{'username': 'alice'}])
        self.assertEqual(
            pipeline.return_usage_query_data(usage_query), [{'username': 'alice'}]
        )

    def test_returns_none_and_logs_on_value_error(self):
        pipeline = _make_pipeline()
        usage_query = Mock()
        type(usage_query).result = PropertyMock(side_effect=ValueError('bad query'))
        self.assertIsNone(pipeline.return_usage_query_data(usage_query))

    def test_returns_none_and_logs_on_timeout(self):
        pipeline = _make_pipeline()
        usage_query = Mock()
        type(usage_query).result = PropertyMock(side_effect=TimeoutError('query took too long'))
        self.assertIsNone(pipeline.return_usage_query_data(usage_query))
