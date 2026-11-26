from .store import one
SCHEMA='''
CREATE TABLE IF NOT EXISTS termination_requests(id TEXT PRIMARY KEY,pod TEXT,uid TEXT,generation INTEGER,at INTEGER,status TEXT);
CREATE TABLE IF NOT EXISTS grace_contracts(id TEXT PRIMARY KEY,request TEXT,expires INTEGER,level TEXT,status TEXT);
CREATE TABLE IF NOT EXISTS hook_receipts(id TEXT PRIMARY KEY,request TEXT,uid TEXT,generation INTEGER,at INTEGER,status TEXT);
'''
def handle(db,x):
    if x['op']=='termination-request':
        old=one(db,'SELECT MAX(generation) g FROM termination_requests WHERE pod=?',(x['pod'],))
        if old['g'] is not None and x['generation']<=old['g']:return {'decision':'stale_termination'}
        db.execute('INSERT INTO termination_requests VALUES(?,?,?,?,?,?)',(x['id'],x['pod'],x['uid'],x['generation'],x['at'],'requested'))
        return {'decision':'termination_requested'}
    if x['op']=='grace-bind':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        if not r or r['status']!='requested' or x['expires']<=r['at'] or x['level'] not in ('critical','standard'):
            return {'decision':'grace_denied'}
        db.execute('INSERT INTO grace_contracts VALUES(?,?,?,?,?)',(x['id'],r['id'],x['expires'],x['level'],'active'))
        return {'decision':'grace_active'}
    if x['op']=='hook-ack':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        if not r or x['uid']!=r['uid'] or x['generation']!=r['generation'] or x['at']<r['at']:
            return {'decision':'hook_denied'}
        db.execute('INSERT INTO hook_receipts VALUES(?,?,?,?,?,?)',(x['id'],r['id'],x['uid'],x['generation'],x['at'],'done'))
        return {'decision':'hook_done'}
    return {'decision':'unknown_operation'}
