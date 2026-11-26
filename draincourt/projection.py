"""Local model of published configuration generations and descriptor reads."""
import json

from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_generations (
    id TEXT PRIMARY KEY, files TEXT NOT NULL, rank INTEGER UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS config_publications (
    generation TEXT PRIMARY KEY, at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS config_reads (
    id TEXT PRIMARY KEY, generation TEXT NOT NULL, file TEXT NOT NULL,
    text TEXT NOT NULL, at INTEGER NOT NULL
);
"""


def published(db, at):
    return one(db, 'SELECT g.*, p.at FROM config_generations g JOIN config_publications p '
                   'ON p.generation=g.id WHERE p.at<=? ORDER BY g.rank DESC LIMIT 1', (at,))


def handle(db, x):
    op, at = x['op'], x['at']
    if op == 'stage':
        if (not x['files'] or not all(isinstance(v, str) for v in x['files'].values())
                or one(db, 'SELECT 1 FROM config_generations WHERE id=? OR rank=?', (x['id'], x['rank']))):
            return {'decision': 'bundle_denied'}
        db.execute('INSERT INTO config_generations VALUES (?,?,?)', (x['id'], json.dumps(x['files'], sort_keys=True), x['rank']))
        return {'decision': 'bundle_staged'}
    if op == 'publish':
        requested = one(db, 'SELECT * FROM config_generations WHERE id=?', (x['generation'],))
        active = one(db, 'SELECT g.rank, p.at FROM config_generations g JOIN config_publications p '
                         'ON p.generation=g.id ORDER BY g.rank DESC LIMIT 1')
        if not requested or (active and (requested['rank'] <= active['rank'] or at < active['at'])):
            return {'decision': 'publication_denied'}
        db.execute('INSERT INTO config_publications VALUES (?,?)', (requested['id'], at))
        return {'decision': 'bundle_published'}
    if op == 'read':
        previous = one(db, 'SELECT * FROM config_reads WHERE id=?', (x['relative_to'],)) if x.get('relative_to') else None
        if x.get('relative_to'):
            if not previous or previous['at'] > at:
                return {'decision': 'read_denied'}
            target = one(db, 'SELECT * FROM config_generations WHERE id=?', (previous['generation'],))
        else:
            target = published(db, at)
        files = json.loads(target['files']) if target else {}
        if x['file'] not in files or one(db, 'SELECT 1 FROM config_reads WHERE id=?', (x['id'],)):
            return {'decision': 'read_denied'}
        db.execute('INSERT INTO config_reads VALUES (?,?,?,?,?)', (x['id'], target['id'], x['file'], files[x['file']], at))
        return {'decision': 'config_read', 'generation': target['id'], 'text': files[x['file']]}
    return {'decision': 'unknown_operation'}
