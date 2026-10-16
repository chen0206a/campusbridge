"""Unchanged D4 tasks plus co-located load-generator CPU/RSS/hub-lag telemetry."""
import json
from pathlib import Path
import time

import gevent
from locust import events
import psutil

import locustfile_d4 as workload

CampusUser=workload.CampusUser
client_samples=[]
watcher=None


@events.test_start.add_listener
def start(**kwargs):
    global watcher
    process=psutil.Process();process.cpu_percent()
    def watch():
        while True:
            expected=time.perf_counter()+.05
            gevent.sleep(.05)
            client_samples.append({'seconds':time.perf_counter()-workload.STARTED,
                'hub_lag_ms':max(0,(time.perf_counter()-expected)*1000),
                'cpu_percent':process.cpu_percent(),'rss_bytes':process.memory_info().rss,
                'threads':process.num_threads()})
    watcher=gevent.spawn(watch)


@events.quitting.add_listener
def stop(**kwargs):
    if watcher:watcher.kill()
    (workload.OUTPUT/'load-client.json').write_text(json.dumps(client_samples),encoding='utf8')
