"""Starfish-related Django signals are defined and dispatchable."""

from django.test import SimpleTestCase

from sftocf import signals


class StarfishSignalsTests(SimpleTestCase):
    def test_signals_exist(self):
        self.assertTrue(hasattr(signals, 'starfish_add_aduser'))
        self.assertTrue(hasattr(signals, 'starfish_remove_aduser'))
        self.assertTrue(hasattr(signals, 'starfish_add_adgroup'))

    def test_send_receive_roundtrip(self):
        received = []

        def handler(sender, **kwargs):
            received.append(kwargs.get('username'))

        signals.starfish_add_aduser.connect(handler)
        try:
            signals.starfish_add_aduser.send(sender=self.__class__, username='tester')
            self.assertEqual(received, ['tester'])
        finally:
            signals.starfish_add_aduser.disconnect(handler)
