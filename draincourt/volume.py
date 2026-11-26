"""Offline storage driver observations for a retiring Pod's writable volume."""
import json

from .store import one, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS volume_owners (
    volume TEXT PRIMARY KEY, request TEXT NOT NULL, uid TEXT NOT NULL,
    generation INTEGER NOT NULL, streams TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS volume_writes (
    volume TEXT NOT NULL, stream TEXT NOT NULL, position INTEGER NOT NULL,
    at INTEGER NOT NULL, unit TEXT NOT NULL, PRIMARY KEY (volume, stream, position),
    UNIQUE (volume, stream, unit)
);
CREATE TABLE IF NOT EXISTS volume_checkpoints (
    id TEXT PRIMARY KEY, volume TEXT NOT NULL, at INTEGER NOT NULL,
    positions TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS volume_fences (
    volume TEXT PRIMARY KEY, receipt TEXT NOT NULL, at INTEGER NOT NULL
);
"""


def positions(db, owner, at):
    """Return the delivered write frontier at the supplied observation time."""
    result = dict.fromkeys(json.loads(owner['streams']), 0)
    for row in rows(db, 'SELECT stream, MAX(position) AS position FROM volume_writes '
                       'WHERE volume=? AND at<=? GROUP BY stream', (owner['volume'], at)):
        result[row['stream']] = row['position']
    return result


def handle(db, event):
    op = event['op']
    volume = event['volume']
    at = event['at']
    owner = one(db, 'SELECT * FROM volume_owners WHERE volume=?', (volume,))
    if op == 'attach':
        request = one(db, 'SELECT * FROM termination_requests WHERE id=?', (event['request'],))
        streams = event['streams']
        if (owner or not request or request['uid'] != event['uid']
                or request['generation'] != event['generation'] or at < request['at']
                or not streams or len(set(streams)) != len(streams)):
            return {'decision': 'attachment_denied'}
        db.execute('INSERT INTO volume_owners VALUES (?,?,?,?,?,?)',
                   (volume, request['id'], request['uid'], request['generation'],
                    json.dumps(sorted(streams)), at))
        return {'decision': 'writer_attached'}
    if (not owner or at < owner['at'] or event['uid'] != owner['uid']
            or event['generation'] != owner['generation']):
        return {'decision': 'storage_identity_denied'}
    frontier = positions(db, owner, at)
    fence = one(db, 'SELECT * FROM volume_fences WHERE volume=?', (volume,))
    if op == 'write-ack':
        stream = event['stream']
        latest = one(db, 'SELECT MAX(at) AS last FROM volume_writes WHERE volume=? AND stream=?',
                     (volume, stream))['last']
        sealed = one(db, 'SELECT MAX(at) AS last FROM volume_checkpoints WHERE volume=?', (volume,))['last']
        if (fence or stream not in frontier or event['position'] != frontier[stream] + 1
                or not event['unit'] or one(db, 'SELECT 1 FROM volume_writes WHERE volume=? AND stream=? AND unit=?',
                                             (volume, stream, event['unit']))
                or (latest is not None and at < latest) or (sealed is not None and at <= sealed)):
            return {'decision': 'write_ack_denied'}
        db.execute('INSERT INTO volume_writes VALUES (?,?,?,?,?)',
                   (volume, stream, event['position'], at, event['unit']))
        return {'decision': 'write_acknowledged', 'positions': positions(db, owner, at)}
    if op == 'checkpoint':
        supplied = event['positions']
        if (not supplied or not all(stream in frontier and 0 <= position <= frontier[stream]
                                   for stream, position in supplied.items())
                or one(db, 'SELECT id FROM volume_checkpoints WHERE id=?', (event['id'],))):
            return {'decision': 'checkpoint_denied'}
        db.execute('INSERT INTO volume_checkpoints VALUES (?,?,?,?)',
                   (event['id'], volume, at, json.dumps(supplied, sort_keys=True)))
        return {'decision': 'checkpoint_available', 'positions': supplied}
    if op == 'fence':
        latest = one(db, 'SELECT MAX(at) AS last FROM volume_writes WHERE volume=?', (volume,))['last']
        if fence or (latest is not None and at < latest):
            return {'decision': 'fence_denied'}
        db.execute('INSERT INTO volume_fences VALUES (?,?,?)', (volume, event['id'], at))
        return {'decision': 'writer_fenced', 'positions': frontier}
    return {'decision': 'unknown_operation'}

