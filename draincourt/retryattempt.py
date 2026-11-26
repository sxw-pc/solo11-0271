"""Issue simulated backend attempts after reserving retry budget."""
from .retrybudget import current
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS retry_attempts (
    id TEXT PRIMARY KEY, parent TEXT NOT NULL, service TEXT NOT NULL, started INTEGER NOT NULL,
    state TEXT NOT NULL, observed INTEGER, spent INTEGER
);
"""


def handle(db, x):
    if x['op'] == 'dispatch':
        epoch = current(db, x['service'], x['at'])
        if not epoch or one(db, 'SELECT 1 FROM retry_attempts WHERE id=?', (x['id'],)) or x['units'] < 1:
            return {'decision': 'dispatch_denied'}
        used = one(db, 'SELECT COALESCE(SUM(units),0) AS n FROM retry_reservations WHERE epoch=?', (epoch['name'],))['n']
        if used + x['units'] > epoch['capacity']:
            return {'decision': 'retry_budget_exhausted'}
        db.execute('INSERT INTO retry_reservations VALUES (?,?,?,?)', (x['id'], epoch['name'], x['at'], x['units']))
        db.execute('INSERT INTO retry_attempts VALUES (?,?,?,?,?,NULL,NULL)',
                   (x['id'], x['parent'], x['service'], x['at'], 'pending'))
        return {'decision': 'retry_dispatched'}
    if x['op'] == 'result':
        attempt = one(db, 'SELECT * FROM retry_attempts WHERE id=?', (x['attempt'],))
        reservation = one(db, 'SELECT units FROM retry_reservations WHERE id=?', (x['attempt'],))
        if (not attempt or attempt['service'] != x['service'] or attempt['state'] != 'pending'
                or x['at'] < attempt['started'] or not 0 <= x['spent'] <= reservation['units']):
            return {'decision': 'result_denied'}
        db.execute("UPDATE retry_attempts SET state='completed', observed=?, spent=? WHERE id=?",
                   (x['at'], x['spent'], attempt['id']))
        return {'decision': 'result_recorded'}
    return {'decision': 'unknown_operation'}
