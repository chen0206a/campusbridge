"""Offline D5 profiling and paired load comparison from immutable samples."""
import argparse
import hashlib
import json
from pathlib import Path

from d4_contracts import percentile
from report_load_d4 import memory_bytes


def extract(root):
    root=Path(root);result=json.loads((root/'result.json').read_text(encoding='utf8'))
    phases=[]
    for declared in result['phases']:
        folder=root/('users-'+str(declared['users']))
        requests=[json.loads(line) for line in (folder/'requests.jsonl').read_text(encoding='utf8').splitlines()]
        samples=json.loads((folder/'resources.json').read_text(encoding='utf8'))
        client=json.loads((folder/'load-client.json').read_text(encoding='utf8')) if (folder/'load-client.json').is_file() else []
        final=declared['before_final_restart_metrics']
        services={}
        for sample in samples:
            for row in sample.get('docker_stats',[]):
                service=row['Name'].removeprefix(result['project']+'-').removesuffix('-1')
                item=services.setdefault(service,{'cpu_max_percent':0,'rss_max_bytes':0})
                item['cpu_max_percent']=max(item['cpu_max_percent'],float(row['CPUPerc'].rstrip('%')))
                item['rss_max_bytes']=max(item['rss_max_bytes'],memory_bytes(row['MemUsage'].split('/')[0]))
        phase={k:declared[k] for k in ('users','requests','rps','p50_ms','p95_ms','failures','error_rate','timeouts','observed_seconds')}
        phase.update(p99_ms=percentile([r['elapsed_ms'] for r in requests],.99),resources=services,
            performance=final.get('performance',{}),monitor_samples=len(samples),
            monitor_failures=sum(bool(s.get('component_errors') or s.get('monitor_error')) for s in samples),
            monitor_journal_lines=len((folder/'resources.jsonl').read_text().splitlines()) if (folder/'resources.jsonl').is_file() else None,
            exported_snapshot_age_max_seconds=max((s.get('app',{}).get('export_age_seconds',0) for s in samples),default=0),
            backend_threads_max=max((s.get('app',{}).get('backend_threads',0) for s in samples),default=0),
            database_counts=declared['correctness']['counts'],run_states=declared['correctness']['run_states'],
            client={'cpu_mean_percent':sum(v['cpu_percent'] for v in client)/len(client) if client else None,
                    'rss_max_bytes':max((v['rss_bytes'] for v in client),default=0),
                    'hub_lag_p99_ms':percentile([v['hub_lag_ms'] for v in client],.99),
                    'threads_max':max((v['threads'] for v in client),default=0)},
            mysql_final_sample=next((s['mysql'] for s in reversed(samples) if 'mysql' in s),{}))
        assert phase['requests']==len(requests)
        assert phase['monitor_journal_lines'] in (None,len(samples))
        phases.append(phase)
    return {'status':result['status'],'phases':phases,'protocol':json.loads((root/'protocol.json').read_text()),
            'source_binding_sha256':hashlib.sha256((root/'source-binding.json').read_bytes()).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--optimized',type=Path)
    parser.add_argument('--historical',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new analysis directory')
    data={'baseline':extract(args.baseline)}
    if args.optimized:
        data['optimized']=extract(args.optimized)
        for key in ('users','seconds_per_phase','spawn_per_second','stop_timeout_seconds','wait_seconds',
                    'request_timeout_seconds','seed_users','model','retrieval','login_limit','login_peer_limit','message_limit','workload_sha256'):
            assert data['baseline']['protocol'][key]==data['optimized']['protocol'][key],key
    if args.historical:data['historical_d4']=extract(args.historical)
    args.output.mkdir(parents=True)
    (args.output/'report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    lines=['# D5 原始采样复算','', '| Run | VU | RPS | P50 ms | P95 ms | P99 ms | failures | monitor failures |',
           '|---|---|---|---|---|---|---|---|']
    for kind,experiment in data.items():
        for p in experiment['phases']:
            lines.append(f"| {kind} | {p['users']} | {p['rps']:.2f} | {p['p50_ms']:.2f} | {p['p95_ms']:.2f} | {p['p99_ms']:.2f} | {p['failures']} | {p['monitor_failures']} |")
    lines+=['','HTTP 分位数由全部请求精确复算；内部 timer 分位数为每标签最近 8192 次，count/sum/max 为进程累计。',
            'SQL execute、Store、事务为嵌套计时，不相加；连接池 queue wait 与 acquire（含建连）单独记录。']
    (args.output/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({'experiments':list(data),'output':str(args.output)}))


if __name__=='__main__':main()
