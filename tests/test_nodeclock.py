import json
from pathlib import Path
import unittest

from draincourt.store import connect, one, run


class NodeClockTests(unittest.TestCase):
    def test_node_local_values_and_exchange_are_persisted(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'clock_incident.json'
        visible = {e['input']['event_id']: run(db, e['command'], e['input'])
                   for e in json.loads(fixture.read_text(encoding='utf8'))}
        self.assertEqual(visible['a']['local_tick'], 34)
        self.assertEqual(visible['b']['local_tick'], 31)
        self.assertEqual(visible['r-to-n']['decision'], 'exchange_noted')
        self.assertEqual(one(db, "SELECT max_delay FROM node_exchanges WHERE id='r-to-n'")['max_delay'], 2)
