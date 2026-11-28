"""Snapshot actual route changes installed by independent controllers."""
from .store import one, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_entries (
    service TEXT NOT NULL, name TEXT NOT NULL, manager TEXT NOT NULL, method TEXT NOT NULL,
    prefix TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(service, name)
);
"""


def choose(entries, method, path):
    """Pick the winning entry with the actual priority semantics.

    Longest literal prefix wins, then an exact method over '*', then the name.
    Shared by runtime resolution and the publish merge so both judge request
    ownership with the same rules.
    """
    candidates = [route for route in entries
                  if route['method'] in (method, '*') and path.startswith(route['prefix'])]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (-len(r['prefix']), r['method'] == '*', r['name']))


def direct(db, service, method, path):
    chosen = choose(rows(db, 'SELECT * FROM route_entries WHERE service=? AND method IN (?,?)',
                         (service, method, '*')), method, path)
    return chosen['target'] if chosen else None


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
