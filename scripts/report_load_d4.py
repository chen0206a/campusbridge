"""Recompute D4 load metrics from raw request samples; no model or Docker call."""
import argparse
import csv
import json
from pathlib import Path
import re

from d4_contracts import percentile, summarize


def memory_bytes(value):
    match=re.fullmatch(r'([0-9.]+)(B|kB|MB|GB|KiB|MiB|GiB)',value.strip())
    if not match:raise ValueError('UNRECOGNIZED_DOCKER_MEMORY_UNIT')
    return float(match[1])*{'B':1,'kB':1000,'MB':1000**2,'GB':1000**3,'KiB':1024,'MiB':1024**2,'GiB':1024**3}[match[2]]


def report(root):
    root=Path(root)
    result=json.loads((root/'result.json').read_text(encoding='utf8'))
    phases=[]
    for declared in result['phases']:
        folder=root/('users-'+str(declared['users']))
        rows=[json.loads(line) for line in (folder/'requests.jsonl').read_text(encoding='utf8').splitlines()]
        computed=summarize(rows,declared['observed_seconds'])
        for key,value in computed.items():
            assert value==declared[key],(key,value,declared[key])
        with (folder/'locust_stats.csv').open(encoding='utf8') as handle:
            stats=list(csv.DictReader(handle))
        logins=next(int(r['Request Count']) for r in stats if r['Name']=='login')
        assert logins==declared['users'], 'Not all target virtual users logged in'
        resource=json.loads((folder/'resources.json').read_text(encoding='utf8'))
        samples=[s for s in resource if 'app' in s]
        assert samples,'No actual resource samples'
        services={}
        for sample in samples:
            for item in sample['docker_stats']:
                name=item['Name'].removeprefix(result['project']+'-').removesuffix('-1')
                values=services.setdefault(name,{'cpu_percent':[],'memory_bytes':[]})
                values['cpu_percent'].append(float(item['CPUPerc'].rstrip('%')))
                values['memory_bytes'].append(memory_bytes(item['MemUsage'].split('/')[0]))
        by_endpoint={}
        for name in sorted({r['name'] for r in rows}):
            by_endpoint[name]=summarize([r for r in rows if r['name']==name],declared['observed_seconds'])
        # End-of-phase gauges include drained requests after the final periodic
        # sample. Resource maxima remain explicitly sampled observations.
        redis=declared['before_final_restart_metrics']['redis']
        phases.append({'users':declared['users'],**computed,'actual_logins':logins,
            'endpoints':by_endpoint,'database_counts':declared['correctness']['counts'],
            'run_states_cumulative':declared['correctness']['run_states'],
            'duplicate_groups':declared['correctness']['duplicate_groups'],
            'monitor_samples':len(samples),'monitor_errors':len(resource)-len(samples),
            'monitor_collection_p95_seconds':percentile([s['collection_seconds'] for s in samples],.95),
            'services':{name:{'cpu_p50_percent':percentile(values['cpu_percent'],.5),
                              'cpu_max_percent':max(values['cpu_percent']),
                              'memory_max_bytes':max(values['memory_bytes'])} for name,values in services.items()},
            'pool_checked_out_observed_max':max(s['app']['database_pool'].get('checkedout',0) for s in samples),
            'mysql_threads_connected_observed_max':max(s['mysql']['Threads_connected'] for s in samples),
            'mysql_slow_queries_cumulative':samples[-1]['mysql']['Slow_queries'],
            'redis_cache_counts_cumulative':redis,
            'redis_memory_observed_max_bytes':max(s['app']['redis_memory_bytes'] or 0 for s in samples)})
    return {'status':result['status'],'scope':result['scope'],'phases':phases,
            'correctness':{k:result[k] for k in ('duplicate_http','claim_race','isolation_checks','redis_outage','backend_recovery','replay_after_restart') if k in result},
            'external_llm_calls':0,'note':'Nearest-rank latency over all requests, including failures, ramp, login and drain; cache/SQL totals are cumulative. CPU 100% equals one logical core.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new report directory')
    data=report(args.input);args.output.mkdir(parents=True)
    (args.output/'report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    lines=['# D4 HTTP 负载实测','',data['scope'],'',
        '| VU | 请求数 | RPS | P50 ms | P95 ms | 错误率 | 超时率 |','|---|---|---|---|---|---|---|']
    for row in data['phases']:
        lines.append(f"| {row['users']} | {row['requests']} | {row['rps']:.2f} | {row['p50_ms']:.2f} | {row['p95_ms']:.2f} | {row['error_rate']:.2%} | {row['timeout_rate']:.2%} |")
    lines += ['', '逐端点指标、资源、缓存和正确性结果见 report.json。此为单次短时 Mock HTTP 测量，包含 ramp/登录/drain；没有调用真实 LLM。',
              '逐请求记录和预先固定的 protocol/source-binding 随本轮结果保留。高并发延迟和所有失败同样进入统计。']
    (args.output/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'status':data['status'],'phases':len(data['phases'])}))


if __name__=='__main__':main()
