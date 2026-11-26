"""Offline clock bounds and observed exchange pairs between node agents."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_clocks (id TEXT PRIMARY KEY, lower INTEGER NOT NULL, upper INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS node_exchanges (
    id TEXT PRIMARY KEY, sender TEXT NOT NULL, other TEXT NOT NULL,
    sent INTEGER NOT NULL, arrived INTEGER NOT NULL, max_delay INTEGER NOT NULL
);
"""


def handle(db, x):
    if one(db, 'SELECT 1 FROM node_clocks WHERE id=?', (x['id'],)) or one(db, 'SELECT 1 FROM node_exchanges WHERE id=?', (x['id'],)):
        return {'decision': 'observation_id_conflict'}
    if x['op'] == 'clock':
        if x['lower'] > x['upper']:
            return {'decision': 'invalid_clock_bound'}
        db.execute('INSERT INTO node_clocks VALUES (?,?,?)', (x['id'], x['lower'], x['upper']))
        return {'decision': 'clock_noted'}
    if x['op'] == 'exchange':
        if (x['sender'] == x['other'] or x['max_delay'] < 0
                or not all(one(db, 'SELECT 1 FROM node_clocks WHERE id=?', (x[k],)) for k in ('sender', 'other'))):
            return {'decision': 'invalid_exchange'}
        db.execute('INSERT INTO node_exchanges VALUES (?,?,?,?,?,?)',
                   (x['id'], x['sender'], x['other'], x['sent'], x['arrived'], x['max_delay']))
        return {'decision': 'exchange_noted'}
    return {'decision': 'unknown_operation'}
