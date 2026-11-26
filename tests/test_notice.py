import json
from pathlib import Path
import unittest

from draincourt.store import connect, one, run


class NoticeTests(unittest.TestCase):
    def test_delivery_order_and_receipt_are_independent(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'notice_delivery.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        results = {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}
        self.assertEqual(results['delivered-healthy']['incident'], 'closed')
        self.assertEqual(results['delivered-first']['incident'], 'open')
        self.assertEqual(one(db, "SELECT incident FROM notice_receiver_state WHERE receiver='ops'")['incident'], 'open')
        self.assertEqual(one(db, "SELECT acknowledged FROM notice_attempts WHERE id='warning-first'")['acknowledged'], 20)
        wrong = dict(events[-1]['input'], event_id='wrong-receiver', receiver='other')
        self.assertEqual(run(db, 'notice', wrong)['decision'], 'attempt_denied')
