import copy
import json
import unittest

from fleet_reliability.application import ROOT
from fleet_reliability.domain import run


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'fixtures/data.json').read_text())
        self.data = {key: [] for key in self.fixture['tables']}

    def test_fixture_is_deterministic_and_not_mutated(self):
        original = copy.deepcopy(self.fixture['tables'])
        options = {'cutoff': self.fixture['meta']['default_cutoff'], 'group': ''}
        first = run(self.fixture['tables'], options)
        second = run(self.fixture['tables'], options)
        self.assertEqual(first, second)
        self.assertEqual(original, self.fixture['tables'])
        self.assertTrue(first['rows'])
        json.dumps(first, allow_nan=False)

    def sample(self):
        self.data['machines'] = [dict(machine='m', site='甲', batch='b', installed_hour=0)]
        self.data['intervals'] = [dict(machine='m', start_hour=0, end_hour=10, known_hour=0), dict(machine='m', start_hour=5, end_hour=15, known_hour=5)]
        self.data['events'] = [dict(machine='m', kind='failure', hour=8, known_hour=8), dict(machine='m', kind='retire', hour=12, known_hour=13), dict(machine='m', kind='inspection', hour=7, known_hour=7)]
        self.data['repairs'] = [dict(machine='m', grade='overhaul', hour=9, known_hour=9)]

    def test_overlapping_exposure_union_and_retirement(self):
        self.sample()
        row = run(self.data, {'cutoff': 20, 'group': ''})['rows'][0]
        self.assertEqual(row['operating_hours'], 12)
        self.assertEqual(row['failures'], 1)
        self.assertEqual(row['inspections'], 1)
        self.assertTrue(row['retired'])

    def test_future_event_does_not_affect_early_view(self):
        self.sample()
        row = run(self.data, {'cutoff': 10, 'group': ''})['rows'][0]
        self.assertFalse(row['retired'])
        self.assertEqual(row['operating_hours'], 10)
        self.assertEqual(row['overhauls'], 1)

    def test_open_interval_and_invalid_interval(self):
        from fleet_reliability.domain import exposure
        self.assertEqual(exposure([dict(start_hour=3, end_hour=None, known_hour=3)], 10), 7)
        with self.assertRaises(ValueError):
            exposure([dict(start_hour=5, end_hour=2, known_hour=0)], 10)
