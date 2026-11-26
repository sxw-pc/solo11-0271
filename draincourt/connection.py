from .store import one,rows
SCHEMA='''
CREATE TABLE IF NOT EXISTS endpoint_states(request TEXT PRIMARY KEY,pod TEXT,uid TEXT,status TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS connection_snapshots(id TEXT PRIMARY KEY,request TEXT,connection TEXT,kind TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS drain_cases(id TEXT PRIMARY KEY,request TEXT,at INTEGER,active INTEGER,status TEXT);
'''
def active(db,request):
    events=rows(db,'SELECT * FROM connection_snapshots WHERE request=? ORDER BY at,rowid',(request,))
    opened=set()
    for e in events:
        if e['kind']=='open':opened.add(e['connection'])
        elif e['kind']=='close':opened.discard(e['connection'])
    return opened
def handle(db,x):
    if x['op']=='endpoint-remove':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        if not r or r['uid']!=x['uid']:return {'decision':'endpoint_denied'}
        db.execute('INSERT INTO endpoint_states VALUES(?,?,?,?,?) ON CONFLICT(request) DO UPDATE SET status=excluded.status,at=excluded.at',(r['id'],r['pod'],r['uid'],'removed',x['at']))
        return {'decision':'endpoint_removed'}
    if x['op']=='connection-event':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        if not r or x['uid']!=r['uid'] or x['kind'] not in ('open','close') or x['at']<r['at']:
            return {'decision':'connection_denied'}
        if x['kind']=='open' and one(db,'SELECT status FROM endpoint_states WHERE request=?',(r['id'],)):
            return {'decision':'new_connection_blocked'}
        if x['kind']=='close' and x['connection'] not in active(db,r['id']):
            return {'decision':'unknown_connection'}
        db.execute('INSERT INTO connection_snapshots VALUES(?,?,?,?,?)',(x['id'],r['id'],x['connection'],x['kind'],x['at']))
        return {'decision':'connection_recorded','active':len(active(db,r['id']))}
    if x['op']=='drain-check':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        endpoint=one(db,'SELECT * FROM endpoint_states WHERE request=?',(x['request'],))
        if not r or not endpoint or endpoint['status']!='removed':return {'decision':'drain_unproven'}
        count=len(active(db,r['id']))
        db.execute('INSERT INTO drain_cases VALUES(?,?,?,?,?)',(x['id'],r['id'],x['at'],count,'drained' if count==0 else 'waiting'))
        return {'decision':'drained' if count==0 else 'connections_remaining','active':count}
    return {'decision':'unknown_operation'}
