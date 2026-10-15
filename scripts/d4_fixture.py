"""Synthetic data and read-only checks for a dedicated D4 Compose project.

Invoked explicitly inside the test container, never on startup or production.
No tokens, passwords or student text are serialized.
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import func, select, text

from campusbridge.contracts import Identity, RiskOutput, RiskLevel, identifier
from campusbridge.db import Account, AgentRun, CaseEvent, Database, Message, ModelCall, RiskEvent, Student, SupportCase
from campusbridge.deployment import configure_credentials
from campusbridge.security import DEMO_PASSWORD, password_hash
from campusbridge.settings import Settings
from campusbridge.store import Store


def database():
    configure_credentials()
    config = Settings.from_environment()
    # Requires the private explicit test mount and an independently created marker.
    if config.model_provider != "mock" or not Path('/var/lib/campusbridge/knowledge/d4-test-only').is_file():
        raise RuntimeError('D4_SYNTHETIC_PROJECT_REQUIRED')
    return Database.business(config)


def seed(db, count):
    if not 1 <= count <= 100: raise ValueError('MAXIMUM_100_SYNTHETIC_USERS')
    profiles = []
    encoded = password_hash(DEMO_PASSWORD)
    with db.sessions.begin() as session:
        if session.scalar(select(func.count()).select_from(Account).where(Account.username.like('d4_%'))):
            raise ValueError('D4_SEED_ALREADY_PRESENT')
        for index in range(count+4):
            student = index < count
            username = 'd4_student_%03d'%index if student else 'd4_counselor_%d'%(index-count)
            campus = 'fictional-campus-a' if index != count+3 else 'd4-isolated-campus'
            account = Account(id=identifier(), username=username, password_hash=encoded,
                              role='student' if student else 'counselor', campus_id=campus)
            session.add(account);session.flush()
            if student:
                session.add(Student(id=account.id, display_name='合成负载学生%03d'%index, synthetic=True))
                profiles.append({'username':username,'campus_id':campus})
    return profiles


def snapshot(db):
    with db.sessions() as session:
        duplicates={}
        for model,columns in [(AgentRun,('student_id','operation_key')), (CaseEvent,('case_id','operation_key')),
                              (Message,('conversation_id','order_index')), (RiskEvent,('run_id',)),
                              (SupportCase,('risk_event_id',)), (ModelCall,('run_id','call_index'))]:
            statement=select(func.count()).select_from(model).group_by(*(getattr(model,n) for n in columns)).having(func.count()>1)
            duplicates[model.__tablename__]=len(session.execute(statement).all())
        return {'counts':{model.__tablename__:session.scalar(select(func.count()).select_from(model))
                          for model in (Account,AgentRun,Message,RiskEvent,SupportCase,CaseEvent,ModelCall)},
                'duplicate_groups':duplicates,
                'run_states':dict(session.execute(select(AgentRun.state,func.count()).group_by(AgentRun.state)).all()),
                'non_mock_calls':session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.provider!='mock'))}


def interrupted(db):
    store=Store(db)
    with db.sessions() as session:
        account=session.scalar(select(Account).where(Account.username=='d4_student_000'))
        actor=Identity(account_id=account.id,role=account.role,campus_id=account.campus_id)
    conv=store.create_conversation(actor)['conversation_id']
    run=store.enqueue(conv,actor,'D4 合成恢复夹具：需要人工关注。','d4-interrupted-fixture')
    case=store.persist_attention(run,RiskOutput(level=RiskLevel.HIGH,needs_human_support=True,reason_codes=('EXPLICIT_DANGER',)))
    call=store.start_model_call(run,__import__('campusbridge.contracts',fromlist=['AgentName']).AgentName.RESPONSE,
                                6,'mock','controlled-interrupted-fixture')
    return {'run_id':run,'case_id':case,'model_call_id':call,'fixture':'persisted_inflight_not_an_actual_model_call'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('seed','snapshot','interrupted'))
    parser.add_argument('--count',type=int,default=100)
    args=parser.parse_args()
    db=database()
    try:
        result=seed(db,args.count) if args.command=='seed' else snapshot(db) if args.command=='snapshot' else interrupted(db)
        print(json.dumps(result,ensure_ascii=False))
    finally: db.close()


if __name__=='__main__': main()
