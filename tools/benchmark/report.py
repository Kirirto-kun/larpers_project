"""Reproducible reports from raw, matched measurements. Standard library only."""
from __future__ import annotations
import argparse
import csv
import html
import json
import math
import statistics
from pathlib import Path

MATCH_FIELDS = ('workload_digest', 'input_digest', 'quality_policy', 'resource_settings', 'unit', 'input_distribution', 'batch_policy', 'concurrency', 'cold')

def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def rate(record):
    """Count unique accepted units, including retry time in elapsed time."""
    duration = record.get('elapsed_ms')
    if not number(duration) or duration <= 0:
        raise ValueError('elapsed_ms must be positive and finite')
    accepted = set()
    for result in record.get('results', []):
        if result.get('state') != 'accepted':
            continue
        unit_id = result.get('unit_id')
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError('Accepted results require a stable unit_id')
        accepted.add(unit_id)
    return len(accepted) / (duration / 1000)

def summarize(values):
    if not values:
        return None
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values), 'repetitions': len(values), 'raw': values}

def build_report(data):
    config = data['config']
    for key in MATCH_FIELDS:
        if key not in config:
            raise ValueError(f'Missing experiment setting: {key}')
    nodes = config['compute_node_ids']
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError('compute_node_ids must be nonempty and unique')
    flags = []
    records = data.get('records', [])
    seen = set()
    groups = {node: [] for node in nodes}
    pool, service = [], []
    provenance = []
    for record in records:
        record_id = record['record_id']
        if record_id in seen:
            raise ValueError('Duplicate record_id; refusing ambiguous repeated measurements')
        seen.add(record_id)
        if record.get('experiment_id') != config['experiment_id']:
            raise ValueError('Experiment mismatch')
        for key in MATCH_FIELDS:
            if record.get(key) != config[key]:
                raise ValueError(f'Incompatible {key}; do not combine these series')
        kind = record['kind']
        if kind == 'standalone':
            node = record['node_id']
            if node not in groups:
                raise ValueError('Standalone node is not in this compute series')
            groups[node].append(rate(record))
            provenance.append({'record_id': record_id, 'node_id': node, 'source': record.get('provenance', 'Unspecified local timing'), 'credential_verified': record.get('credential_verified', False)})
        elif kind in ('pool', 'single_service'):
            if kind == 'pool' and set(record.get('compute_node_ids', [])) != set(nodes):
                raise ValueError('Pool compute participants differ from the baseline denominator')
            (pool if kind == 'pool' else service).append(rate(record))
        else:
            raise ValueError(f'Unsupported series kind: {kind}')
    baseline = {node: summarize(values) for node, values in groups.items()}
    pool_summary = summarize(pool)
    sufficient = pool_summary is not None and all(v is not None and v['median'] > 0 for v in baseline.values())
    efficiency = speedup = None
    if sufficient:
        medians = [v['median'] for v in baseline.values()]
        efficiency = pool_summary['median'] / sum(medians) * 100
        speedup = pool_summary['median'] / max(medians)
        if efficiency > 100:
            flags.append('Efficiency exceeds 100%: review baseline, batching, input distribution, and concurrency.')
    else:
        flags.append('Insufficient data: a positive baseline for each compute node and a pool series are required.')
    if len(pool) < 3 or any(len(v) < 3 for v in groups.values()):
        flags.append('Fewer than three comparable repetitions; not a formal benchmark.')
    return {'experiment_id': config['experiment_id'], 'git_sha': config.get('git_sha'), 'config': config, 'unit': config['unit']+'/s', 'standalone': baseline, 'single_service': summarize(service), 'pool': pool_summary, 'efficiency_percent': efficiency, 'speedup': speedup, 'flags': flags, 'provenance': provenance, 'timings_ms': data.get('timings_ms', {}), 'utilization': data.get('utilization', []), 'bottleneck': data.get('bottleneck', 'Not measured'), 'planning': data.get('planning', {}), 'costs': data.get('costs', {}), 'status': 'comparable' if sufficient else 'insufficient_data'}

def markdown(report):
    def fmt(value):
        return 'Insufficient data' if value is None else f'{value:.3f}'
    lines = ['# Campus Pool benchmark report', '', f"Experiment: `{report['experiment_id']}`", f"Commit: `{report['git_sha'] or 'Not recorded'}`", f"Units: {report['unit']}", '', '| Series | Median | Min | Max | Repeats |', '|---|---:|---:|---:|---:|']
    for name, values in [*report['standalone'].items(), ('single_service', report['single_service']), ('pool', report['pool'])]:
        lines.append(f"| {str(name).replace('|', '/')} | "+('Not measured | — | — | 0 |' if values is None else f"{values['median']:.3f} | {values['min']:.3f} | {values['max']:.3f} | {values['repetitions']} |"))
    lines += ['', f"Efficiency (%): {fmt(report['efficiency_percent'])}", f"Speedup: {fmt(report['speedup'])}", '', *['- '+flag for flag in report['flags']], '', '## Evidence and assumptions', '', '```json', json.dumps({k: report[k] for k in ('config','provenance','timings_ms','utilization','bottleneck','planning','costs')}, ensure_ascii=False, indent=2), '```', '', 'Local signatures establish origin, not independent proof of timing honesty. Utilization is not pooling efficiency.']
    return '\n'.join(lines)+'\n'

def export(data, output):
    report = build_report(data)
    output.mkdir(parents=True, exist_ok=True)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    text = markdown(report)
    (output/'report.md').write_text(text)
    (output/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Campus Pool benchmark</title><style>body{max-width:1000px;margin:40px auto;padding:20px;font:15px system-ui;background:#f5f6f2;color:#173b34}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Campus Pool benchmark</h1><pre>'+html.escape(text)+'</pre></html>')
    with (output/'rates.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['series','median','min','max','repetitions','unit'])
        for name, values in [*report['standalone'].items(), ('single_service', report['single_service']), ('pool', report['pool'])]:
            writer.writerow([name, *([values[k] for k in ('median','min','max','repetitions')] if values else [None,None,None,0]), report['unit']])
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    export(json.loads(args.input.read_text()), args.out)

if __name__ == '__main__':
    main()
