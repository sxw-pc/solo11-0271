"""Offline clock bounds and observed exchange pairs between node agents."""
from .store import one

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_clocks (id TEXT PRIMARY KEY, lower INTEGER NOT NULL, upper INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS node_exchanges (
    id TEXT PRIMARY KEY, sender TEXT NOT NULL, other TEXT NOT NULL,
    sent INTEGER NOT NULL, arrived INTEGER NOT NULL, max_delay INTEGER NOT NULL
);
"""

ZERO = '*zero*'
_INF = 10 ** 18


def handle(db, x):
    if one(db, 'SELECT 1 FROM node_clocks WHERE id=?', (x['id'],)) or one(db, 'SELECT 1 FROM node_exchanges WHERE id=?', (x['id'],)):
        return {'decision': 'observation_id_conflict'}
    if x['op'] == 'clock':
        if x['lower'] > x['upper']:
            return {'decision': 'invalid_clock_bound'}
        db.execute('INSERT INTO node_clocks VALUES (?,?,?)', (x['id'], x['lower'], x['upper']))
        return {'decision': 'clock_noted'}
    if x['op'] == 'exchange':
        if (x['sender'] == x['other'] or x['max_delay'] < 0
                or not all(one(db, 'SELECT 1 FROM node_clocks WHERE id=?', (x[k],)) for k in ('sender', 'other'))):
            return {'decision': 'invalid_exchange'}
        db.execute('INSERT INTO node_exchanges VALUES (?,?,?,?,?,?)',
                   (x['id'], x['sender'], x['other'], x['sent'], x['arrived'], x['max_delay']))
        return {'decision': 'exchange_noted'}
    return {'decision': 'unknown_operation'}


def load_clocks(db, nodes):
    """Static offset ranges of the requested nodes; missing nodes return None."""
    out = {}
    for node in nodes:
        row = one(db, 'SELECT id, lower, upper FROM node_clocks WHERE id=?', (node,))
        out[node] = row
    return out


def load_exchanges(db, exchange_ids):
    """Fetch exchanges by id; absent ids are simply not in the result mapping."""
    out = {}
    for exchange_id in exchange_ids:
        row = one(db, 'SELECT * FROM node_exchanges WHERE id=?', (exchange_id,))
        if row:
            out[exchange_id] = row
    return out


def _floyd_warshall(edges, nodes):
    """All-pairs shortest paths of the difference-constraint graph.

    Every edge (u, v, w) encodes x_v - x_u <= w.  A finite dist[ZERO][v]
    always exists because ZERO reaches every node through the static ranges.
    dist[v][v] < 0 marks a negative cycle, i.e. contradictory evidence.
    ``pred`` supports negative-cycle reconstruction.
    """
    dist = {u: {v: _INF for v in nodes} for u in nodes}
    pred = {u: {v: None for v in nodes} for u in nodes}
    for node in nodes:
        dist[node][node] = 0
        pred[node][node] = node
    for u, v, w, origin in edges:
        if w < dist[u][v]:
            dist[u][v] = w
            pred[u][v] = u
    for k in nodes:
        dk = dist[k]
        for i in nodes:
            dik = dist[i][k]
            if dik == _INF:
                continue
            di = dist[i]
            pi = pred[i]
            for j in nodes:
                cand = dik + dk[j]
                if cand < di[j]:
                    di[j] = cand
                    pi[j] = pred[k][j]
    return dist, pred


def _negative_cycle(dist, pred, nodes, edge_origins):
    """Reconstruct one negative cycle and return the origins of its edges."""
    root = next((v for v in nodes if dist[v][v] < 0), None)
    if root is None:
        return []
    # Follow predecessors of root until a node repeats; the segment between
    # the two visits is a genuine cycle.
    order = []
    seen_at = {}
    cur = root
    while cur not in seen_at and cur is not None and len(order) <= len(nodes):
        seen_at[cur] = len(order)
        order.append(cur)
        cur = pred[root][cur]
    if cur is None or cur not in seen_at:
        return []
    cycle_nodes = order[seen_at[cur]:]
    origins = []
    for u, v in zip(cycle_nodes, cycle_nodes[1:] + [cycle_nodes[0]]):
        for origin in edge_origins.get((u, v), ()):
            if origin not in origins:
                origins.append(origin)
    return origins


def solve_offset_system(db, node_ids, authorized_exchange_ids, objective):
    """Bound an offset difference under one jointly feasible offset assignment.

    ``objective`` is (plus_node, minus_node): the optimised quantity is
    x_plus - x_minus.  Only static ranges of ``node_ids`` and the exchanges in
    ``authorized_exchange_ids`` constrain the same shared set of offsets; every
    edge in the returned graph is either a static bound or one authorized
    exchange.  Endpoints of the difference and all exchange participants are
    expected to already exist in node_clocks.
    """
    clocks = load_clocks(db, node_ids)
    missing = sorted(node for node, row in clocks.items() if row is None)
    if missing:
        return {'status': 'unknown_node', 'missing_nodes': missing}

    unknown = sorted(i for i in authorized_exchange_ids
                     if not one(db, 'SELECT 1 FROM node_exchanges WHERE id=?', (i,)))
    if unknown:
        return {'status': 'unknown_exchange', 'missing_exchanges': unknown}

    exchanges = load_exchanges(db, authorized_exchange_ids)
    nodes = [ZERO] + sorted(node_ids)
    edges = []
    edge_origins = {}

    def add_edge(u, v, w, origin):
        edges.append((u, v, w, origin))
        edge_origins.setdefault((u, v), [])
        if origin not in edge_origins[(u, v)]:
            edge_origins[(u, v)].append(origin)

    # Static ranges: lower_v <= x_v <= upper_v
    #   x_v <= upper_v        : ZERO -> v, weight upper_v
    #   x_v >= lower_v        : v -> ZERO, weight -lower_v
    for node in sorted(node_ids):
        clock = clocks[node]
        add_edge(ZERO, node, clock['upper'], ('clock', node))
        add_edge(node, ZERO, -clock['lower'], ('clock', node))
    # Each authorized exchange constrains the same shared offset assignment:
    #   0 <= (arrived + x_other) - (sent + x_sender) <= max_delay
    #   x_other - x_sender <= max_delay - arrived + sent
    #   x_sender - x_other <= arrived - sent
    for exchange_id in authorized_exchange_ids:
        ex = exchanges[exchange_id]
        sender, other = ex['sender'], ex['other']
        add_edge(sender, other, ex['max_delay'] - ex['arrived'] + ex['sent'],
                 ('exchange', exchange_id))
        add_edge(other, sender, ex['arrived'] - ex['sent'],
                 ('exchange', exchange_id))

    dist, pred = _floyd_warshall(edges, nodes)
    cycle = _negative_cycle(dist, pred, nodes, edge_origins)
    if cycle:
        return {'status': 'conflict',
                'conflicting_evidence': cycle,
                'exchanges': [origin[1] for origin in cycle if origin[0] == 'exchange'],
                'nodes': sorted(node_ids),
                'authorized_exchanges': list(authorized_exchange_ids)}

    plus_node, minus_node = objective
    # Difference constraints x_v - x_u <= w make each shortest distance
    # d(u, v) the tightest joint upper bound on x_v - x_u.  Hence for
    # D = x_plus - x_minus:
    #   D <= d(minus_node, plus_node)
    #  -D <= d(plus_node, minus_node), i.e. D >= -d(plus_node, minus_node)
    # Every bound already folds in the static ranges and every authorized
    # exchange, and both are attained by the feasible potentials below.
    maximum = dist[minus_node][plus_node]
    minimum = -dist[plus_node][minus_node]

    def assignment_from(source_dist):
        # x_v = dist[source][v] - dist[source][ZERO] is a feasible integer
        # potential of the whole system (triangle inequalities hold), shifted
        # so the synthetic zero node sits at 0; static ranges are then met.
        shift = source_dist[ZERO]
        values = {v: source_dist[v] - shift for v in node_ids}
        return values

    # The potential from the dist[minus_node] row tightens x_plus - x_minus
    # to its upper bound d(minus, plus) (maximum); the dist[plus_node] row
    # tightens the reversed difference to d(plus, minus), hence D reaches
    # -d(plus, minus) (minimum).  Each witness is one jointly feasible
    # assignment of every offset, never a stitch-up across two solutions.
    max_witness = assignment_from(dist[minus_node])
    min_witness = assignment_from(dist[plus_node])

    return {'status': 'feasible',
            'nodes': sorted(node_ids),
            'clocks': {n: {'lower': clocks[n]['lower'], 'upper': clocks[n]['upper']}
                       for n in sorted(node_ids)},
            'authorized_exchanges': list(authorized_exchange_ids),
            'exchanges': {i: dict(exchanges[i]) for i in authorized_exchange_ids},
            'edges': [{'u': u, 'v': v, 'weight': w, 'origin': list(origin)}
                      for (u, v, w, origin) in edges],
            'minimum': minimum,
            'maximum': maximum,
            'min_assignment': min_witness,
            'max_assignment': max_witness}
