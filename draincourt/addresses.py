"""Offline allocation inventory for historical pod network addresses."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS address_leases (
    id TEXT PRIMARY KEY, node TEXT NOT NULL, address TEXT NOT NULL, owner TEXT NOT NULL,
    pod TEXT NOT NULL, start INTEGER NOT NULL, released INTEGER
);
"""


def handle(db, x):
    if x['op'] == 'allocate':
        if (one(db, 'SELECT id FROM address_leases WHERE id=?', (x['id'],))
                or one(db, 'SELECT id FROM address_leases WHERE node=? AND address=? AND '
                           '(released IS NULL OR released>?)', (x['node'], x['address'], x['at']))):
            return {'decision': 'allocation_denied'}
        db.execute('INSERT INTO address_leases VALUES (?,?,?,?,?,?,NULL)',
                   (x['id'], x['node'], x['address'], x['owner'], x['pod'], x['at']))
        return {'decision': 'address_allocated'}
    if x['op'] == 'release':
        lease = one(db, 'SELECT * FROM address_leases WHERE id=?', (x['lease'],))
        if not lease or lease['released'] is not None or x['owner'] != lease['owner'] or x['at'] < lease['start']:
            return {'decision': 'lease_release_denied'}
        db.execute('UPDATE address_leases SET released=? WHERE id=?', (x['at'], x['lease']))
        return {'decision': 'address_released'}
    return {'decision': 'unknown_operation'}
