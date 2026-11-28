import json
import os
import tempfile
import unittest
from pathlib import Path

from draincourt.store import connect, one, replay, run

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
INCIDENT = FIXTURES / 'clock_incident.json'
PROOFS = FIXTURES / 'clock_order_proofs.json'


def load_incident(db):
    for item in json.loads(INCIDENT.read_text(encoding='utf8')):
        run(db, item['command'], item['input'])


def load_proofs(db):
    return [run(db, item['command'], item['input'])
            for item in json.loads(PROOFS.read_text(encoding='utf8'))]


def check_witness(proof, witness):
    """An endpoint witness must satisfy every static range and every
    authorized exchange, and its delta must equal the claimed endpoint."""
    offsets = witness['offsets']
    for node, bounds in proof['static_ranges'].items():
        assert bounds['lower'] <= offsets[node] <= bounds['upper']
    for check in witness['edge_checks']:
        assert check['ok'], check
    a = proof['milestones']['a']
    b = proof['milestones']['b']
    assert witness['global_times'][a['node']] == a['tick'] + offsets[a['node']]
    assert witness['global_times'][b['node']] == b['tick'] + offsets[b['node']]
    assert witness['delta'] == (witness['global_times'][b['node']]
                                - witness['global_times'][a['node']])


class ClockOrderProofTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)
        load_incident(self.db)
        self.contract_a, self.contract_b = load_proofs(self.db)

    def test_contract_a_static_ranges_only_is_undecided(self):
        proof = self.contract_a
        self.assertEqual(proof['decision'], 'order_undecided')
        diff = proof['difference']
        self.assertEqual((diff['minimum'], diff['maximum']), (-15, 9))
        self.assertTrue(diff['spans_both_signs'])
        self.assertTrue(diff['contains_zero'])
        # The relay exchanges were not authorized and must not enter the proof.
        self.assertEqual(proof['authorized_exchanges'], [])
        self.assertEqual(sorted(proof['ignored_exchanges']), ['e-to-r', 'r-to-n'])
        self.assertEqual(proof['nodes'], ['edge', 'runtime'])
        self.assertEqual(proof['milestones']['a']['tick'], 34)
        self.assertEqual(proof['milestones']['b']['tick'], 31)

    def test_contract_a_witnesses_flip_the_order(self):
        proof = self.contract_a
        lo, hi = proof['witnesses']['minimum'], proof['witnesses']['maximum']
        check_witness(proof, lo)
        check_witness(proof, hi)
        self.assertEqual(lo['delta'], -15)
        self.assertEqual(hi['delta'], 9)
        self.assertLess(lo['delta'], 0)
        self.assertGreater(hi['delta'], 0)
        self.assertEqual(set(lo['offsets']), {'edge', 'runtime'})

    def test_contract_b_authorized_relay_proves_after(self):
        proof = self.contract_b
        self.assertEqual(proof['decision'], 'proven_after')
        diff = proof['difference']
        self.assertEqual((diff['minimum'], diff['maximum']), (2, 7))
        self.assertGreater(diff['minimum'], 0)
        self.assertFalse(diff['contains_zero'])
        self.assertEqual(proof['authorized_exchanges'], ['e-to-r', 'r-to-n'])
        self.assertEqual(proof['ignored_exchanges'], [])
        self.assertEqual(proof['nodes'], ['edge', 'relay', 'runtime'])

    def test_contract_b_witnesses_jointly_satisfy_both_exchanges(self):
        proof = self.contract_b
        lo, hi = proof['witnesses']['minimum'], proof['witnesses']['maximum']
        check_witness(proof, lo)
        check_witness(proof, hi)
        self.assertEqual(lo['delta'], 2)
        self.assertEqual(hi['delta'], 7)
        for witness in (lo, hi):
            o = witness['offsets']
            # e-to-r: relay - edge in [2, 5]; r-to-n: runtime - relay in [3, 5]
            self.assertBetween(o['relay'] - o['edge'], 2, 5)
            self.assertBetween(o['runtime'] - o['relay'], 3, 5)
            for node in ('edge', 'relay', 'runtime'):
                self.assertBetween(o[node], 0, 12)

    def assertBetween(self, value, lower, upper):
        self.assertGreaterEqual(value, lower)
        self.assertLessEqual(value, upper)

    def test_unauthorized_exchange_never_tightens_the_proof(self):
        # Authorizing only r-to-n must produce a different, wider result than
        # the jointly-authorized contract B, proving the ignored exchange in
        # contract A is genuinely excluded from the extremal evidence.
        proof = run(self.db, 'milestone',
                    {'op': 'prove_order', 'id': 'partial', 'event_id': 'partial',
                     'incident': 'node-drain', 'a': 'a', 'b': 'b',
                     'authorized_exchanges': ['r-to-n']})
        self.assertEqual(proof['authorized_exchanges'], ['r-to-n'])
        self.assertEqual(proof['ignored_exchanges'], ['e-to-r'])
        self.assertNotEqual((proof['difference']['minimum'],
                             proof['difference']['maximum']), (2, 7))

    def test_contradictory_calibrations_conflict_without_blame(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        for node in ('u', 'v'):
            run(db, 'nodeclock', {'op': 'clock', 'id': node, 'event_id': node,
                                  'lower': 0, 'upper': 5})
        run(db, 'nodeclock', {'op': 'exchange', 'id': 'bad', 'event_id': 'bad',
                              'sender': 'u', 'other': 'v', 'sent': 10,
                              'arrived': 2, 'max_delay': 0})
        run(db, 'milestone', {'op': 'mark', 'id': 'm1', 'event_id': 'm1',
                              'incident': 'i', 'node': 'u', 'kind': 'k', 'tick': 1})
        run(db, 'milestone', {'op': 'mark', 'id': 'm2', 'event_id': 'm2',
                              'incident': 'i', 'node': 'v', 'kind': 'k', 'tick': 1})
        proof = run(db, 'milestone',
                    {'op': 'prove_order', 'id': 'p', 'event_id': 'p',
                     'incident': 'i', 'a': 'm1', 'b': 'm2',
                     'authorized_exchanges': ['bad']})
        self.assertEqual(proof['decision'], 'calibration_conflict')
        self.assertIn('bad', proof['conflicting_exchanges'])
        # No difference range or ordering verdict is emitted on conflict.
        self.assertNotIn('difference', proof)
        self.assertNotIn('witnesses', proof)

    def test_static_ranges_without_link_still_resolve_undecided(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        run(db, 'nodeclock', {'op': 'clock', 'id': 'u', 'event_id': 'u',
                              'lower': 0, 'upper': 4})
        run(db, 'nodeclock', {'op': 'clock', 'id': 'v', 'event_id': 'v',
                              'lower': 0, 'upper': 4})
        run(db, 'milestone', {'op': 'mark', 'id': 'm1', 'event_id': 'm1',
                              'incident': 'i', 'node': 'u', 'kind': 'k', 'tick': 10})
        run(db, 'milestone', {'op': 'mark', 'id': 'm2', 'event_id': 'm2',
                              'incident': 'i', 'node': 'v', 'kind': 'k', 'tick': 10})
        proof = run(db, 'milestone',
                    {'op': 'prove_order', 'id': 'p', 'event_id': 'p',
                     'incident': 'i', 'a': 'm1', 'b': 'm2',
                     'authorized_exchanges': []})
        self.assertEqual(proof['decision'], 'order_undecided')
        self.assertEqual((proof['difference']['minimum'],
                          proof['difference']['maximum']), (-4, 4))
        self.assertEqual(proof['nodes'], ['u', 'v'])

    def test_simultaneous_is_a_separate_verdict(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        for node in ('u', 'v'):
            run(db, 'nodeclock', {'op': 'clock', 'id': node, 'event_id': node,
                                  'lower': 0, 'upper': 0})
        run(db, 'milestone', {'op': 'mark', 'id': 'm1', 'event_id': 'm1',
                              'incident': 'i', 'node': 'u', 'kind': 'k', 'tick': 5})
        run(db, 'milestone', {'op': 'mark', 'id': 'm2', 'event_id': 'm2',
                              'incident': 'i', 'node': 'v', 'kind': 'k', 'tick': 5})
        proof = run(db, 'milestone',
                    {'op': 'prove_order', 'id': 'p', 'event_id': 'p',
                     'incident': 'i', 'a': 'm1', 'b': 'm2',
                     'authorized_exchanges': []})
        self.assertEqual(proof['decision'], 'proven_simultaneous')
        self.assertEqual(proof['difference']['minimum'], 0)
        self.assertEqual(proof['difference']['maximum'], 0)

    def test_denials_for_unknown_milestone_and_exchange(self):
        missing_ms = run(self.db, 'milestone',
                         {'op': 'prove_order', 'id': 'x1', 'event_id': 'x1',
                          'incident': 'node-drain', 'a': 'a', 'b': 'ghost',
                          'authorized_exchanges': []})
        self.assertEqual(missing_ms['decision'], 'proof_denied')
        self.assertEqual(missing_ms['reason'], 'unknown_milestone')
        missing_ex = run(self.db, 'milestone',
                         {'op': 'prove_order', 'id': 'x2', 'event_id': 'x2',
                          'incident': 'node-drain', 'a': 'a', 'b': 'b',
                          'authorized_exchanges': ['ghost']})
        self.assertEqual(missing_ex['decision'], 'proof_denied')
        self.assertEqual(missing_ex['reason'], 'unknown_exchange')

    def test_replay_preserves_conclusions_and_evidence_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'clocks.sqlite')
            db = connect(path)
            load_incident(db)
            before = load_proofs(db)
            db.close()
            result = replay(path)
            self.assertTrue(result['verified'], result)
            db2 = connect(path)
            self.addCleanup(db2.close)
            stored = [json.loads(row['result'])
                      for row in db2.execute(
                          "SELECT result FROM events WHERE command='milestone' "
                          "AND json_extract(result,'$.proof_id') IS NOT NULL ORDER BY seq")]
            self.assertEqual(stored, before)
            for proof in stored:
                self.assertIn('authorized_exchanges', proof)
                self.assertIn('ignored_exchanges', proof)
                self.assertIn('milestones', proof)


if __name__ == '__main__':
    unittest.main()
