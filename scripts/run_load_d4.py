"""Dedicated local Compose + Locust acceptance; never removes volumes or calls LLMs."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import httpx

from d4_contracts import delivered, local_target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--docker',default='docker')
    parser.add_argument('--load-python',required=True)
    parser.add_argument('--port',type=int,default=8081)
    parser.add_argument('--duration',type=int,default=45)
    parser.add_argument('--profile',action='store_true',help='D5 optional profiling; original D4 tasks remain unchanged')
    args=parser.parse_args()
    if not args.project.startswith('cb-d4-') or args.output.exists() or not 10<=args.duration<=120:
        parser.error('Use a new cb-d4-* project/output and 10..120 second load windows')
    args.root=args.root.resolve();args.output=args.output.resolve()
    if args.output.is_relative_to(args.root):parser.error('Raw evidence must be external to source')
    args.output.mkdir(parents=True)
    docker=str(Path(args.docker).resolve()) if Path(args.docker).is_file() else args.docker
    env=os.environ.copy()
    if Path(docker).is_file():env['PATH']=str(Path(docker).parent)+os.pathsep+env.get('PATH','')
    env.update(CAMPUSBRIDGE_MODEL_PROVIDER='mock',CAMPUSBRIDGE_API_KEY='',CAMPUSBRIDGE_IMAGE_TAG=args.project,
               CAMPUSBRIDGE_WEB_PORT=str(args.port))
    if args.profile: env['CAMPUSBRIDGE_PERFORMANCE_ENABLED']='true'
    prefix=[docker,'compose','--project-name',args.project,'-f','compose.yaml','-f','deploy/compose.load.yaml']
    host=local_target('http://127.0.0.1:'+str(args.port))
    result={'status':'RUNNING','project':args.project,'external_llm_calls':0,'model':'mock','phases':[],
            'scope':'single backend, real HTTP, co-located Windows load generator and WSL2 containers'}
    def save(name,value):
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    def run(command,*,input=None,timeout=240):
        p=subprocess.run(command,cwd=args.root,env=env,input=input,capture_output=True,text=True,encoding='utf8',timeout=timeout)
        if p.returncode:
            # Raw infrastructure logs stay external and are sanitized for archival.
            (args.output/'last-command-failure.txt').write_text(p.stdout+p.stderr,encoding='utf8')
            raise RuntimeError('COMMAND_FAILED:'+Path(command[0]).name)
        return p.stdout
    def compose(*parts,input=None,timeout=240):return run([*prefix,*parts],input=input,timeout=timeout)
    def execute(code):
        return json.loads(compose('exec','-T','backend','python','-c',code).strip().splitlines()[-1])
    def fixture(action):
        return json.loads(compose('exec','-T','backend','python','/checks/scripts/d4_fixture.py',action).strip().splitlines()[-1])
    def metrics(fresh=True):
        if args.profile and not fresh:
            return execute("import json,time; from pathlib import Path; d=json.loads(Path('/tmp/campusbridge-performance.json').read_text()); d['export_age_seconds']=time.monotonic()-d['exported_monotonic']; print(json.dumps(d))")
        return execute("import urllib.request,json; print(urllib.request.urlopen('http://127.0.0.1:8000/ops/metrics',timeout=5).read().decode())")
    def wait():
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            raw=compose('ps','--all','--format','json')
            try:
                data=json.loads(raw);rows=data if isinstance(data,list) else [data]
            except json.JSONDecodeError:rows=[json.loads(line) for line in raw.splitlines() if line]
            by={r['Service']:r for r in rows}
            if all(by.get(n,{}).get('Health')=='healthy' for n in ['mysql','redis','backend','frontend']):
                assert all(by.get(n,{}).get('ExitCode')==0 for n in ['bootstrap','migrate'])
                return by
            time.sleep(.5)
        raise RuntimeError('HEALTH_DEADLINE_EXCEEDED')
    def mysql_metrics():
        sql=("SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Max_used_connections',"
             "'Connections','Slow_queries','Aborted_connects','Innodb_row_lock_waits','Innodb_row_lock_time'); "
             "SELECT 'slow_log_rows',COUNT(*) FROM mysql.slow_log; "
             "SELECT 'slow_log_max_seconds',COALESCE(MAX(TIME_TO_SEC(query_time)),0) FROM mysql.slow_log;")
        raw=compose('exec','-T','mysql','sh','-c',
                    'MYSQL_PWD="$(cat /run/cb-root/password)" mysql -h 127.0.0.1 -u root --batch --skip-column-names',input=sql)
        return {line.split('\t')[0]:float(line.split('\t')[1]) for line in raw.splitlines() if '\t' in line}
    http_events=[]
    client=httpx.Client(base_url=host,trust_env=False,timeout=45)
    def request(method,path,*,body=None,auth=None,key=None,expect=200):
        headers=dict(auth or {})
        if key:headers['Idempotency-Key']=key
        started=time.perf_counter()
        response=client.request(method,path,json=body,headers=headers)
        http_events.append({'operation':path.split('/')[-1],'method':method,'status':response.status_code,
                            'expected_status':expect,'elapsed_ms':(time.perf_counter()-started)*1000})
        assert response.status_code==expect,(method,path,response.status_code,response.text[:150])
        return response.json() if response.content and 'json' in response.headers.get('content-type','') else response
    def login(username):
        value=request('POST','/api/auth/login',body={'username':username,'password':'CampusDemo-2026!'})
        return {'Authorization':'Bearer '+value['access_token']}
    def newconv(auth):return request('POST','/api/conversations',auth=auth,expect=201)['conversation_id']
    def chat(auth,conv,text,key=None):
        return delivered(request('POST',f'/api/conversations/{conv}/messages',auth=auth,body={'message':text},key=key or uuid.uuid4().hex))
    async def race(method,path,bodies,headers):
        async with httpx.AsyncClient(base_url=host,trust_env=False,timeout=60,limits=httpx.Limits(max_connections=32)) as http:
            async def one(body,header):
                started=time.perf_counter();r=await http.request(method,path,json=body,headers=header)
                return {'status':r.status_code,'body':r.json(),'elapsed_ms':(time.perf_counter()-started)*1000}
            return await asyncio.gather(*(one(b,h) for b,h in zip(bodies,headers)))
    try:
        assert not compose('ps','--all','--quiet').strip(),'TEST_PROJECT_ALREADY_EXISTS'
        result['docker']=json.loads(run([docker,'version','--format','{{json .}}']))
        info=json.loads(run([docker,'info','--format','{{json .}}']))
        result['engine_resources']={k:info[k] for k in ('NCPU','MemTotal','OSType','Architecture')}
        versions=run([args.load_python,'-m','pip','freeze'])
        (args.output/'load-dependencies.txt').write_text(versions,encoding='utf8')
        paths=[p for directory in ('src','scripts','deploy') for p in (args.root/directory).rglob('*')
               if p.is_file() and p.suffix in {'.py','.json','.yaml','.sh','.conf'} and '__pycache__' not in p.parts]
        paths += [args.root/n for n in ('Dockerfile','compose.yaml','requirements.lock','requirements-mysql.lock','requirements-redis.lock')]
        binding={p.relative_to(args.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        save('source-binding.json',binding)
        protocol={'users':[10,30,50,100],'seconds_per_phase':args.duration,'spawn_per_second':10,'stop_timeout_seconds':30,
                  'wait_seconds':[.1,.3],'request_timeout_seconds':30,'stop_scaling_error_rate':.05,'stop_scaling_p95_ms':20000,
                  'seed_users':100,'model':'mock','retrieval':'bm25',
                  'login_limit':1000,'login_peer_limit':10000,'message_limit':1000,
                  'includes_ramp_and_graceful_drain':True,'workload':'login/create then cyclic list,RAG,replay,detail,source,SSE',
                  'frozen_before_build_and_requests':True}
        protocol.update(profile=args.profile,workload_sha256=hashlib.sha256((args.root/'scripts/locustfile_d4.py').read_bytes()).hexdigest())
        save('protocol.json',protocol)
        started=time.perf_counter()
        (args.output/'build.txt').write_text(compose('up','--build','-d',timeout=1800),encoding='utf8')
        result['initial_services']=wait();result['startup_seconds']=time.perf_counter()-started
        execute("from pathlib import Path; import json; Path('/var/lib/campusbridge/knowledge/d4-test-only').write_text('"+args.project+"'); print(json.dumps({'synthetic_only':True}))")
        profiles=fixture('seed');save('profiles.json',profiles)
        a=login('d4_student_000');b=login('d4_student_001')
        c=[login('d4_counselor_'+str(n)) for n in range(4)]
        conv=newconv(a)
        before=metrics();first=chat(a,conv,'图书馆预约规则是什么');second=chat(a,conv,'图书馆预约规则是什么')
        assert first['citations'] and first['citations']==second['citations']
        after=metrics()
        assert after['redis'].get('cache_miss',0)>before['redis'].get('cache_miss',0)
        assert after['redis'].get('cache_hit',0)>before['redis'].get('cache_hit',0)
        result['cold_warm']={'before':before,'after':after,'requests':http_events[-2:],'same_citations':True}
        source=first['citations'][0]['source_id']
        request('GET',f'/api/conversations/{conv}',auth=b,expect=404)
        request('GET','/api/cases',auth=a,expect=403)
        request('GET','/api/knowledge/sources/'+source,auth=c[3],expect=404)
        request('GET',f'/api/runs/{first["run_id"]}/trace',auth=c[3],expect=404)
        # Nginx never forwards internal operations, even with forwarding spoofing.
        r=client.get('/ops/metrics',headers={'X-Real-IP':'127.0.0.1'})
        assert r.status_code!=200 or 'single_backend_process' not in r.text
        key='d4-duplicate-'+uuid.uuid4().hex
        headers={**a,'Idempotency-Key':key};body={'message':'最近课程安排比较忙'}
        responses=asyncio.run(race('POST',f'/api/conversations/{conv}/messages',[body]*16,[headers]*16))
        assert all(row['status']==200 for row in responses)
        assert len({row['body']['run_id'] for row in responses})==1
        assert len({row['body']['message_id'] for row in responses})==1
        duplicate_run=responses[0]['body']['run_id']
        result['duplicate_http']={'concurrent_requests':16,'statuses':[r['status'] for r in responses],
                                  'unique_runs':1,'unique_assistant_messages':1}
        risk=chat(a,conv,'我无法保证自己的安全')
        cases=request('GET','/api/cases',auth=c[0])['cases']
        details=[request('GET','/api/cases/'+v['case_id'],auth=c[0]) for v in cases]
        case=next(v for v in details if v['run_id']==risk['run_id'])
        target='/api/cases/'+case['case_id']
        request('GET',target,auth=c[3],expect=404)
        requests=[{'expected_version':0,'note':'D4 合成并发认领'}]*16
        race_headers=[{**c[n%3],'Idempotency-Key':'d4-claim-'+str(n)} for n in range(16)]
        responses=asyncio.run(race('POST',target+'/claim',requests,race_headers))
        assert sum(row['status']==200 for row in responses)==1
        assert all(row['status'] in (200,409) for row in responses)
        winner=next(n for n,row in enumerate(responses) if row['status']==200)%3
        replay=request('POST',target+'/claim',auth=race_headers[next(n for n,row in enumerate(responses) if row['status']==200)],
                       body={'expected_version':0,'note':'D4 合成并发认领'})
        # Header dictionary already contains the original Idempotency-Key.
        assert replay['version']==1
        request('POST',target+'/process',auth=c[(winner+1)%3],key='d4-wrong-owner',
                body={'expected_version':1,'note':'不应获得权限'},expect=403)
        request('POST',target+'/process',auth=c[winner],key='d4-process',body={'expected_version':1,'note':'已联系合成学生'})
        request('POST',target+'/close',auth=c[winner],key='d4-close',body={'expected_version':2,'note':'模拟处理完成'})
        detail=request('GET',target,auth=c[winner]);assert detail['state']=='CLOSED'
        result['claim_race']={'contenders':16,'success':1,'conflicts':15,'case_id':case['case_id'],'states':['OPEN','CLAIMED','IN_PROGRESS','CLOSED']}
        trace=request('GET',f'/api/runs/{risk["run_id"]}/trace',auth=c[winner]);save('risk-trace.json',trace)
        result['isolation_checks']={'cross_student':True,'cross_campus_case_source_trace':True,'student_case_denied':True,'ops_not_proxied':True}
        initial=fixture('snapshot');assert not any(initial['duplicate_groups'].values())
        for users in protocol['users']:
            phase=args.output/('users-'+str(users));phase.mkdir()
            samples=[];stop=threading.Event()
            def monitor():
                while not stop.is_set():
                    started=time.perf_counter()
                    sample={'monotonic_seconds':time.monotonic(),'component_errors':{}}
                    try:
                        stats=run([docker,'stats','--no-stream','--format','{{json .}}',*[
                            args.project+'-'+service+'-1' for service in ('backend','mysql','redis','frontend')]],timeout=30)
                        sample['docker_stats']=[json.loads(line) for line in stats.splitlines() if line]
                    except Exception as exc:sample['component_errors']['docker']=type(exc).__name__
                    for component,operation in [('app',lambda:metrics(fresh=False)),('mysql',mysql_metrics)]:
                        try:sample[component]=operation()
                        except Exception as exc:sample['component_errors'][component]=type(exc).__name__
                    sample['collection_seconds']=time.perf_counter()-started;samples.append(sample)
                    # Preserve every partial sample immediately, even if interrupted.
                    with (phase/'resources.jsonl').open('a',encoding='utf8') as journal:
                        journal.write(json.dumps(sample)+'\n');journal.flush()
                    stop.wait(2)
            monitor_thread=threading.Thread(target=monitor);monitor_thread.start()
            command=[args.load_python,'-m','locust','-f','scripts/locustfile_d5.py' if args.profile else 'scripts/locustfile_d4.py','--headless','--host',host,
                     '--users',str(users),'--spawn-rate','10','--run-time',str(args.duration)+'s','--stop-timeout','30',
                     '--csv',str(phase/'locust'),'--csv-full-history','--only-summary','--loglevel','WARNING']
            load_env={**env,'CB_D4_PROFILES':str(args.output/'profiles.json'),'CB_D4_OUTPUT':str(phase)}
            try:
                p=subprocess.run(command,cwd=args.root,env=load_env,capture_output=True,text=True,encoding='utf8',timeout=args.duration+100)
                (phase/'driver.txt').write_text(p.stdout+p.stderr,encoding='utf8')
            finally:
                stop.set();monitor_thread.join(timeout=60)
            save(phase.name+'/resources.json',samples)
            summary=json.loads((phase/'summary.json').read_text())
            summary['users']=users;summary['exit_code']=p.returncode
            summary['correctness']=fixture('snapshot');summary['before_final_restart_metrics']=metrics()
            assert not any(summary['correctness']['duplicate_groups'].values())
            assert summary['correctness']['non_mock_calls']==0
            assert not summary['invariant_failures']
            save(phase.name+'/summary.json',summary);result['phases'].append(summary)
            print(json.dumps({'users':users,'requests':summary['requests'],'rps':summary['rps'],'p95_ms':summary['p95_ms'],'failures':summary['failures']}),flush=True)
            if summary['error_rate'] is None or summary['error_rate']>.05 or summary['p95_ms']>20000:
                result['scale_stopped']='predeclared_error_or_latency_threshold';break
        # Outage: limit fails closed before enqueue, then Redis recovers without data loss.
        count=fixture('snapshot')['counts']['agent_runs']
        compose('stop','redis')
        try:
            value=request('POST',f'/api/conversations/{conv}/messages',auth=a,key='d4-redis-outage',body={'message':'故障测试合成消息'},expect=503)
            assert value['detail']=='RATE_LIMIT_UNAVAILABLE'
            assert fixture('snapshot')['counts']['agent_runs']==count
        finally:compose('start','redis');wait()
        result['redis_outage']={'status':503,'new_runs':0,'recovered':True}
        # Controlled durable interrupted fixture; not described as a generated model response.
        pending=fixture('interrupted');save('interrupted-fixture.json',pending)
        durable=fixture('snapshot');started=time.perf_counter();compose('restart','backend');wait()
        trace=request('GET','/api/runs/'+pending['run_id']+'/trace',auth=c[0])
        assert trace['state']=='FAILED' and trace['failure_code']=='SERVER_RESTARTED'
        assert next(v for v in trace['calls'] if v['call_id']==pending['model_call_id'])['status']=='INTERRUPTED'
        assert request('GET','/api/cases/'+pending['case_id'],auth=c[0])['state']=='OPEN'
        after=fixture('snapshot');assert after['counts']==durable['counts']
        result['backend_recovery']={'seconds':time.perf_counter()-started,'interrupted_run_failed':True,
                                    'call_interrupted':True,'risk_case_preserved':True,'counts_unchanged':True}
        save('interrupted-trace.json',trace)
        # Reusing original key after restart must not launch another execution.
        replay=chat(a,conv,body['message'],key)
        assert replay['run_id']==duplicate_run
        assert fixture('snapshot')['counts']==after['counts']
        result['replay_after_restart']={'new_runs':0,'new_model_calls':0}
        result['final_services']=wait();result['final_metrics']=metrics();result['mysql_final']=mysql_metrics()
        result['images']=json.loads(compose('images','--format','json'))
        assert all(hashlib.sha256((args.root/n).read_bytes()).hexdigest()==h for n,h in binding.items())
        result['source_unchanged_during_run']=True
        result['status']='PASSED' if len(result['phases'])==4 and all(p['failures']==0 for p in result['phases']) else 'COMPLETED_WITH_LIMITS'
    except Exception as exc:
        result.update(status='FAILED',failure_type=type(exc).__name__,reason=str(exc));raise
    finally:
        save('http-correctness.json',http_events);save('result.json',result);client.close()
        print(json.dumps({'status':result['status'],'output':str(args.output)}),flush=True)


if __name__=='__main__':main()
