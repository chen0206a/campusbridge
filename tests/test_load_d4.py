import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from campusbridge.api import create_app
from campusbridge.diagnostics import process_metrics, register_metrics
from campusbridge.settings import Settings

spec=importlib.util.spec_from_file_location('d4_contracts',Path(__file__).parents[1]/'scripts/d4_contracts.py')
contracts=importlib.util.module_from_spec(spec);spec.loader.exec_module(contracts)


@pytest.mark.parametrize('url',['https://example.org','http://127.0.0.1@evil.test','http://127.0.0.1/api','http://localhost/?token=a'])
def test_load_rejects_remote_or_non_origin(url):
    with pytest.raises(ValueError): contracts.local_target(url)


def test_metrics_are_content_free_and_local_only(tmp_path):
    app=create_app(Settings(environment='test',database_path=tmp_path/'test.sqlite3'))
    register_metrics(app)
    with TestClient(app,client=('127.0.0.1',10)) as client:
        value=client.get('/ops/metrics').json()
        assert value['runtime']['active_jobs']==0
        assert 'password' not in json.dumps(value) and 'student_lan' not in json.dumps(value)
    with TestClient(app,client=('172.20.0.2',10)) as client:
        assert client.get('/ops/metrics',headers={'X-Real-IP':'127.0.0.1'}).status_code==403


def test_sse_requires_done_hash_and_no_error():
    text='已审核的回复'
    value={'run_id':'r','state':'ACCEPTED','message_id':'m','content_hash':hashlib.sha256(text.encode()).hexdigest()}
    frame=lambda kind,value:'event: '+kind+'\ndata: '+json.dumps(value,ensure_ascii=False)+'\n\n'
    stream=frame('token',{'text':text})+frame('done',value)
    assert contracts.checked_sse(stream)['reply']==text
    for bad in [frame('token',{'text':text}),frame('error',{})+stream,stream.replace(text,'篡改')]:
        with pytest.raises(ValueError):contracts.checked_sse(bad)


def test_latency_summary_includes_failures_and_nearest_rank():
    data=[{'kind':'request','elapsed_ms':n,'failed':n==4,'timeout':n==4} for n in [1,2,3,4]]
    value=contracts.summarize(data,2)
    assert value['p50_ms']==2 and value['p95_ms']==4
    assert value['rps']==2 and value['error_rate']==.25 and value['timeout_rate']==.25


def test_load_overlay_does_not_scale_or_disable_security():
    import yaml
    config=yaml.safe_load((Path(__file__).parents[1]/'deploy/compose.load.yaml').read_text())
    backend=config['services']['backend']
    assert backend['environment']['CAMPUSBRIDGE_MODEL_PROVIDER']=='mock'
    assert 'CAMPUSBRIDGE_REDIS_RATE_LIMIT_ENABLED' not in backend['environment']
    assert 'deploy' not in backend and 'replicas' not in backend
    assert int(backend['environment']['CAMPUSBRIDGE_MESSAGE_LIMIT'])<=1000


def test_memory_units_and_empty_summary():
    import sys
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    try:
        from report_load_d4 import memory_bytes
        assert memory_bytes('1.5GiB')==1.5*1024**3
        assert memory_bytes('2MB')==2000000
        with pytest.raises(ValueError):memory_bytes('2unknown')
        assert contracts.summarize([],1)['p95_ms'] is None
    finally:sys.path.pop(0)


def test_docker_redis_fixture_keeps_password_out_of_argv():
    spec=importlib.util.spec_from_file_location('redis_docker_test_server',Path(__file__).parents[1]/'scripts/redis_docker_test_server.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    argv,config=module.command('bind 127.0.0.1\nport 54321\nrequirepass synthetic-secret\n','docker')
    assert 'synthetic-secret' not in ' '.join(argv)
    assert '127.0.0.1:54321:54321' in argv and '--rm' in argv
    assert 'bind 0.0.0.0\n' in config and 'requirepass synthetic-secret' in config
    with pytest.raises(ValueError):module.command('bind 0.0.0.0\nport 54321\n','docker')
