import json
import tempfile
import unittest
from pathlib import Path

from draincourt.store import connect, one, replay, rows, run

ROOT = Path(__file__).resolve().parents[1]

CHANGE = {'name': 'receipts-v2', 'manager': 'release-team', 'method': '*',
          'prefix': '/pay/ledger/receipts', 'target': 'v2'}


def load(db, *names):
    results = {}
    for name in names:
        for e in json.loads((ROOT / 'fixtures' / name).read_text(encoding='utf-8')):
            results[e['input']['event_id']] = run(db, e['command'], e['input'])
    return results


def resolve(db, service, method, path, tag):
    return run(db, 'routes', {'op': 'resolve', 'event_id': tag, 'service': service,
                              'method': method, 'path': path})['target']


class AppliedFixtureTests(unittest.TestCase):
    """Both contracts applied on top of the shared actual-route baseline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'routes.sqlite')
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        self.results = load(self.db, 'route_reconcile.json', 'route_reconcile_apply.json')

    def test_contract_a_publish_result(self):
        published = self.results['apply-cooperative']
        self.assertEqual(published['decision'], 'published')
        self.assertEqual(published['installed'], 'receipts-v2')
        # Only the publisher's own wildcard region is rerouted; the ledger GET
        # region is preserved by exactly one generated protection route.
        self.assertEqual(published['taken_over'], [
            {'method': '*', 'prefix': '/pay/ledger/receipts', 'from': 'current',
             'to': 'v2', 'manager': 'release-team', 'basis': 'own'}])
        self.assertEqual(published['protected'], [
            {'name': 'protect-receipts-v2-GET-pay-ledger-receipts', 'method': 'GET',
             'prefix': '/pay/ledger/receipts', 'target': 'idempotent-ledger',
             'manager': 'ledger-team'}])

    def test_contract_a_resolution(self):
        self.assertEqual(self.results['resolve-a-post-receipts']['target'], 'v2')
        self.assertEqual(self.results['resolve-a-get-receipts']['target'], 'idempotent-ledger')
        self.assertEqual(self.results['resolve-a-get-receipt']['target'], 'idempotent-ledger')
        # Probes added after the change go through the landed routes.
        self.assertEqual(self.results['probe-a-get-receipts'],
                         {'decision': 'target_observed', 'target': 'idempotent-ledger'})
        self.assertEqual(self.results['probe-a-post-receipts'],
                         {'decision': 'target_observed', 'target': 'v2'})

    def test_contract_a_ledger_entry_untouched(self):
        ledger = one(self.db, "SELECT * FROM route_entries WHERE service='checkout' AND name='ledger'")
        self.assertEqual((ledger['manager'], ledger['method'], ledger['prefix'], ledger['target']),
                         ('ledger-team', 'GET', '/pay/ledger', 'idempotent-ledger'))

    def test_contract_b_handover(self):
        published = self.results['apply-handover']
        self.assertEqual(published['decision'], 'published')
        self.assertEqual(published['protected'], [])
        self.assertEqual(published['taken_over'], [
            {'method': 'GET', 'prefix': '/pay/ledger/receipts', 'from': 'idempotent-ledger',
             'to': 'v2', 'manager': 'ledger-team', 'basis': 'handover'},
            {'method': '*', 'prefix': '/pay/ledger/receipts', 'from': 'current',
             'to': 'v2', 'manager': 'release-team', 'basis': 'own'}])
        self.assertEqual(self.results['resolve-b-get-receipts']['target'], 'v2')
        self.assertEqual(self.results['resolve-b-post-receipts']['target'], 'v2')
        self.assertEqual(self.results['resolve-b-get-receipt']['target'], 'idempotent-ledger')
        self.assertEqual(self.results['probe-b-get-receipts']['target'], 'v2')
        self.assertEqual(self.results['probe-b-get-receipt']['target'], 'idempotent-ledger')
        self.assertEqual(rows(self.db, "SELECT * FROM route_entries "
                                       "WHERE service='checkout-handover' AND name LIKE 'protect-%'"), [])

    def test_unprobed_subpath_judged_by_intersection_not_samples(self):
        # The two registered probes still pass after the merge, so validating
        # only them would false-pass...
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger/receipt',
                                 'check-receipt'), 'idempotent-ledger')
        self.assertEqual(resolve(self.db, 'checkout', 'POST', '/pay/new',
                                 'check-payment'), 'current')
        # ...yet the unprobed subdomain keeps its ledger ownership under
        # contract A and moves only where the publisher may take over.
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger/receipts/42',
                                 'check-unprobed-get'), 'idempotent-ledger')
        self.assertEqual(resolve(self.db, 'checkout', 'POST', '/pay/ledger/receipts/42',
                                 'check-unprobed-post'), 'v2')

    def test_duplicate_and_conflicting_orders_do_not_alter_publication(self):
        self.assertEqual(self.results['apply-cooperative-duplicate'],
                         self.results['apply-cooperative'])
        conflict = self.results['apply-cooperative-conflict']
        self.assertEqual(conflict['decision'], 'publish_rejected')
        self.assertEqual(conflict['reason'], 'publication_conflict')
        entries = rows(self.db, "SELECT * FROM route_entries WHERE service='checkout'")
        self.assertEqual(len(entries), 4)
        self.assertEqual(len([e for e in entries if e['name'].startswith('protect-')]), 1)
        self.assertEqual(one(self.db, "SELECT COUNT(*) n FROM route_publications "
                                      "WHERE service='checkout'")['n'], 1)

    def test_unapproved_change_rejected_atomically(self):
        rejected = self.results['apply-unapproved']
        self.assertEqual(rejected['decision'], 'publish_rejected')
        self.assertEqual(rejected['reason'], 'approval_missing')
        # Previous actual flow kept: nothing installed, nothing published.
        self.assertEqual(self.results['resolve-a-get-v3']['target'], 'idempotent-ledger')
        self.assertIsNone(one(self.db, "SELECT 1 x FROM route_entries WHERE name='receipts-v3'"))
        self.assertIsNone(one(self.db, "SELECT 1 x FROM route_publications WHERE id='receipts-v3'"))

    def test_replay_verified(self):
        report = replay(self.path)
        self.assertTrue(report['verified'], report['divergences'])


class MergeConstraintTests(unittest.TestCase):
    """Authorization, evidence and atomicity constraints of the merge itself."""

    def setUp(self):
        self.db = connect(':memory:')
        self.addCleanup(self.db.close)
        load(self.db, 'route_reconcile.json')

    def publish(self, event_id, change=None, approval='__unset__'):
        order = {'op': 'publish', 'event_id': event_id, 'id': 'receipts-v2',
                 'service': 'checkout', 'change': change or dict(CHANGE)}
        if approval != '__unset__':
            order['approval'] = approval
        return run(self.db, 'gatewaymerge', order)

    def test_narrower_handover_scope_cannot_authorize_wider_reroute(self):
        result = self.publish('narrow', approval={
            'granted_by': 'ledger-team',
            'handover': [{'method': 'GET', 'prefix': '/pay/ledger/receipts/4'}]})
        self.assertEqual(result['decision'], 'publish_rejected')
        self.assertEqual(result['reason'], 'scope_not_taken_over')
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger/receipts/42',
                                 'narrow-get'), 'idempotent-ledger')
        self.assertEqual(resolve(self.db, 'checkout', 'POST', '/pay/ledger/receipts/42',
                                 'narrow-post'), 'current')
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM route_entries')['n'], 2)

    def test_wider_scope_than_change_is_rejected(self):
        result = self.publish('wide', approval={
            'granted_by': 'ledger-team',
            'handover': [{'method': 'GET', 'prefix': '/pay/ledger'}]})
        self.assertEqual(result['decision'], 'publish_rejected')
        self.assertEqual(result['reason'], 'scope_not_taken_over')
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger/receipts/42',
                                 'wide-get'), 'idempotent-ledger')

    def test_missing_or_wrong_approval_rejected(self):
        result = self.publish('no-approval')
        self.assertEqual(result['decision'], 'publish_rejected')
        self.assertEqual(result['reason'], 'approval_missing')
        result = self.publish('wrong-grantor', approval={'granted_by': 'release-team'})
        self.assertEqual(result['decision'], 'publish_rejected')
        self.assertEqual(result['reason'], 'approval_missing')
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM route_entries')['n'], 2)

    def test_publisher_cannot_rewrite_ledger_entry(self):
        result = self.publish('rewrite', change=dict(CHANGE, name='ledger'),
                              approval={'granted_by': 'ledger-team'})
        self.assertEqual(result['decision'], 'publish_rejected')
        self.assertEqual(result['reason'], 'route_name_conflict')
        ledger = one(self.db, "SELECT * FROM route_entries WHERE name='ledger'")
        self.assertEqual(ledger['target'], 'idempotent-ledger')

    def test_stale_probe_evidence_blocks_publish(self):
        run(self.db, 'routes', {'op': 'observe', 'event_id': 'base-moved', 'id': 'base-moved',
                                'service': 'checkout', 'name': 'base', 'manager': 'release-team',
                                'method': '*', 'prefix': '/pay', 'target': 'current-v2'})
        result = self.publish('stale', approval={'granted_by': 'ledger-team'})
        self.assertEqual(result['decision'], 'publish_rejected')
        self.assertEqual(result['reason'], 'evidence_mismatch')
        self.assertEqual(result['probe'], 'payment')
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM route_entries')['n'], 2)
        self.assertEqual(one(self.db, 'SELECT COUNT(*) n FROM route_publications')['n'], 0)

    def test_contract_a_preserves_every_prior_flow_outside_publisher_domain(self):
        methods = ['GET', 'POST', 'PUT', 'DELETE']
        paths = ['/pay', '/pay/new', '/pay/other', '/pay/ledger', '/pay/ledger/receipt',
                 '/pay/ledger/receipts', '/pay/ledger/receipts/42', '/pay/ledger/receipts/42/pdf']
        before = {(m, p): resolve(self.db, 'checkout', m, p, 'pre-{}-{}'.format(m, p))
                  for m in methods for p in paths}
        result = self.publish('apply', approval={'granted_by': 'ledger-team'})
        self.assertEqual(result['decision'], 'published')
        for (m, p), target in before.items():
            after = resolve(self.db, 'checkout', m, p, 'post-{}-{}'.format(m, p))
            if p.startswith('/pay/ledger/receipts') and m != 'GET':
                self.assertEqual(target, 'current')
                self.assertEqual(after, 'v2')
            else:
                self.assertEqual(after, target, (m, p))

    def test_contract_b_handover_transfers_exact_scope(self):
        result = self.publish('apply', approval={
            'granted_by': 'ledger-team',
            'handover': [{'method': 'GET', 'prefix': '/pay/ledger/receipts'}]})
        self.assertEqual(result['decision'], 'published')
        self.assertEqual(result['protected'], [])
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger/receipts/42',
                                 'b-get'), 'v2')
        self.assertEqual(resolve(self.db, 'checkout', 'POST', '/pay/ledger/receipts/42',
                                 'b-post'), 'v2')
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger/receipt',
                                 'b-singular'), 'idempotent-ledger')
        self.assertEqual(resolve(self.db, 'checkout', 'GET', '/pay/ledger',
                                 'b-ledger'), 'idempotent-ledger')


if __name__ == '__main__':
    unittest.main()
