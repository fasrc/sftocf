"""Tests for the starfish_set_zone_userparam receiver."""
from unittest.mock import patch

from django.test import SimpleTestCase

from sftocf.signals import starfish_set_zone_userparam

from sftocf import receivers


class UpdateStarfishZoneParamsTests(SimpleTestCase):
    """Generic zone_user_param pass-through: any caller may send zone_id,
    param_name, and param_value; this just forwards them to Starfish."""

    @patch('sftocf.receivers.StarfishClient')
    def test_forwards_kwargs_to_set_zone_user_param(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.set_zone_user_param.return_value = 'ok'

        result = receivers.update_starfish_zone_params(
            sender=self.__class__,
            zone_id='42',
            param_name='archive_globus_collection',
            param_value='the-uuid',
        )

        mock_client.set_zone_user_param.assert_called_once_with(
            '42', 'archive_globus_collection', 'the-uuid'
        )
        self.assertEqual(result, 'ok')

    @patch('sftocf.receivers.StarfishClient')
    def test_connected_to_starfish_set_zone_userparam_signal(self, mock_client_cls):
        mock_client = mock_client_cls.return_value

        starfish_set_zone_userparam.send(
            sender=self.__class__,
            zone_id='42',
            param_name='archive_globus_collection',
            param_value='the-uuid',
        )

        mock_client.set_zone_user_param.assert_called_once_with(
            '42', 'archive_globus_collection', 'the-uuid'
        )

    @patch('sftocf.receivers.StarfishClient')
    def test_works_for_an_arbitrary_param_name(self, mock_client_cls):
        """Not Globus-UUID-specific: any param_name/value can be pushed this way."""
        mock_client = mock_client_cls.return_value

        starfish_set_zone_userparam.send(
            sender=self.__class__,
            zone_id='7',
            param_name='some_other_param',
            param_value='some_value',
        )

        mock_client.set_zone_user_param.assert_called_once_with(
            '7', 'some_other_param', 'some_value'
        )
