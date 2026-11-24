"""Tests for the starfish_zone_created signal sent by update_zones."""
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from coldfront.core.test_helpers.factories import (
    AllocationFactory,
    AllocationStatusChoiceFactory,
    PAttributeTypeFactory,
    ProjectAttributeTypeFactory,
    ProjectFactory,
    ProjectStatusChoiceFactory,
    ResourceFactory,
    ResourceTypeFactory,
    UserFactory,
)

from sftocf.signals import starfish_zone_created


def _make_starfish_mock(groups, resources):
    sf = MagicMock()
    sf.get_corresponding_coldfront_resources.return_value = resources
    sf.get_zones.return_value = []
    sf.get_groups.return_value = groups
    sf.get_volumes_in_coldfront.return_value = []
    sf.zone_from_project.return_value = {'id': 'zone-123'}
    return sf


@patch('sftocf.management.commands.update_zones.DEBUG', False)
@patch('sftocf.management.commands.update_zones.StarfishClient')
class UpdateZonesStarfishZoneCreatedSignalTests(TestCase):
    """update_zones is organization-agnostic: it only announces that it created a
    zone for a project (starfish_zone_created), and knows nothing about what any
    particular institution wants to do with that (see coldfront.plugins.fasrc for
    FASRC's own archive_globus_collection reaction to this same signal)."""

    def setUp(self):
        ProjectAttributeTypeFactory(
            name='Starfish Zone', attribute_type=PAttributeTypeFactory(name='Text')
        )
        ProjectStatusChoiceFactory(name='Active')
        resource_type = ResourceTypeFactory(name='Storage')
        self.resource = ResourceFactory(resource_type=resource_type)
        pi = UserFactory()
        self.project = ProjectFactory(pi=pi, title='test_project', status__name='Active')

        self._received = []

        def handler(sender, **kwargs):
            self._received.append(kwargs)

        starfish_zone_created.connect(handler, weak=False)
        self.addCleanup(starfish_zone_created.disconnect, handler)

    def test_sends_signal_when_new_zone_created(self, mock_client_cls):
        AllocationStatusChoiceFactory(name='Active')
        allocation = AllocationFactory(project=self.project, status__name='Active')
        allocation.resources.add(self.resource)
        mock_sf = _make_starfish_mock(groups=[self.project.title], resources=[self.resource])
        mock_client_cls.return_value = mock_sf

        call_command('update_zones')

        self.assertEqual(len(self._received), 1)
        self.assertEqual(self._received[0]['project'], self.project)
        self.assertEqual(self._received[0]['zone_id'], 'zone-123')

    def test_no_signal_when_no_matching_project(self, mock_client_cls):
        # no allocation on self.resource, so the project never qualifies for a zone
        mock_sf = _make_starfish_mock(groups=[self.project.title], resources=[self.resource])
        mock_client_cls.return_value = mock_sf

        call_command('update_zones')

        self.assertEqual(self._received, [])
