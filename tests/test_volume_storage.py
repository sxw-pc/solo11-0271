import json
from pathlib import Path
import unittest

from draincourt.store import connect, one, run
from draincourt.volume import positions


class VolumeStorageTests(unittest.TestCase):
    def test_claims_driver_observations(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'volume_handoff.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        result = {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}
        self.assertEqual(result['exit']['decision'], 'pod_exited')
        self.assertEqual(result['order-2']['decision'], 'write_acknowledged')
        self.assertEqual(result['split']['positions'], {'orders': 1, 'payments': 2})
        self.assertEqual(result['newer-partial']['positions'], {'payments': 2})
        self.assertEqual(result['driver-fence']['decision'], 'writer_fenced')
        owner = one(db, "SELECT * FROM volume_owners WHERE volume='claims'")
        self.assertEqual(positions(db, owner, 18), {'orders': 1, 'payments': 2})
        self.assertEqual(positions(db, owner, 20), {'orders': 2, 'payments': 2})
        later = dict(events[-2]['input'], id='order-3', event_id='order-3', at=24,
                     position=3, unit='U3')
        self.assertEqual(run(db, 'volume', later)['decision'], 'write_ack_denied')
