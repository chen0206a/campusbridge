"""Offline dynamic plan/trace examples and human-case lifecycle."""
import argparse
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from campusbridge.api import create_app
from campusbridge.models import MockModel
from campusbridge.security import DEMO_PASSWORD
from campusbridge.settings import Settings

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise SystemExit('Choose a new demo output directory.')
    args.output.mkdir(parents=True)
    cases={'ordinary':'我想梳理今日学习安排','rag':'图书馆座位预约需要做什么？',
           'human':'我想找辅导员谈谈适应校园的困难','risk':'我无法保证自己的安全，还想了解图书馆预约。'}
    summary={'model_provider':'mock','orchestration':'dynamic','external_model_calls':0,'scenarios':[]}
    with tempfile.TemporaryDirectory(prefix='campusbridge-showcase-') as temporary:
        config=Settings(environment='test',database_path=Path(temporary)/'demo.sqlite3',orchestration='dynamic')
        with TestClient(create_app(config,model=MockModel())) as client:
            def login(name):
                result=client.post('/api/auth/login',json={'username':name,'password':DEMO_PASSWORD});result.raise_for_status()
                return {'Authorization':'Bearer '+result.json()['access_token']}
            student,counselor=login('student_lan'),login('counselor_yu')
            for name,message in cases.items():
                conversation=client.post('/api/conversations',headers=student).json()['conversation_id']
                response=client.post(f'/api/conversations/{conversation}/stream',headers={**student,'Idempotency-Key':'showcase-'+name},json={'message':message});response.raise_for_status()
                run=response.headers['x-run-id'];reply=client.get('/api/runs/'+run,headers=student).json()
                trace=client.get('/api/runs/'+run+'/trace',headers=counselor).json()
                assert reply['state']=='ACCEPTED' and 'event: done' in response.text
                (args.output/(name+'.json')).write_text(json.dumps({'reply':reply,'trace':trace},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
                final_plan=next(e['plan'] for e in reversed(trace['events']) if e['code']=='PLAN_TERMINAL')
                summary['scenarios'].append({'name':name,'state':reply['state'],'tasks':len(final_plan['tasks'])})
            support=client.get('/api/cases',headers=counselor).json()['cases'][0];states=[support['state']]
            for version,action in enumerate(['claim','process','close']):
                response=client.post(f"/api/cases/{support['case_id']}/{action}",headers={**counselor,'Idempotency-Key':'showcase-'+action},json={'expected_version':version,'note':'虚构演示：工作人员记录并确认下一步支持安排。'});response.raise_for_status();states.append(response.json()['state'])
            assert states==['OPEN','CLAIMED','IN_PROGRESS','CLOSED'];summary['case_states']=states
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
