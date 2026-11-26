"""Offline retry-token reservations and fixed rolling budget epochs."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS retry_epochs (
    name TEXT PRIMARY KEY, service TEXT NOT NULL, start INTEGER NOT NULL, stop INTEGER NOT NULL,
    capacity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS retry_reservations (
    id TEXT PRIMARY KEY, epoch TEXT NOT NULL, at INTEGER NOT NULL, units INTEGER NOT NULL
);
"""


def current(db, service, at):
    return one(db, 'SELECT * FROM retry_epochs WHERE service=? AND start<=? AND stop>?', (service, at, at))


def handle(db, x):
    if x['op'] != 'epoch':
        return {'decision': 'unknown_operation'}
    if (x['start'] >= x['stop'] or x['capacity'] <= 0 or one(db, 'SELECT 1 FROM retry_epochs '
            'WHERE name=? OR (service=? AND start<? AND stop>?)', (x['id'], x['service'], x['stop'], x['start']))):
        return {'decision': 'epoch_denied'}
    db.execute('INSERT INTO retry_epochs VALUES (?,?,?,?,?)',
               (x['id'], x['service'], x['start'], x['stop'], x['capacity']))
    return {'decision': 'epoch_available'}
