"""StarFishServer tests with HTTP/API calls mocked."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from sftocf.utils import StarFishServer


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
