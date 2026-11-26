import argparse,json
from pathlib import Path
from .store import connect,run,replay,rows
from . import MODULES

def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=[*MODULES,'demo','replay','report'])
    p.add_argument('--db',default='demo.sqlite')
    p.add_argument('--fixture')
    p.add_argument('--op')
    p.add_argument('--input')
    a=p.parse_args()
    if a.command=='replay':
        result=replay(a.db)
    else:
        db=connect(a.db)
        if a.command=='report':result=rows(db,'SELECT * FROM events ORDER BY seq')
        elif a.command=='demo':
            result=[]
            for path in [Path('fixtures')/'path.json']:
                for item in json.loads(path.read_text(encoding='utf-8')):
                    result.append({'command':item['command'],'result':run(db,item['command'],item['input'])})
        else:
            if a.fixture: commands=json.loads(Path(a.fixture).read_text(encoding='utf-8'))
            elif a.input: commands=[json.loads(a.input)]
            else:p.error('provide --fixture or --input')
            if a.op:
                for x in commands:x['op']=a.op
            result=[run(db,x['command'],x['input']) if 'command' in x else run(db,a.command,x) for x in commands]
        db.close()
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
    if a.command=='replay' and not result['verified']:raise SystemExit(2)

if __name__=='__main__':main()
