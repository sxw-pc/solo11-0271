import json
import tempfile
import unittest
from pathlib import Path

from draincourt.hookclock import compensation
from draincourt.store import connect, one, replay, run

ROOT = Path(__file__).resolve().parents[1]
BASE_EVENTS = json.loads((ROOT / 'fixtures' / 'hookclock.json').read_text(encoding='utf-8'))
CONTRACT_B_EVENTS = json.loads((ROOT / 'fixtures' / 'hookclock_shutdown.json').read_text(encoding='utf-8'))
HARD_BASIS = {'policy': 'hard', 'expires': 40, 'effective': 40, 'compensation': 0, 'intervals': []}
COMPENSATED_BASIS = {'policy': 'compensate', 'expires': 40, 'effective': 46,
                     'compensation': 6, 'intervals': [[16, 18], [21, 25]]}


def feed(db, events):
    return [run(db, event['command'], event['input']) for event in events]


def shutdown(order_id, at, policy, loss_id):
    return {'event_id': order_id, 'op': 'shutdown', 'id': order_id, 'request': 'request',
            'uid': 'u1', 'at': at, 'policy': policy, 'loss_id': loss_id}


class HookClockShutdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'db.sqlite'
        self.db = connect(str(self.path))
        self.addCleanup(self.db.close)

    def test_contract_a_hard_deadline_forces_at_original_expiry(self):
        feed(self.db, BASE_EVENTS)
        waiting = run(self.db, 'shutdown', shutdown('shutdown-a-39', 39, 'hard', 'loss-a-39'))
        self.assertEqual(waiting, {'decision': 'shutdown_waiting', **HARD_BASIS})
        forced = run(self.db, 'shutdown', shutdown('shutdown-a', 44, 'hard', 'loss-a'))
        self.assertEqual(forced, {'decision': 'shutdown_forced', 'connections': 1, **HARD_BASIS})
        loss = one(self.db, "SELECT * FROM loss_records WHERE id='loss-a'")
        self.assertEqual((loss['connections'], loss['at'], loss['reason']), (1, 44, 'grace_expired'))
        clock = one(self.db, "SELECT * FROM shutdown_clocks WHERE order_id='shutdown-a'")
        self.assertEqual((clock['policy'], clock['expires'], clock['effective'], clock['compensation']),
                         ('hard', 40, 40, 0))
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM loss_records')['n'], 1)
        report = replay(str(self.path))
        self.assertTrue(report['verified'], report['divergences'])

    def test_contract_b_compensation_defers_forced_shutdown(self):
        feed(self.db, BASE_EVENTS)
        waiting, still_waiting = feed(self.db, CONTRACT_B_EVENTS[:2])
        self.assertEqual(waiting, {'decision': 'shutdown_waiting', **COMPENSATED_BASIS})
        self.assertEqual(still_waiting, {'decision': 'shutdown_waiting', **COMPENSATED_BASIS})
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM shutdown_orders')['n'], 0)
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM loss_records')['n'], 0)
        forced, duplicate, late_hold, after_late, exit_ack = feed(self.db, CONTRACT_B_EVENTS[2:])
        self.assertEqual(forced, {'decision': 'shutdown_forced', 'connections': 1, **COMPENSATED_BASIS})
        loss = one(self.db, "SELECT * FROM loss_records WHERE id='loss-b'")
        self.assertEqual((loss['connections'], loss['at'], loss['reason']), (1, 46, 'grace_expired'))
        clock = one(self.db, "SELECT * FROM shutdown_clocks WHERE order_id='shutdown-b-46'")
        self.assertEqual((clock['policy'], clock['expires'], clock['effective'], clock['compensation'],
                          json.loads(clock['intervals'])), ('compensate', 40, 46, 6, [[16, 18], [21, 25]]))
        self.assertEqual(duplicate, {'decision': 'shutdown_already_issued', 'order': 'shutdown-b-46'})
        self.assertEqual(late_hold, {'decision': 'hold_observed'})
        self.assertEqual(after_late, {'decision': 'shutdown_already_issued', 'order': 'shutdown-b-46'})
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM shutdown_orders')['n'], 1)
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM loss_records')['n'], 1)
        self.assertEqual(exit_ack, {'decision': 'pod_exited', 'mode': 'forced'})
        report = replay(str(self.path))
        self.assertTrue(report['verified'], report['divergences'])

    def test_two_contracts_on_independent_databases(self):
        other_path = Path(self.tmp.name) / 'other.sqlite'
        other = connect(str(other_path))
        self.addCleanup(other.close)
        feed(self.db, BASE_EVENTS)
        feed(other, BASE_EVENTS)
        hard = run(self.db, 'shutdown', shutdown('shutdown-a', 44, 'hard', 'loss-a'))
        compensated = run(other, 'shutdown', shutdown('shutdown-b', 44, 'compensate', 'loss-b'))
        self.assertEqual(hard, {'decision': 'shutdown_forced', 'connections': 1, **HARD_BASIS})
        self.assertEqual(compensated, {'decision': 'shutdown_waiting', **COMPENSATED_BASIS})
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM loss_records')['n'], 1)
        self.assertEqual(one(other, 'SELECT COUNT(*) n FROM loss_records')['n'], 0)
        self.assertTrue(replay(str(self.path))['verified'])
        self.assertTrue(replay(str(other_path))['verified'])

    def test_evidence_delivered_while_waiting_changes_next_adjudication(self):
        feed(self.db, BASE_EVENTS[:6])
        first = run(self.db, 'shutdown', shutdown('shutdown-early', 39, 'compensate', 'loss-early'))
        self.assertEqual(first, {'decision': 'shutdown_waiting', 'policy': 'compensate', 'expires': 40,
                                 'effective': 40, 'compensation': 0, 'intervals': []})
        feed(self.db, BASE_EVENTS[6:])
        second = run(self.db, 'shutdown', shutdown('shutdown-later', 44, 'compensate', 'loss-later'))
        self.assertEqual(second, {'decision': 'shutdown_waiting', **COMPENSATED_BASIS})
        forced = run(self.db, 'shutdown', shutdown('shutdown-final', 46, 'compensate', 'loss-final'))
        self.assertEqual(forced, {'decision': 'shutdown_forced', 'connections': 1, **COMPENSATED_BASIS})
        self.assertEqual(one(self.db, 'SELECT at FROM loss_records')['at'], 46)

    def test_late_maintenance_cannot_revive_terminated_instance(self):
        feed(self.db, BASE_EVENTS[:6])
        forced = run(self.db, 'shutdown', shutdown('shutdown-early', 44, 'compensate', 'loss-early'))
        self.assertEqual(forced, {'decision': 'shutdown_forced', 'connections': 1, 'policy': 'compensate',
                                  'expires': 40, 'effective': 40, 'compensation': 0, 'intervals': []})
        feed(self.db, BASE_EVENTS[6:])
        again = run(self.db, 'shutdown', shutdown('shutdown-again', 46, 'compensate', 'loss-again'))
        self.assertEqual(again, {'decision': 'shutdown_already_issued', 'order': 'shutdown-early'})
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM shutdown_orders')['n'], 1)
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM loss_records')['n'], 1)
        clock = one(self.db, "SELECT * FROM shutdown_clocks WHERE order_id='shutdown-early'")
        self.assertEqual((clock['effective'], clock['compensation']), (40, 0))
        report = replay(str(self.path))
        self.assertTrue(report['verified'], report['divergences'])

    def test_duplicate_delivery_adds_no_new_records(self):
        feed(self.db, BASE_EVENTS)
        feed(self.db, CONTRACT_B_EVENTS)
        forced_event = CONTRACT_B_EVENTS[2]
        self.assertEqual(run(self.db, forced_event['command'], forced_event['input']),
                         {'decision': 'shutdown_forced', 'connections': 1, **COMPENSATED_BASIS})
        hold_event = BASE_EVENTS[6]
        self.assertEqual(run(self.db, hold_event['command'], hold_event['input']),
                         {'decision': 'hold_observed'})
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM shutdown_orders')['n'], 1)
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM loss_records')['n'], 1)
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM clock_holds')['n'], 5)

    def test_compensation_interval_rules(self):
        feed(self.db, BASE_EVENTS)
        self.assertEqual(compensation(self.db, 'request', 13, 40), {'compensation': 0, 'intervals': []})
        self.assertEqual(compensation(self.db, 'request', 20, 40), {'compensation': 0, 'intervals': []})
        self.assertEqual(compensation(self.db, 'request', 26, 40),
                         {'compensation': 6, 'intervals': [[16, 18], [21, 25]]})
        self.assertEqual(compensation(self.db, 'request', 44, 40),
                         {'compensation': 6, 'intervals': [[16, 18], [21, 25]]})
        for hold_id, route, start, end in [('cap-primary', 'primary', 14, 24),
                                           ('cap-mirror', 'mirror', 18, 24)]:
            self.assertEqual(run(self.db, 'hookclock', {'event_id': hold_id, 'op': 'hold', 'id': hold_id,
                                                        'at': 30, 'route': route, 'start': start, 'end': end,
                                                        'kind': 'maintenance'}), {'decision': 'hold_observed'})
        self.assertEqual(compensation(self.db, 'request', 44, 40),
                         {'compensation': 10, 'intervals': [[14, 25]]})


if __name__ == '__main__':
    unittest.main()
