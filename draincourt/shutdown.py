import json

from .store import one
from .connection import active
from .hookclock import compensation
SCHEMA='''
CREATE TABLE IF NOT EXISTS shutdown_orders(id TEXT PRIMARY KEY,request TEXT,uid TEXT,at INTEGER,mode TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS exit_receipts(id TEXT PRIMARY KEY,order_id TEXT,uid TEXT,at INTEGER,status TEXT);
CREATE TABLE IF NOT EXISTS loss_records(id TEXT PRIMARY KEY,request TEXT,connections INTEGER,at INTEGER,reason TEXT);
CREATE TABLE IF NOT EXISTS shutdown_clocks(order_id TEXT PRIMARY KEY,request TEXT,policy TEXT,expires INTEGER,effective INTEGER,compensation INTEGER,intervals TEXT);
'''
def handle(db,x):
    if x['op']=='shutdown':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        grace=one(db,'SELECT * FROM grace_contracts WHERE request=?',(x['request'],))
        if not r or not grace or x['uid']!=r['uid']:return {'decision':'shutdown_denied'}
        existing=one(db,'SELECT * FROM shutdown_orders WHERE request=?',(r['id'],))
        if existing:return {'decision':'shutdown_already_issued','order':existing['id']}
        policy=x.get('policy','hard')
        if policy not in ('hard','compensate'):return {'decision':'shutdown_policy_denied'}
        clock=compensation(db,r['id'],x['at'],grace['expires']) if policy=='compensate' else {'compensation':0,'intervals':[]}
        effective=grace['expires']+clock['compensation']
        drained=one(db,"SELECT * FROM drain_cases WHERE request=? AND status='drained' ORDER BY at DESC LIMIT 1",(r['id'],))
        hook=one(db,'SELECT * FROM hook_receipts WHERE request=?',(r['id'],))
        endpoint=one(db,'SELECT * FROM endpoint_states WHERE request=?',(r['id'],))
        safe=drained and hook and endpoint and endpoint['at']<=drained['at']<=x['at'] and hook['at']<=x['at'] and not active(db,r['id'],x['at']) and r['at']<=x['at']<effective
        forced=x['at']>=effective and x['at']>=r['at']
        basis={'policy':policy,'expires':grace['expires'],'effective':effective,'compensation':clock['compensation'],'intervals':clock['intervals']}
        if not safe and not forced:return {'decision':'shutdown_waiting',**basis} if 'policy' in x else {'decision':'shutdown_waiting'}
        mode='safe' if safe else 'forced'
        db.execute('INSERT INTO shutdown_orders VALUES(?,?,?,?,?,?)',(x['id'],r['id'],r['uid'],x['at'],mode,'issued'))
        db.execute('INSERT INTO shutdown_clocks VALUES(?,?,?,?,?,?,?)',(x['id'],r['id'],policy,grace['expires'],effective,clock['compensation'],json.dumps(clock['intervals'])))
        if forced:db.execute('INSERT INTO loss_records VALUES(?,?,?,?,?)',(x['loss_id'],r['id'],len(active(db,r['id'],x['at'])),x['at'],'grace_expired'))
        result={'decision':'shutdown_safe' if safe else 'shutdown_forced','connections':len(active(db,r['id'],x['at']))}
        if 'policy' in x:result.update(basis)
        return result
    if x['op']=='exit-ack':
        o=one(db,'SELECT * FROM shutdown_orders WHERE id=?',(x['order'],))
        if not o or o['status']!='issued' or x['uid']!=o['uid'] or x['at']<o['at']:
            return {'decision':'exit_denied'}
        db.execute('INSERT INTO exit_receipts VALUES(?,?,?,?,?)',(x['id'],o['id'],o['uid'],x['at'],'confirmed'))
        db.execute("UPDATE shutdown_orders SET status='exited' WHERE id=?",(o['id'],))
        db.execute("UPDATE termination_requests SET status='complete' WHERE id=?",(o['request'],))
        return {'decision':'pod_exited','mode':o['mode']}
    return {'decision':'unknown_operation'}
