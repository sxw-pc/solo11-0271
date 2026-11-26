import json
from pathlib import Path
import unittest

from draincourt.store import connect, run


class ImageRegistryTests(unittest.TestCase):
    def test_encoded_identity_cannot_be_replaced_by_equal_diff_id(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'image_bundle.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        self.assertEqual(events[0]['input']['diff_id'], events[1]['input']['diff_id'])
        self.assertNotEqual(events[0]['input']['digest'], events[1]['input']['digest'])
        for event in events:
            if event['input']['event_id'] == 'base-arm':
                continue
            result = run(db, event['command'], event['input'])
        self.assertEqual(result, {'decision': 'layer_unavailable', 'layer': events[1]['input']['digest']})
