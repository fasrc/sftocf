"""StarFishServer tests with HTTP/API calls mocked."""

from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase, override_settings

from sftocf.utils import AsyncQuery, StarFishServer


def _make_server():
    """A StarFishServer with auth/volume discovery short-circuited."""
    with patch('sftocf.utils.StarFishServer.get_volume_names', return_value=[]), \
         patch('sftocf.utils.StarFishServer.get_auth_token', return_value='tok'):
        return StarFishServer('https://sf.example.com')


@override_settings(SFUSER='user', SFPASS='secret')
class StarFishServerInitTests(SimpleTestCase):
    """Avoid real network; patch auth and volume discovery."""

    @patch('sftocf.utils.StarFishServer.get_volume_names', return_value=['vol1', 'vol2'])
    @patch('sftocf.utils.StarFishServer.get_auth_token', return_value='tok')
    def test_init_sets_api_url_and_volumes(self, _mock_token, _mock_vols):
        sf = StarFishServer('https://starfish.example.com')
        self.assertEqual(sf.name, 'https://starfish.example.com')
        self.assertEqual(sf.api_url, 'https://starfish.example.com/api/')
        self.assertEqual(sf.token, 'tok')
        self.assertEqual(sf.headers['Authorization'], 'Bearer tok')
        self.assertEqual(sf.volumes, ['vol1', 'vol2'])

    @patch('sftocf.utils.requests.post')
    @patch('sftocf.utils.StarFishServer.get_volume_names', return_value=[])
    def test_get_auth_token_posts_to_auth_endpoint(self, _mock_vols, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'token': 'mytoken'}
        mock_post.return_value = mock_resp

        sf = StarFishServer('https://sf.example.com')
        self.assertEqual(sf.token, 'mytoken')
        mock_post.assert_called_once()
        call_kw = mock_post.call_args
        self.assertIn('/api/auth/', call_kw[0][0])


class ZoneLookupTests(SimpleTestCase):
    @patch('sftocf.utils.StarFishServer.get_zones')
    def test_get_zone_by_name_found(self, mock_get_zones):
        mock_get_zones.return_value = [{'name': 'lab_a', 'id': 1}, {'name': 'lab_b', 'id': 2}]
        sf = _make_server()
        self.assertEqual(sf.get_zone_by_name('lab_b'), {'name': 'lab_b', 'id': 2})

    @patch('sftocf.utils.StarFishServer.get_zones')
    def test_get_zone_by_name_not_found(self, mock_get_zones):
        mock_get_zones.return_value = [{'name': 'lab_a', 'id': 1}]
        sf = _make_server()
        self.assertIsNone(sf.get_zone_by_name('missing'))


class CreateZoneTests(SimpleTestCase):
    @patch('sftocf.utils.StarFishServer.update_zone_paths')
    @patch('sftocf.utils.return_post_json')
    def test_posts_expected_payload_and_sets_paths(self, mock_post_json, mock_update_paths):
        mock_post_json.return_value = {'id': 99, 'name': 'newzone'}
        sf = _make_server()
        response = sf.create_zone('newzone', ['vol1:/a'], [{'username': 'pi'}], [])
        self.assertEqual(response, {'id': 99, 'name': 'newzone'})
        posted_data = mock_post_json.call_args.kwargs['data']
        self.assertEqual(posted_data['name'], 'newzone')
        self.assertEqual(posted_data['members']['users'], [{'username': 'pi'}])
        mock_update_paths.assert_called_once_with(99, ['vol1:/a'])

    @patch('sftocf.utils.StarFishServer.update_zone_paths')
    @patch('sftocf.utils.return_post_json')
    def test_skips_path_update_when_no_paths(self, mock_post_json, mock_update_paths):
        mock_post_json.return_value = {'id': 5, 'name': 'emptyzone'}
        sf = _make_server()
        sf.create_zone('emptyzone', [], [], [])
        mock_update_paths.assert_not_called()


