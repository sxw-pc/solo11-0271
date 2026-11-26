"""Observed preStop execution routes and maintenance holds."""
import json

from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS clock_stages (
    id TEXT PRIMARY KEY, request TEXT NOT NULL, routes TEXT NOT NULL, at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clock_delegations (
    stage TEXT NOT NULL, route TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY(stage, route)
);
CREATE TABLE IF NOT EXISTS clock_holds (
    id TEXT PRIMARY KEY, route TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    observed INTEGER NOT NULL, kind TEXT NOT NULL
);
"""


def handle(db, x):
    op, at = x['op'], x['at']
    if op == 'hold':
        if (x['start'] >= x['end'] or at < x['end'] or x['kind'] not in ('maintenance', 'workload')
                or one(db, 'SELECT 1 FROM clock_holds WHERE id=?', (x['id'],))):
            return {'decision': 'hold_denied'}
        db.execute('INSERT INTO clock_holds VALUES (?,?,?,?,?,?)',
                   (x['id'], x['route'], x['start'], x['end'], at, x['kind']))
        return {'decision': 'hold_observed'}
    request = one(db, 'SELECT * FROM termination_requests WHERE id=?', (x['request'],))
    if (not request or request['uid'] != x['uid'] or request['generation'] != x['generation']
            or at < request['at']):
        return {'decision': 'clock_identity_denied'}
    if op == 'stage-start':
        if not x['routes'] or one(db, 'SELECT 1 FROM clock_stages WHERE request=? OR id=?', (x['request'], x['id'])):
            return {'decision': 'stage_denied'}
        db.execute('INSERT INTO clock_stages VALUES (?,?,?,?)',
                   (x['id'], x['request'], json.dumps(sorted(set(x['routes']))), at))
        db.execute('INSERT INTO clock_delegations VALUES (?,?,?)', (x['id'], x['routes'][0], at))
        return {'decision': 'stage_started'}
    if op == 'delegate':
        stage = one(db, 'SELECT * FROM clock_stages WHERE id=? AND request=?', (x['stage'], x['request']))
        if (not stage or at < stage['at'] or x['route'] not in json.loads(stage['routes'])
                or one(db, 'SELECT 1 FROM clock_delegations WHERE stage=? AND route=?', (x['stage'], x['route']))):
            return {'decision': 'delegation_denied'}
        db.execute('INSERT INTO clock_delegations VALUES (?,?,?)', (stage['id'], x['route'], at))
        return {'decision': 'route_delegated'}
    return {'decision': 'unknown_operation'}
