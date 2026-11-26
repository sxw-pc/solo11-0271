"""Observe checkout service probes that must survive gateway reconciliation."""
from .routes import direct
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_probes (
    id TEXT PRIMARY KEY, service TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
    asserted_by TEXT NOT NULL, observed_target TEXT NOT NULL
);
"""


def handle(db, x):
    if x['op'] != 'probe':
        return {'decision': 'unknown_operation'}
    target = direct(db, x['service'], x['method'], x['path'])
    if not target or one(db, 'SELECT 1 FROM route_probes WHERE id=?', (x['id'],)):
        return {'decision': 'probe_denied'}
    db.execute('INSERT INTO route_probes VALUES (?,?,?,?,?,?)',
               (x['id'], x['service'], x['method'], x['path'], x['asserted_by'], target))
    return {'decision': 'target_observed', 'target': target}
