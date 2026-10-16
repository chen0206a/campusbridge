from concurrent.futures import ThreadPoolExecutor

from campusbridge.contracts import Event, RunState
from tests.test_mysql_d1 import mysql_db, run_and_case


def test_parallel_mysql_batches_are_atomic_and_ordered(mysql_db):
    _,_,_=run_and_case(mysql_db)
    store,student,counselor=mysql_db[4:]
    cases=store.cases(counselor);run=store.case_detail(cases[0]['case_id'],counselor)['run_id']
    before=len(store.trace(run,counselor)['events'])
    def batch(index):
        store.audit_many(run,tuple(Event(state=RunState.QUEUED,code='BATCH_'+str(index)+'_'+str(n)) for n in range(4)))
    with ThreadPoolExecutor(max_workers=16) as pool:list(pool.map(batch,range(16)))
    events=store.trace(run,counselor)['events']
    assert len(events)==before+64
    assert [e['sequence'] for e in events]==list(range(1,len(events)+1))
    codes=[e['code'] for e in events[before:]]
    for offset in range(0,64,4):
        prefix=codes[offset].rsplit('_',1)[0]
        assert codes[offset:offset+4]==[prefix+'_'+str(n) for n in range(4)]
    assert all(e['state']=='ANALYZING' for e in events[before:])
