import json
from pathlib import Path
import unittest

from draincourt.store import connect, run


class ProjectionTests(unittest.TestCase):
    def test_per_file_resolution_and_descriptor_relative_read(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'projection_rotation.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        seen = {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}
        self.assertEqual(seen['command-before']['text'], seen['command-after']['text'])
        self.assertEqual(seen['routing-middle']['generation'], 'g2')
        self.assertEqual(seen['routing-pinned'], {'decision': 'config_read', 'generation': 'g1', 'text': 'route-A'})
        self.assertEqual(seen['naive-hook']['invocation'], 'flush-v1 route-B')
