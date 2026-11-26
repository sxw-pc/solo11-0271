import json
from pathlib import Path
import unittest

from draincourt.store import connect, one, run


class FlowObservationTests(unittest.TestCase):
    def test_reuse_and_packet_observations_keep_independent_identities(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'address_reuse.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        result = {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}
        self.assertEqual(result['lease-new']['decision'], 'address_allocated')
        self.assertEqual(result['crossing']['bytes'], 10)
        self.assertEqual(one(db, "SELECT owner FROM address_leases WHERE id='lease-new'")['owner'], 'team-b')
        self.assertEqual(one(db, "SELECT bytes FROM flow_batches WHERE id='post'")['bytes'], 90)
