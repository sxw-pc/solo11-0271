"""Snapshot actual route changes installed by independent controllers."""
from .store import one, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_entries (
    service TEXT NOT NULL, name TEXT NOT NULL, manager TEXT NOT NULL, method TEXT NOT NULL,
    prefix TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(service, name)
);
"""


def direct(db, service, method, path):
    candidates = [route for route in rows(db, 'SELECT * FROM route_entries WHERE service=? AND method IN (?,?)',
                                         (service, method, '*')) if path.startswith(route['prefix'])]
    if not candidates:
        return None
    chosen = min(candidates, key=lambda r: (-len(r['prefix']), r['method'] == '*', r['name']))
    return chosen['target']


def handle(db, x):
    if x['op'] == 'observe':
        if not x['prefix'].startswith('/') or not x['manager'] or not x['target']:
            return {'decision': 'route_declined'}
        db.execute('INSERT INTO route_entries VALUES (?,?,?,?,?,?) ON CONFLICT(service,name) DO UPDATE SET '
                   'manager=excluded.manager, method=excluded.method, prefix=excluded.prefix, target=excluded.target',
                   tuple(x[key] for key in ('service', 'name', 'manager', 'method', 'prefix', 'target')))
        return {'decision': 'route_observed'}
    if x['op'] == 'resolve':
        target = direct(db, x['service'], x['method'], x['path'])
        return {'decision': 'route_resolved', 'target': target}
    return {'decision': 'unknown_operation'}
