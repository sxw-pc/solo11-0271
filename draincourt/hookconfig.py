"""Illustrative workload loader for command and routing files on a projected mount."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_loads (
    id TEXT PRIMARY KEY, command_read TEXT NOT NULL, routing_read TEXT NOT NULL,
    invocation TEXT NOT NULL, at INTEGER NOT NULL
);
"""


def handle(db, x):
    if x['op'] != 'load':
        return {'decision': 'unknown_operation'}
    command = one(db, 'SELECT * FROM config_reads WHERE id=? AND file=? AND at<=?',
                  (x['command_read'], 'command', x['at']))
    routing = one(db, 'SELECT * FROM config_reads WHERE id=? AND file=? AND at<=?',
                  (x['routing_read'], 'routing', x['at']))
    if not command or not routing or one(db, 'SELECT 1 FROM hook_loads WHERE id=?', (x['id'],)):
        return {'decision': 'hook_load_denied'}
    invocation = command['text'] + ' ' + routing['text']
    db.execute('INSERT INTO hook_loads VALUES (?,?,?,?,?)',
               (x['id'], command['id'], routing['id'], invocation, x['at']))
    return {'decision': 'hook_loaded', 'invocation': invocation}
