"""Incident milestones as recorded on each node's own clock."""
from . import nodeclock
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_milestones (id TEXT PRIMARY KEY, incident TEXT NOT NULL, node TEXT NOT NULL,
    kind TEXT NOT NULL, tick INTEGER NOT NULL);
"""


def _verify_edges(edges, assignment):
    """Independently check an offset assignment against every constraint edge."""
    values = dict(assignment)
    values[nodeclock.ZERO] = 0
    checks = []
    for edge in edges:
        u, v, w = edge['u'], edge['v'], edge['weight']
        slack = w - (values[v] - values[u])
        checks.append({'u': u, 'v': v, 'weight': w, 'slack': slack, 'ok': slack >= 0})
    return checks


def _witness(solution, assignment, a_node, b_node, tick_a, tick_b):
    global_a = tick_a + assignment[a_node]
    global_b = tick_b + assignment[b_node]
    return {
        'offsets': dict(assignment),
        'global_times': {a_node: global_a, b_node: global_b},
        'delta': global_b - global_a,
        'edge_checks': _verify_edges(solution['edges'], assignment),
    }


def _build_proof(db, x):
    milestones = {}
    for label in ('a', 'b'):
        milestone_id = x[label]
        row = one(db, 'SELECT * FROM node_milestones WHERE id=?', (milestone_id,))
        if row is None:
            return {'decision': 'proof_denied', 'reason': 'unknown_milestone',
                    'unknown_milestone': milestone_id}
        milestones[label] = row
    a, b = milestones['a'], milestones['b']
    if a['incident'] != b['incident'] or a['incident'] != x.get('incident', a['incident']):
        return {'decision': 'proof_denied', 'reason': 'cross_incident',
                'incidents': [a['incident'], b['incident']]}

    authorized = list(x.get('authorized_exchanges', ()))
    node_ids = {a['node'], b['node']}
    for exchange_id in authorized:
        row = one(db, 'SELECT * FROM node_exchanges WHERE id=?', (exchange_id,))
        if row is not None:
            node_ids.add(row['sender'])
            node_ids.add(row['other'])

    solution = nodeclock.solve_offset_system(db, sorted(node_ids), authorized,
                                             (b['node'], a['node']))
    ignored = [row['id'] for row in db.execute('SELECT id FROM node_exchanges ORDER BY id')
               if row['id'] not in authorized]

    scope = {
        'proof_id': x['id'],
        'incident': a['incident'],
        'milestones': {
            'a': {'id': a['id'], 'node': a['node'], 'kind': a['kind'], 'tick': a['tick']},
            'b': {'id': b['id'], 'node': b['node'], 'kind': b['kind'], 'tick': b['tick']},
        },
        'nodes': sorted(node_ids),
        'static_ranges': {n: ({'lower': row['lower'], 'upper': row['upper']}
                              if row else None)
                          for n, row in nodeclock.load_clocks(
                              db, sorted(node_ids)).items()},
        'authorized_exchanges': authorized,
        'ignored_exchanges': ignored,
    }
    if solution['status'] in ('unknown_node', 'unknown_exchange'):
        return {'decision': 'proof_denied', 'reason': solution['status'], **scope,
                'detail': solution}
    if solution['status'] == 'conflict':
        return {'decision': 'calibration_conflict', **scope,
                'conflicting_evidence': solution['conflicting_evidence'],
                'conflicting_exchanges': solution['exchanges']}

    tick_delta = b['tick'] - a['tick']
    # solution bounds the offset difference x_b - x_a over one jointly
    # feasible assignment; global(b)-global(a) adds the local tick delta.
    delta_min = tick_delta + solution['minimum']
    delta_max = tick_delta + solution['maximum']
    witnesses = {
        'minimum': _witness(solution, solution['min_assignment'],
                            a['node'], b['node'], a['tick'], b['tick']),
        'maximum': _witness(solution, solution['max_assignment'],
                            a['node'], b['node'], a['tick'], b['tick']),
    }
    if delta_min > 0:
        decision = 'proven_after'
    elif delta_max < 0:
        decision = 'proven_before'
    elif delta_min == 0 and delta_max == 0:
        decision = 'proven_simultaneous'
    else:
        decision = 'order_undecided'

    return {
        'decision': decision,
        **scope,
        'difference': {'expression': 'global(b) - global(a)',
                       'local_tick_delta': tick_delta,
                       'minimum': delta_min, 'maximum': delta_max,
                       'contains_zero': delta_min <= 0 <= delta_max,
                       'spans_both_signs': delta_min < 0 < delta_max},
        'witnesses': witnesses,
    }


def handle(db, x):
    if x['op'] == 'prove_order':
        # Proofs are immutable ledger events but do not occupy node_milestones;
        # event-level idempotency is enforced by the shared events ledger.
        return _build_proof(db, x)
    if x['op'] != 'mark':
        return {'decision': 'unknown_operation'}
    if (not one(db, 'SELECT 1 FROM node_clocks WHERE id=?', (x['node'],))
            or one(db, 'SELECT 1 FROM node_milestones WHERE id=?', (x['id'],))):
        return {'decision': 'milestone_denied'}
    db.execute('INSERT INTO node_milestones VALUES (?,?,?,?,?)',
               (x['id'], x['incident'], x['node'], x['kind'], x['tick']))
    return {'decision': 'milestone_saved', 'local_tick': x['tick']}
