"""Recorded packet-batch observations from local synthetic flow samplers."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_batches (
    id TEXT PRIMARY KEY, node TEXT NOT NULL, address TEXT NOT NULL, phase TEXT NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL, bytes INTEGER NOT NULL, token TEXT
);
"""


def handle(db, x):
    if x['op'] != 'observe':
        return {'decision': 'unknown_operation'}
    if (x['phase'] not in ('pre-translation', 'post-translation') or not x['start'] < x['end']
            or x['bytes'] < 0 or one(db, 'SELECT id FROM flow_batches WHERE id=?', (x['id'],))):
        return {'decision': 'flow_denied'}
    db.execute('INSERT INTO flow_batches VALUES (?,?,?,?,?,?,?,?)',
               (x['id'], x['node'], x['address'], x['phase'], x['start'], x['end'], x['bytes'], x.get('token')))
    return {'decision': 'flow_recorded', 'bytes': x['bytes']}
