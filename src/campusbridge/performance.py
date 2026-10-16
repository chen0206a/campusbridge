"""Opt-in, bounded, content-free profiling. No SQL text or request payloads."""
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
import functools
import json
import math
from pathlib import Path
import threading
import time

from sqlalchemy import event


class Performance:
    def __init__(self, capacity=8192):
        self.capacity, self.lock = capacity, threading.Lock()
        self.rows = defaultdict(lambda: {"count":0, "sum_ms":0., "max_ms":0., "on_loop_count":0,
                                         "samples":deque(maxlen=capacity)})
        self.loop_thread = None

    def observe(self, name, seconds):
        value=max(0.,seconds*1000)
        with self.lock:
            row=self.rows[name];row['count']+=1;row['sum_ms']+=value
            row['max_ms']=max(row['max_ms'],value);row['samples'].append(value)
            row['on_loop_count']+=threading.get_ident()==self.loop_thread

    def snapshot(self):
        with self.lock:
            result={}
            for name,row in self.rows.items():
                values=sorted(row['samples'])
                result[name]={k:row[k] for k in ('count','sum_ms','max_ms','on_loop_count')}
                result[name].update(retained_samples=len(values),mean_ms=row['sum_ms']/row['count'],
                    p50_ms=values[max(0,math.ceil(len(values)*.5)-1)],
                    p95_ms=values[max(0,math.ceil(len(values)*.95)-1)],
                    p99_ms=values[max(0,math.ceil(len(values)*.99)-1)])
            return {"durations":result,"quantile_scope":"last_8192_samples_per_label; counts/sum/max cover full process"}

    @contextmanager
    def time(self, name):
        started=time.perf_counter()
        try:yield
        finally:self.observe(name,time.perf_counter()-started)


def instrument_database(db, profile, label):
    @event.listens_for(db.engine,'before_cursor_execute')
    def before(conn,cursor,statement,parameters,context,many):
        context.cb_profile_started=time.perf_counter()
    @event.listens_for(db.engine,'after_cursor_execute')
    def after(conn,cursor,statement,parameters,context,many):
        profile.observe(label+'.sql_execute',time.perf_counter()-context.cb_profile_started)
    original=db.write
    @contextmanager
    def write():
        with profile.time(label+'.write_transaction'):
            with original() as session:yield session
    db.write=write
    # Version-bound optional diagnostic hooks. Queue wait is measured separately
    # from connection creation/acquisition, not inferred from checkout gauges.
    pool=db.engine.pool
    if hasattr(pool,'_do_get'):
        acquire=pool._do_get
        def measured_acquire():
            with profile.time(label+'.pool_acquire'):return acquire()
        pool._do_get=measured_acquire
    queue=getattr(pool,'_pool',None)
    if queue is not None and hasattr(queue,'get'):
        get=queue.get
        def measured_get(block=True,timeout=None):
            if block:
                with profile.time(label+'.pool_queue_wait'):return get(block,timeout)
            return get(block,timeout)
        queue.get=measured_get


def install_profile(app, export_path='/tmp/campusbridge-performance.json'):
    profile=Performance();app.state.performance=profile
    runtime=app.state.runtime;runtime.performance=profile
    instrument_database(app.state.database,profile,'business')
    if app.state.knowledge_database is not None:
        instrument_database(app.state.knowledge_database,profile,'knowledge')
    for name in ('run_snapshot','audit','record','begin','start_model_call','finish_model_call',
                 'model_output_validity','persist_attention','commit_reply','deliverable','enqueue'):
        original=getattr(app.state.store,name)
        def wrap(original=original,name=name):
            @functools.wraps(original)
            def invoke(*args,**kwargs):
                with profile.time('store.'+name):return original(*args,**kwargs)
            return invoke
        setattr(app.state.store,name,wrap())

    original_lifetime=app.router.lifespan_context
    @asynccontextmanager
    async def lifetime(app):
        async with original_lifetime(app):
            profile.loop_thread=threading.get_ident()
            stop=threading.Event()
            async def lag():
                while True:
                    expected=time.perf_counter()+.05
                    await asyncio.sleep(.05)
                    profile.observe('event_loop_lag',time.perf_counter()-expected)
            def export():
                # Independent of HTTP/event-loop availability. One atomic snapshot
                # per second in container tmpfs; no business state is written.
                from campusbridge.diagnostics import process_metrics
                while not stop.is_set():
                    try:
                        value=process_metrics(app)
                        value.update(exported_monotonic=time.monotonic(),
                                     backend_threads=threading.active_count())
                        target=Path(export_path)
                        temporary=target.with_suffix('.new')
                        temporary.write_text(json.dumps(value),encoding='utf8');temporary.replace(target)
                    except Exception:
                        profile.observe('export_failure',0)
                    stop.wait(1)
            worker=threading.Thread(target=export,name='cb-profile-export',daemon=True)
            worker.start();task=asyncio.create_task(lag())
            try:yield
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                stop.set();await asyncio.to_thread(worker.join,3)
    app.router.lifespan_context=lifetime
