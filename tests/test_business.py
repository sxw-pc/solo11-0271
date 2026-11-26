import json,tempfile,unittest
from pathlib import Path
import draincourt
from draincourt.store import connect,run,replay,one,rows

ROOT=Path(__file__).resolve().parents[1]
class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';self.db=connect(str(self.path))
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def feed(self,name):
        commands=json.loads((ROOT/'fixtures'/name).read_text(encoding='utf-8'))
        return [run(self.db,v['command'],v['input']) for v in commands]
    def test_end_to_end_persists_independent_decisions(self):
        result=self.feed('path.json')
        self.assertGreaterEqual(len(result),10)
        self.assertEqual(one(self.db,"SELECT status FROM termination_requests WHERE id='request'")['status'],'complete')
        self.assertEqual(one(self.db,"SELECT mode FROM shutdown_orders WHERE id='shutdown'")['mode'],'safe')
    def test_failure_changes_result_without_erasing_prior_state(self):
        results=self.feed('path.json')
        self.assertIn('hook_denied',[r['decision'] for r in results])
        self.assertTrue(rows(self.db,'SELECT * FROM events'))
    def test_replay_and_event_conflict(self):
        self.feed('path.json')
        event=json.loads((ROOT/'fixtures'/'path.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(run(self.db,event['command'],event['input']),json.loads(one(self.db,'SELECT result FROM events WHERE event_id=?',(event['input']['event_id'],))['result']))
        changed=dict(event['input'],op='changed')
        self.assertEqual(run(self.db,event['command'],changed)['decision'],'event_identity_conflict')
        report=replay(str(self.path))
        self.assertTrue(report['verified'],report['divergences'])
    def test_six_fixtures_are_runnable(self):
        for command in ['termination', 'connection', 'shutdown']:
            for kind in ('normal','failure'):
                with self.subTest(command=command,kind=kind):
                    db=connect(':memory:')
                    for v in json.loads((ROOT/'fixtures'/f'{command}_{kind}.json').read_text(encoding='utf-8')):
                        run(db,v['command'],v['input'])
                    self.assertGreaterEqual(one(db,'SELECT COUNT(*) n FROM events')['n'],1)
                    db.close()
    def test_module_origin(self):
        self.assertEqual(Path(draincourt.__file__).resolve().parents[1],ROOT)
    def test_forced_shutdown_records_live_connection_loss(self):
        events=json.loads((ROOT/'fixtures'/'path.json').read_text(encoding='utf-8'))
        for event in events[:6]:run(self.db,event['command'],event['input'])
        self.assertEqual(run(self.db,'shutdown',{'event_id':'early','op':'shutdown','id':'early','request':'request','uid':'u1','at':20})['decision'],'shutdown_waiting')
        forced=run(self.db,'shutdown',{'event_id':'forced','op':'shutdown','id':'forced','request':'request','uid':'u1','at':40,'loss_id':'loss'})
        self.assertEqual(forced,{'decision':'shutdown_forced','connections':1})
        self.assertEqual(one(self.db,"SELECT connections FROM loss_records WHERE id='loss'")['connections'],1)
        self.assertEqual(run(self.db,'shutdown',{'event_id':'forced-again','op':'shutdown','id':'forced-again','request':'request','uid':'u1','at':41,'loss_id':'loss-again'})['decision'],'shutdown_already_issued')
        self.assertEqual(one(self.db,'SELECT COUNT(*) n FROM loss_records')['n'],1)
    def test_unknown_close_cannot_forge_drained_state(self):
        events=json.loads((ROOT/'fixtures'/'path.json').read_text(encoding='utf-8'))
        for event in events[:6]:run(self.db,event['command'],event['input'])
        self.assertEqual(run(self.db,'connection',{'event_id':'fake-close','op':'connection-event','id':'fake-close','request':'request','uid':'u1','connection':'not-c1','kind':'close','at':15})['decision'],'unknown_connection')
        self.assertEqual(run(self.db,'connection',{'event_id':'still-open','op':'drain-check','id':'still-open','request':'request','at':16})['decision'],'connections_remaining')

print('module_file',draincourt.__file__)
