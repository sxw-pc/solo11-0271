"""Incident milestones as recorded on each node's own clock."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_milestones (id TEXT PRIMARY KEY, incident TEXT NOT NULL, node TEXT NOT NULL,
    kind TEXT NOT NULL, tick INTEGER NOT NULL);
"""


def handle(db, x):
    if x['op'] != 'mark':
        return {'decision': 'unknown_operation'}
    if (not one(db, 'SELECT 1 FROM node_clocks WHERE id=?', (x['node'],))
            or one(db, 'SELECT 1 FROM node_milestones WHERE id=?', (x['id'],))):
        return {'decision': 'milestone_denied'}
    db.execute('INSERT INTO node_milestones VALUES (?,?,?,?,?)',
               (x['id'], x['incident'], x['node'], x['kind'], x['tick']))
    return {'decision': 'milestone_saved', 'local_tick': x['tick']}
