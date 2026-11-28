"""Registry of synthetic controller trace values and their provenance."""
from .store import one

PUBLIC = 'public'
PRIVATE = 'private'
CLASSIFICATIONS = (PUBLIC, PRIVATE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_values (
    id TEXT PRIMARY KEY, incident TEXT NOT NULL, classification TEXT NOT NULL, text TEXT NOT NULL
);
"""


def source(db, value_id, incident):
    """Return the registered value row (source identity, level and text).

    Transcript shares must consult this provenance rather than trusting the
    rendered frame text: the classification stored here decides which spans
    are redacted for a reader.
    """
    return one(db, 'SELECT * FROM trace_values WHERE id=? AND incident=?', (value_id, incident))


def handle(db, x):
    if x['op'] != 'value':
        return {'decision': 'unknown_operation'}
    if (x['classification'] not in CLASSIFICATIONS or not isinstance(x['text'], str)
            or one(db, 'SELECT 1 FROM trace_values WHERE id=?', (x['id'],))):
        return {'decision': 'trace_value_denied'}
    db.execute('INSERT INTO trace_values VALUES (?,?,?,?)', (x['id'], x['incident'], x['classification'], x['text']))
    return {'decision': 'trace_value_stored'}
