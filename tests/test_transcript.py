import json
from pathlib import Path
import unittest

from draincourt.store import connect, run


class TraceTranscriptTests(unittest.TestCase):
    def test_synthetic_fragments_keep_provenance(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'transcript_fragments.json'
        events = json.loads(fixture.read_text(encoding='utf8'))
        results = {e['input']['event_id']: run(db, e['command'], e['input']) for e in events}
        self.assertEqual(results['first']['text'], 'pod=RID- phase=waiting tag=RID- suffix=π-732')
        self.assertEqual(results['second']['text'], 'done=π-732 peer=RID-π-732')
        invalid = dict(events[-1]['input'], id='wrong', event_id='wrong', incident='one')
        self.assertEqual(run(db, 'transcript', invalid)['decision'], 'fragment_denied')
