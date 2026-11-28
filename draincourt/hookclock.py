"""Observed preStop execution routes and maintenance holds."""
import json

from .store import one, rows

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


MAX_COMPENSATION = 10


def _merge(intervals):
    """Union of half-open intervals so one moment is never counted twice."""
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def _subtract(base, blocks):
    """Half-open intervals of `base` not covered by the merged `blocks`."""
    free = []
    for start, end in base:
        cursor = start
        for block_start, block_end in blocks:
            if block_end <= cursor or block_start >= end:
                continue
            if block_start > cursor:
                free.append([cursor, min(block_start, end)])
            cursor = max(cursor, block_end)
            if cursor >= end:
                break
        if cursor < end:
            free.append([cursor, end])
    return free


def compensation(db, request, at, expires):
    """Platform-blocked preStop time for `request` as adjudicated at `at`.

    Counts only moments inside the original grace window [0, expires) where
    the stage had started, no valid completion receipt for the same request
    and generation had been delivered yet, and every route activated by a
    real delegation receipt was simultaneously under an observed maintenance
    hold. Only maintenance and delegation receipts delivered by `at` take
    part; ordinary workload stalls never do, and accumulation stops once the
    hook completes even if the base deadline is still ahead. The total is
    capped at MAX_COMPENSATION.
    """
    stage = one(db, 'SELECT * FROM clock_stages WHERE request=?', (request,))
    if not stage or stage['at'] > at:
        return {'compensation': 0, 'intervals': []}
    hook = one(db, 'SELECT at FROM hook_receipts WHERE request=? AND at<=? ORDER BY at LIMIT 1',
               (request, at))
    window_end = min(expires, hook['at']) if hook else expires
    if stage['at'] >= window_end:
        return {'compensation': 0, 'intervals': []}
    blocked = {}
    for hold in rows(db, "SELECT route, start, end FROM clock_holds WHERE kind='maintenance' AND observed<=?",
                     (at,)):
        blocked.setdefault(hold['route'], []).append((hold['start'], hold['end']))
    free = []
    delegations = rows(db, 'SELECT route, at FROM clock_delegations WHERE stage=? AND at<=?', (stage['id'], at))
    for delegation in delegations:
        if delegation['at'] < window_end:
            free += _subtract([[delegation['at'], window_end]], _merge(blocked.get(delegation['route'], [])))
    intervals = _subtract([[stage['at'], window_end]], _merge(free))
    total = sum(end - start for start, end in intervals)
    return {'compensation': min(MAX_COMPENSATION, total), 'intervals': intervals}
