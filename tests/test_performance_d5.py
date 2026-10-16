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


def test_queue_wait_is_distinct_from_sql_execution(tmp_path):
    from types import SimpleNamespace
    engine=create_engine('sqlite:///'+str(tmp_path/'pool.sqlite3'),pool_size=1,max_overflow=0)
    profile=Performance()
    instrument_database(SimpleNamespace(engine=engine,write=lambda:None),profile,'pool_test')
    started=threading.Event()
    def acquire():
        started.set()
        with engine.connect() as connection:assert connection.scalar(text('SELECT 1'))==1
    with engine.connect():
        worker=threading.Thread(target=acquire);worker.start()
        assert started.wait(1)
        time.sleep(.05)
    worker.join(2);assert not worker.is_alive()
    rows=profile.snapshot()['durations']
    assert rows['pool_test.pool_queue_wait']['sum_ms']>=10
    assert rows['pool_test.sql_execute']['count']==1
    engine.dispose()
