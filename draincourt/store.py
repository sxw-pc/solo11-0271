"""Transactional SQLite event ledger with full-table deterministic replay."""
import json, sqlite3, tempfile
from pathlib import Path
from contextlib import contextmanager

def connect(path):
    from . import MODULES
    db=sqlite3.connect(path,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, event_id TEXT UNIQUE, input TEXT, result TEXT)')
    for mod in MODULES.values():db.executescript(mod.SCHEMA)
    return db

def one(db,sql,args=()):
    r=db.execute(sql,args).fetchone()
    return dict(r) if r else None

def rows(db,sql,args=()):return [dict(x) for x in db.execute(sql,args)]

@contextmanager
def tx(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise

def run(db,command,x):
    from . import MODULES
    with tx(db):
        old=one(db,'SELECT * FROM events WHERE event_id=?',(x['event_id'],))
        encoded=json.dumps(x,sort_keys=True,ensure_ascii=False)
        if old:return json.loads(old['result']) if old['input']==encoded and old['command']==command else {'decision':'event_identity_conflict'}
        result=MODULES[command].handle(db,x)
        db.execute('INSERT INTO events(command,event_id,input,result) VALUES(?,?,?,?)',(command,x['event_id'],encoded,json.dumps(result,sort_keys=True,ensure_ascii=False)))
        return result

def replay(path):
    original=connect(path)
    target=connect(':memory:')
    divergence=[]
    events=rows(original,'SELECT * FROM events ORDER BY seq')
    for e in events:
        got=run(target,e['command'],json.loads(e['input']))
        if got!=json.loads(e['result']):divergence.append({'seq':e['seq'],'decision':got})
    names=[x['name'] for x in rows(original,"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if x['name']!='events']
    for name in names:
        left=[tuple(v) for v in original.execute('SELECT * FROM '+name+' ORDER BY rowid')]
        right=[tuple(v) for v in target.execute('SELECT * FROM '+name+' ORDER BY rowid')]
        if left!=right:divergence.append({'table':name})
    original.close();target.close()
    return {'decisions':len(events),'verified':not divergence,'divergences':divergence}
