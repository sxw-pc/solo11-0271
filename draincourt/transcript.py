"""Render a local controller frame from literals and source spans."""
import json

from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_frames (
    id TEXT PRIMARY KEY, incident TEXT NOT NULL, recipe TEXT NOT NULL, text TEXT NOT NULL
);
"""


def handle(db, x):
    if x['op'] != 'frame':
        return {'decision': 'unknown_operation'}
    if one(db, 'SELECT 1 FROM trace_frames WHERE id=?', (x['id'],)):
        return {'decision': 'frame_denied'}
    output = []
    for part in x['recipe']:
        if 'literal' in part:
            output.append(part['literal'])
            continue
        value = one(db, 'SELECT * FROM trace_values WHERE id=? AND incident=?', (part['ref'], x['incident']))
        if not value or not 0 <= part['start'] < part['end'] <= len(value['text']):
            return {'decision': 'fragment_denied'}
        output.append(value['text'][part['start']:part['end']])
    text = ''.join(output)
    db.execute('INSERT INTO trace_frames VALUES (?,?,?,?)', (x['id'], x['incident'], json.dumps(x['recipe'], sort_keys=True), text))
    return {'decision': 'frame_rendered', 'text': text}

