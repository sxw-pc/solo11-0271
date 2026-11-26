import json
from pathlib import Path
import unittest

from draincourt.store import connect, one, rows, run


class HookClockTests(unittest.TestCase):
    def test_delegated_route_and_closed_maintenance_receipts(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'hookclock.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        for event in events[:4]:
            run(db, event['command'], event['input'])
        self.assertEqual([run(db, e['command'], e['input'])['decision'] for e in events[4:]],
                         ['stage_started', 'route_delegated', 'hold_observed', 'hold_observed',
                          'hold_observed', 'hold_observed', 'hook_done'])
        self.assertEqual(rows(db, 'SELECT route, at FROM clock_delegations ORDER BY at'),
                         [{'route': 'primary', 'at': 14}, {'route': 'mirror', 'at': 18}])
        self.assertEqual(one(db, 'SELECT at FROM hook_receipts')['at'], 25)