class DeleteZoneTests(SimpleTestCase):
    @patch('sftocf.utils.requests.delete')
    def test_delete_zone_by_id_success(self, mock_delete):
        mock_delete.return_value = Mock(status_code=204)
        sf = _make_server()
        sf.delete_zone(zone_id=7)
        mock_delete.assert_called_once()
        self.assertIn('v2/zones/7', mock_delete.call_args[0][0])

    @patch('sftocf.utils.requests.delete')
    def test_delete_zone_raises_on_non_204(self, mock_delete):
        mock_delete.return_value = Mock(status_code=400, text='bad request')
        sf = _make_server()
        with self.assertRaises(ValueError):
            sf.delete_zone(zone_id=7)

    @patch('sftocf.utils.requests.delete')
    @patch('sftocf.utils.StarFishServer.get_zone_by_name')
    def test_delete_zone_looks_up_id_by_name_when_not_given(self, mock_lookup, mock_delete):
        mock_lookup.return_value = {'id': 42}
        mock_delete.return_value = Mock(status_code=204)
        sf = _make_server()
        sf.delete_zone(zone_id=None, zone_name='somelab')
        mock_lookup.assert_called_once_with('somelab')
        self.assertIn('v2/zones/42', mock_delete.call_args[0][0])


class UpdateZonePathsRetryTests(SimpleTestCase):
    """update_zone_paths tries a bulk 'set' first; on failure it reconciles
    by diffing against the zone's existing paths (removing stale ones, then
    adding new ones one at a time) rather than failing outright."""

    @patch('sftocf.utils.StarFishServer.get_zone_paths')
    @patch('sftocf.utils.StarFishServer.get_zones')
    @patch('sftocf.utils.return_patch_json')
    def test_falls_back_to_add_remove_when_bulk_set_fails(
        self, mock_patch_json, mock_get_zones, mock_get_zone_paths
    ):
        mock_get_zones.return_value = {'name': 'testzone'}
        mock_get_zone_paths.return_value = [{'vol_path': 'vol1:/old/path'}]
        mock_patch_json.side_effect = [Exception('boom'), {}, {}]

        sf = _make_server()
        failed = sf.update_zone_paths(zone_id=1, paths=['vol1:/new/path'])

        self.assertEqual(failed, [])
        self.assertEqual(mock_patch_json.call_count, 3)
        self.assertEqual(
            mock_patch_json.call_args_list[1].kwargs['data'], {'delete': ['vol1:/old/path']}
        )
        self.assertEqual(
            mock_patch_json.call_args_list[2].kwargs['data'], {'add': ['vol1:/new/path']}
        )

    @patch('sftocf.utils.StarFishServer.get_zone_paths')
    @patch('sftocf.utils.StarFishServer.get_zones')
    @patch('sftocf.utils.return_patch_json')
    def test_collects_paths_that_still_cannot_be_added(
        self, mock_patch_json, mock_get_zones, mock_get_zone_paths
    ):
        mock_get_zones.return_value = {'name': 'testzone'}
        mock_get_zone_paths.return_value = []
        mock_patch_json.side_effect = [Exception('boom'), Exception('still broken')]

        sf = _make_server()
        failed = sf.update_zone_paths(zone_id=1, paths=['vol1:/new/path'])

        self.assertEqual(failed, ['vol1:/new/path'])


class AsyncQueryPollingTests(SimpleTestCase):
    @patch('sftocf.utils.time.sleep', return_value=None)
    @patch('sftocf.utils.return_get_json')
    @patch('sftocf.utils.return_post_json')
    def test_polls_until_done_then_returns_result(self, mock_post_json, mock_get_json, mock_sleep):
        mock_post_json.return_value = {'query_id': 'q1'}
        mock_get_json.side_effect = [
            {'is_done': False},
            {'is_done': True},
            {'rows': [1, 2, 3]},
        ]
        query = AsyncQuery(
            headers={}, api_url='https://sf.example.com/api/',
            query='q', group_by='g', volpath='vol:/path',
        )
        self.assertEqual(query.result, {'rows': [1, 2, 3]})
        mock_sleep.assert_called_once()

    @patch('sftocf.utils.time.sleep', return_value=None)
    @patch('sftocf.utils.return_get_json')
    @patch('sftocf.utils.return_post_json')
    def test_raises_when_result_contains_error(self, mock_post_json, mock_get_json, mock_sleep):
        mock_post_json.return_value = {'query_id': 'q1'}
        mock_get_json.side_effect = [
            {'is_done': True},
            {'error': 'bad query'},
        ]
        query = AsyncQuery(
            headers={}, api_url='https://sf.example.com/api/',
            query='q', group_by='g', volpath='vol:/path',
        )
        with self.assertRaises(ValueError):
            query.result
