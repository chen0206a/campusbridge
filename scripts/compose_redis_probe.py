"""Real Compose cache-hit and persisted atomic-limiter probe; Mock HTTP only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import httpx
from campusbridge.security import DEMO_PASSWORD


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker",default="docker")
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--project",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8080)
    args=parser.parse_args()
    if not args.project.startswith("cb-d3-") or args.output.exists(): parser.error("Use a cb-d3-* project and new output")
    env=os.environ.copy(); executable=str(Path(args.docker).resolve()) if Path(args.docker).is_file() else args.docker
    if Path(executable).is_file(): env["PATH"]=str(Path(executable).parent)+os.pathsep+env.get("PATH","")
    command=[executable,"compose","--project-name",args.project]
    def run(*parts):
        result=subprocess.run([*command,*parts],cwd=args.root,env=env,capture_output=True,text=True,encoding="utf8",timeout=90)
        if result.returncode: raise RuntimeError("REDIS_PROBE_COMMAND_FAILED")
        return result.stdout
    prologue=("from campusbridge.deployment import configure_credentials; configure_credentials(); "
        "from campusbridge.redis_support import RedisSupport,RateLimitError; from campusbridge.settings import Settings; "
        "import json; s=RedisSupport(Settings.from_environment()); ")
    def execute(code):
        return json.loads(run("exec","-T","backend","python","-c",prologue+code).strip().splitlines()[-1])
    def stats():
        return execute("d=s.client.info('commandstats'); print(json.dumps({k:d.get('cmdstat_'+k,{}).get('calls',0) for k in ['get','set']})); s.close()")
    assert execute("print(json.dumps({'provider':s.settings.model_provider})); s.close()")['provider'] == 'mock'
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}",timeout=60,trust_env=False) as client:
        logged=client.post("/api/auth/login",json={"username":"student_lan","password":DEMO_PASSWORD});logged.raise_for_status()
        headers={"Authorization":"Bearer "+logged.json()["access_token"]}
        conv=client.post("/api/conversations",headers=headers);conv.raise_for_status();conv=conv.json()["conversation_id"]
        def ask():
            response=client.post(f"/api/conversations/{conv}/messages",headers={**headers,"Idempotency-Key":uuid.uuid4().hex},
                                 json={"message":"图书馆预约规则是什么"});response.raise_for_status();return response.json()
        first=ask();before=stats();second=ask();after=stats()
        assert first['citations'] == second['citations'] and first['citations']
        assert after['get']-before['get'] == 1 and after['set']-before['set'] == 0
    identity=uuid.uuid4().hex
    key_code=("key=s.key('limit',{'policy':'fixed-window-v1','operation':'d3-persistence','identity':"+repr(identity)+
              ",'maximum':3,'window_seconds':600}); ")
    before=execute(key_code+"s.limit('d3-persistence',"+repr(identity)+",3,600); print(json.dumps({'count':int(s.client.get(key)),'ttl':s.client.ttl(key)})); s.close()")
    start=time.perf_counter();run("restart","redis")
    deadline=time.monotonic()+45
    while True:
        try:
            after=execute(key_code+"print(json.dumps({'count':int(s.client.get(key)),'ttl':s.client.ttl(key)})); s.close()")
            break
        except RuntimeError:
            if time.monotonic()>deadline: raise
            time.sleep(.2)
    assert after['count'] == before['count'] == 1 and 0 < after['ttl'] <= before['ttl']
    # The restored window permits exactly two remaining operations.
    execute("s.limit('d3-persistence',"+repr(identity)+",3,600); s.limit('d3-persistence',"+repr(identity)+",3,600); print(json.dumps({'allowed':2}));s.close()")
    blocked=execute("\ntry:\n s.limit('d3-persistence',"+repr(identity)+",3,600)\n print(json.dumps({'blocked':False}))\nexcept RateLimitError as limit_error:\n print(json.dumps({'blocked':True,'status':limit_error.status}))\nfinally:\n s.close()")
    assert blocked == {'blocked':True,'status':429}
    result={'passed':True,'external_llm_calls':0,'cache_hit':{'get_delta':1,'set_delta':0,'citations_equal':True},
        'limiter_before':before,'limiter_after_restart':after,'remaining_allowed':2,'next_blocked':blocked,
        'redis_restart_probe_seconds':time.perf_counter()-start}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(result))


if __name__=='__main__': main()
