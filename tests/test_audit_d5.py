import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from campusbridge.contracts import Event, RunState
from campusbridge.db import RunEvent
from campusbridge.knowledge import DemoKnowledge
from campusbridge.models import MockModel
from campusbridge.runtime import CampusRuntime, RunExecution


def execution(values):
    db,config,_,store,student,_=values
    conv=store.create_conversation(student)['conversation_id']
    run=store.enqueue(conv,student,'合成元数据事务测试','d5-audit')
    store.begin(run,10)
    runtime=CampusRuntime(store,MockModel(),DemoKnowledge(),config.model_copy(update={'orchestration':'dynamic'}))
    return RunExecution(runtime,run),run


def test_batch_binds_locked_state_and_keeps_contiguous_sequence(foundation):
    task,run=execution(foundation);store=foundation[3]
    before=store.trace(run,foundation[-1])['events']
    store.audit_many(run,[Event(state=RunState.QUEUED,code='BATCH_A'),Event(state=RunState.QUEUED,code='BATCH_B')])
    after=store.trace(run,foundation[-1])['events']
    assert [v['sequence'] for v in after]==list(range(1,len(after)+1))
    assert [v['code'] for v in after[-2:]]==['BATCH_A','BATCH_B']
    assert all(v['state']=='ANALYZING' for v in after[-2:])
    assert store.run_snapshot(run)['state']=='ANALYZING' and len(after)==len(before)+2


def test_invalid_batch_rolls_back_all_events(foundation):
    _,run=execution(foundation);db,_,_,store,_,_=foundation
    with db.sessions() as session:before=session.scalar(select(func.count()).select_from(RunEvent))
    item=Event(state=RunState.ANALYZING,code='DUPLICATE_ID')
    with pytest.raises(IntegrityError):store.audit_many(run,[item,item])
    with db.sessions() as session:assert session.scalar(select(func.count()).select_from(RunEvent))==before


def test_group_exception_preserves_partial_audit_and_restores_buffer(foundation):
    task,run=execution(foundation)
    with pytest.raises(ValueError):
        with task.audit_group():
            task.audit(Event(state=RunState.QUEUED,code='BEFORE_EXCEPTION'))
            raise ValueError('controlled failure')
    assert task.audit_buffer is None
    assert foundation[3].trace(run,foundation[-1])['events'][-1]['code']=='BEFORE_EXCEPTION'


def test_dynamic_audit_does_not_read_an_extra_run_snapshot(foundation,monkeypatch):
    task,run=execution(foundation)
    def forbidden(*args):raise AssertionError('Redundant state read')
    monkeypatch.setattr(foundation[3],'run_snapshot',forbidden)
    task.audit(Event(state=RunState.QUEUED,code='STATE_BOUND_IN_LOCK'))
    assert foundation[3].trace(run,foundation[-1])['events'][-1]['state']=='ANALYZING'
