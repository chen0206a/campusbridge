import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from campusbridge.api import create_app
from campusbridge.db import Database
from campusbridge.performance import Performance, instrument_database, install_profile
from campusbridge.settings import Settings


def test_bounded_samples_and_thread_origin():
    value=Performance(capacity=3);value.loop_thread=threading.get_ident()
    for n in range(8):value.observe('fixed',n/1000)
    result=value.snapshot()['durations']['fixed']
    assert result['count']==8 and result['retained_samples']==3 and result['on_loop_count']==8
    assert result['p99_ms']==7 and result['sum_ms']==28


def test_real_sql_and_transaction_timing(tmp_path):
    db=Database(tmp_path/'profile.sqlite3');profile=Performance()
    instrument_database(db,profile,'business')
    with db.write() as session:assert session.scalar(text('SELECT 1'))==1
    rows=profile.snapshot()['durations']
    assert rows['business.sql_execute']['count']>=2
    assert rows['business.write_transaction']['count']==1
    assert rows['business.pool_acquire']['count']>0
    assert 'SELECT' not in json.dumps(rows)
    db.close()


def test_profile_lifetime_exports_without_http(tmp_path):
    app=create_app(Settings(environment='test',database_path=tmp_path/'business.sqlite3'))
    path=tmp_path/'metrics.json';install_profile(app,path)
    with TestClient(app):
        deadline=time.monotonic()+3
        while not path.exists() and time.monotonic()<deadline:time.sleep(.02)
        value=json.loads(path.read_text())
        assert value['scope']=='single_backend_process' and 'performance' in value
        assert 'password' not in json.dumps(value)
    assert not any(t.name=='cb-profile-export' for t in threading.enumerate())


def test_profiling_environment_switch(monkeypatch):
    monkeypatch.setenv('CAMPUSBRIDGE_PERFORMANCE_ENABLED','true')
    assert Settings.from_environment().performance_enabled
