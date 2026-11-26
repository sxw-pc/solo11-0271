from .store import one,rows
SCHEMA='''
CREATE TABLE IF NOT EXISTS endpoint_states(request TEXT PRIMARY KEY,pod TEXT,uid TEXT,status TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS connection_snapshots(id TEXT PRIMARY KEY,request TEXT,connection TEXT,kind TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS drain_cases(id TEXT PRIMARY KEY,request TEXT,at INTEGER,active INTEGER,status TEXT);
'''
def active(db,request,at=None):
    events=rows(db,'SELECT * FROM connection_snapshots WHERE request=? AND (? IS NULL OR at<=?) ORDER BY at,rowid',(request,at,at))
    opened=set()
    for e in events:
        if e['kind']=='open':opened.add(e['connection'])
        elif e['kind']=='close':opened.discard(e['connection'])
    return opened
def handle(db,x):
    if x['op']=='endpoint-remove':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        latest=one(db,'SELECT MAX(at) at FROM connection_snapshots WHERE request=?',(x['request'],))
        if not r or r['uid']!=x['uid'] or x['at']<r['at'] or latest['at'] is not None and x['at']<latest['at']:
            return {'decision':'endpoint_denied'}
        db.execute('INSERT INTO endpoint_states VALUES(?,?,?,?,?) ON CONFLICT(request) DO UPDATE SET status=excluded.status,at=excluded.at',(r['id'],r['pod'],r['uid'],'removed',x['at']))
        return {'decision':'endpoint_removed'}
    if x['op']=='connection-event':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        if not r or x['uid']!=r['uid'] or x['kind'] not in ('open','close') or x['at']<r['at']:
            return {'decision':'connection_denied'}
        latest=one(db,'SELECT MAX(at) at FROM connection_snapshots WHERE request=?',(r['id'],))
        if latest['at'] is not None and x['at']<latest['at']:return {'decision':'stale_connection_event'}
        endpoint=one(db,'SELECT * FROM endpoint_states WHERE request=?',(r['id'],))
        if x['kind']=='open' and endpoint:
            return {'decision':'new_connection_blocked'}
        if x['kind']=='close' and x['connection'] not in active(db,r['id'],x['at']):
            return {'decision':'unknown_connection'}
        db.execute('INSERT INTO connection_snapshots VALUES(?,?,?,?,?)',(x['id'],r['id'],x['connection'],x['kind'],x['at']))
        return {'decision':'connection_recorded','active':len(active(db,r['id'],x['at']))}
    if x['op']=='drain-check':
        r=one(db,'SELECT * FROM termination_requests WHERE id=?',(x['request'],))
        endpoint=one(db,'SELECT * FROM endpoint_states WHERE request=?',(x['request'],))
        if not r or not endpoint or endpoint['status']!='removed' or x['at']<endpoint['at']:
            return {'decision':'drain_unproven'}
        count=len(active(db,r['id'],x['at']))
        db.execute('INSERT INTO drain_cases VALUES(?,?,?,?,?)',(x['id'],r['id'],x['at'],count,'drained' if count==0 else 'waiting'))
        return {'decision':'drained' if count==0 else 'connections_remaining','active':count}
    return {'decision':'unknown_operation'}
