import json
from pathlib import Path
import unittest

from draincourt.store import connect, one, run


class RetryAttemptTests(unittest.TestCase):
    def test_existing_budget_captures_late_actual_cost_independently(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'retry_epochs.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        results = {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}
        self.assertEqual(results['next']['decision'], 'retry_dispatched')
        self.assertEqual(results['late-result']['decision'], 'result_recorded')
        self.assertEqual(one(db, "SELECT epoch FROM retry_reservations WHERE id='uncertain'")['epoch'], 'w1')
        self.assertEqual(one(db, "SELECT spent FROM retry_attempts WHERE id='uncertain'")['spent'], 1)
