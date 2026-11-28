"""Merge publisher change orders into the actual route inventory.

Planning uses the same longest-prefix/exact-method/name priority as
routes.direct and is constrained by the ownership evidence recorded through
gatewaycheck probes. Request regions owned by another manager are either
handed over through an explicit approved method+prefix scope or kept resolving
to their pre-change backend with generated protection routes. The change
route, generated protections and the publication marker commit atomically;
conflicts or stale evidence keep the previous actual flow.
"""
import json

from .routes import choose
from .store import one, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS route_publications (
    service TEXT NOT NULL, id TEXT NOT NULL, change_name TEXT NOT NULL,
    request TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(service, id)
);
"""


def _resolve(entries, method, path):
    chosen = choose(entries, method, path)
    return chosen['target'] if chosen else None


def _affected_methods(entries, change):
    if change['method'] != '*':
        return [change['method']]
    # Every concrete method installed so far, plus '*' as the sentinel that
    # stands for all other request methods (only wildcard routes match it).
    return sorted({e['method'] for e in entries if e['method'] != '*'}) + ['*']


def _regions(entries, change):
    """Request regions whose resolution the change would actually move.

    The change can only win where it is the longest matching prefix, so every
    moved region is rooted at the change prefix itself. Evaluating that prefix
    per affected method covers the whole method/literal-prefix intersection,
    including sub-paths no probe ever reached.
    """
    planned = entries + [change]
    found = []
    for method in _affected_methods(entries, change):
        before = choose(entries, method, change['prefix'])
        after = choose(planned, method, change['prefix'])
        before_target = before['target'] if before else None
        after_target = after['target'] if after else None
        if before_target != after_target:
            found.append({'method': method, 'prefix': change['prefix'],
                          'manager': before['manager'] if before else None,
                          'before': before_target, 'after': after_target})
    return found


def _rejected(reason, **detail):
    return {'decision': 'publish_rejected', 'reason': reason, **detail}


def _plan(db, x):
    service, change = x['service'], x['change']
    entries = rows(db, 'SELECT * FROM route_entries WHERE service=?', (service,))
    for e in entries:
        if e['name'] == change['name'] and any(e[k] != change[k]
                                               for k in ('manager', 'method', 'prefix', 'target')):
            return None, _rejected('route_name_conflict', name=change['name'])
    # Ownership evidence must agree with the current inventory before commit.
    for p in rows(db, 'SELECT * FROM route_probes WHERE service=? ORDER BY rowid', (service,)):
        if _resolve(entries, p['method'], p['path']) != p['observed_target']:
            return None, _rejected('evidence_mismatch', probe=p['id'])
    regions = _regions(entries, change)
    foreign = [r for r in regions if r['manager'] and r['manager'] != change['manager']]
    approval = x.get('approval') or {}
    handover = approval.get('handover') or []
    for s in handover:
        if not str(s.get('prefix') or '').startswith('/') or not s.get('method'):
            return None, _rejected('invalid_handover_scope')
    for r in foreign:
        if approval.get('granted_by') != r['manager']:
            return None, _rejected('approval_missing', manager=r['manager'],
                                   method=r['method'], prefix=r['prefix'])
        r['handed_over'] = any(s['method'] in ('*', r['method']) and r['prefix'].startswith(s['prefix'])
                               for s in handover)
    protections = []
    for r in foreign:
        if r['handed_over']:
            continue
        name = 'protect-{}-{}-{}'.format(x['id'], r['method'],
                                         r['prefix'].strip('/').replace('/', '-') or 'root')
        protections.append({'service': service, 'name': name, 'manager': r['manager'],
                            'method': r['method'], 'prefix': r['prefix'], 'target': r['before']})
    names = {e['name'] for e in entries} | {change['name']}
    for p in protections:
        if p['name'] in names:
            return None, _rejected('protection_name_conflict', name=p['name'])
    guarded = entries + protections
    planned = guarded + [change]
    # Protections must not seize requests that already resolved to a backend.
    check_methods = sorted({e['method'] for e in planned if e['method'] != '*'}) + ['*']
    check_paths = sorted({e['prefix'] for e in planned})
    for method in check_methods:
        for path in check_paths:
            if _resolve(entries, method, path) != _resolve(guarded, method, path):
                return None, _rejected('protection_overreach', method=method, path=path)
    # An approved handover scope must be wholly taken over by the new target.
    for s in handover:
        sm, sp = s['method'], s['prefix']
        if not sp.startswith(change['prefix']) or (change['method'] not in ('*', sm) and sm != '*'):
            return None, _rejected('scope_not_taken_over', method=sm, prefix=sp)
        if any(e['prefix'] != sp and e['prefix'].startswith(sp) and e['method'] in (sm, '*')
               for e in entries):
            return None, _rejected('scope_not_taken_over', method=sm, prefix=sp)
        if _resolve(planned, sm, sp) != change['target']:
            return None, _rejected('scope_not_taken_over', method=sm, prefix=sp)
    # Every foreign region ends up handed over to the new target or preserved.
    for r in foreign:
        want = change['target'] if r['handed_over'] else r['before']
        if _resolve(planned, r['method'], r['prefix']) != want:
            return None, _rejected('region_verification_failed', method=r['method'], prefix=r['prefix'])
    taken_over = []
    for r in regions:
        if r in foreign and not r['handed_over']:
            continue
        taken_over.append({'method': r['method'], 'prefix': r['prefix'], 'from': r['before'],
                           'to': change['target'], 'manager': r['manager'],
                           'basis': 'handover' if r in foreign else 'own'})
    result = {'decision': 'published', 'service': service, 'id': x['id'], 'installed': change['name'],
              'taken_over': taken_over,
              'protected': [{'name': p['name'], 'method': p['method'], 'prefix': p['prefix'],
                             'target': p['target'], 'manager': p['manager']} for p in protections]}
    return (change, protections, result), None


def handle(db, x):
    if x['op'] != 'publish':
        return {'decision': 'unknown_operation'}
    change = x.get('change') or {}
    if (not x.get('service') or not x.get('id')
            or any(not change.get(k) for k in ('name', 'manager', 'method', 'target'))
            or not str(change.get('prefix') or '').startswith('/')):
        return {'decision': 'publish_declined'}
    request = json.dumps({'change': change, 'approval': x.get('approval')},
                         sort_keys=True, ensure_ascii=False)
    pub = one(db, 'SELECT * FROM route_publications WHERE service=? AND id=?', (x['service'], x['id']))
    if pub:
        if pub['request'] == request:
            return json.loads(pub['result'])
        return _rejected('publication_conflict', id=x['id'])
    plan, rejection = _plan(db, x)
    if rejection:
        return rejection
    change, protections, result = plan
    db.execute('INSERT INTO route_entries VALUES (?,?,?,?,?,?) ON CONFLICT(service,name) DO UPDATE SET '
               'manager=excluded.manager, method=excluded.method, prefix=excluded.prefix, target=excluded.target',
               (x['service'], change['name'], change['manager'], change['method'],
                change['prefix'], change['target']))
    for p in protections:
        db.execute('INSERT INTO route_entries VALUES (?,?,?,?,?,?)',
                   (x['service'], p['name'], p['manager'], p['method'], p['prefix'], p['target']))
    db.execute('INSERT INTO route_publications VALUES (?,?,?,?,?)',
               (x['service'], x['id'], change['name'], request,
                json.dumps(result, sort_keys=True, ensure_ascii=False)))
    return result
