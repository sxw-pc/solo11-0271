import json
from pathlib import Path
import unittest

from draincourt.store import connect, run


class RoutingTests(unittest.TestCase):
    def test_runtime_resolution_captures_other_controller_routes(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'route_reconcile.json'
        results = {e['input']['event_id']: run(db, e['command'], e['input'])
                   for e in json.loads(fixture.read_text(encoding='utf8'))}
        self.assertEqual(results['receipt']['target'], 'idempotent-ledger')
        self.assertEqual(results['payment']['target'], 'current')
