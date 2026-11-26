"""Registry of synthetic controller trace values and their provenance."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_values (
    id TEXT PRIMARY KEY, incident TEXT NOT NULL, classification TEXT NOT NULL, text TEXT NOT NULL
);
"""


def handle(db, x):
    if x['op'] != 'value':
        return {'decision': 'unknown_operation'}
    if (x['classification'] not in ('public', 'private') or not isinstance(x['text'], str)
            or one(db, 'SELECT 1 FROM trace_values WHERE id=?', (x['id'],))):
        return {'decision': 'trace_value_denied'}
    db.execute('INSERT INTO trace_values VALUES (?,?,?,?)', (x['id'], x['incident'], x['classification'], x['text']))
    return {'decision': 'trace_value_stored'}
