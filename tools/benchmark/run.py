"""HTTP-only collector with safe resume. Never accesses the scheduler/database directly."""
import argparse
import json
import math
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('Benchmark API redirects are rejected to protect credentials')

def open_request(request, timeout):
    return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)

def save(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    temporary.replace(path)

def collect(base_url, config, output, *, token=None, timeout=1800, interval=2, resume=False):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Expected HTTP(S) base URL without credentials, query or fragment')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Use HTTPS outside loopback to protect credentials')
    if not math.isfinite(timeout) or timeout < 0 or not math.isfinite(interval) or interval < 0:
        raise ValueError('Timeout and interval must be finite and nonnegative')
    output.mkdir(parents=True, exist_ok=True)
    headers = {'Accept':'application/json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base_url.rstrip('/')+'/api/v1'+path, body, {**headers, **({'Content-Type':'application/json'} if body else {})})
        with open_request(req, timeout=30) as response:
            return json.load(response)
    config_path = output/'config.json'
    created_path = output/'created.json'
    if resume:
        if json.loads(config_path.read_text()) != config:
            raise ValueError('Resume configuration differs from saved experiment')
        saved_origin = json.loads((output/'origin.json').read_text())
        if saved_origin != base_url.rstrip('/'):
            raise ValueError('Resume origin differs from saved coordinator')
        created = json.loads(created_path.read_text())
    else:
        if any(output.iterdir()):
            raise ValueError('Output directory is nonempty. Use --resume to observe the saved experiment, or choose a new directory.')
        save(config_path, config)
        save(output/'origin.json',base_url.rstrip('/'))
        created = request('/benchmarks', config)
        save(created_path, created)
    identifier = urllib.parse.quote(created['id'], safe='')
    start = time.monotonic()
    snapshots = output/'snapshots.jsonl'
    while True:
        record = request('/benchmarks/'+identifier)
        with snapshots.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')
        if record['state'] in ('succeeded', 'failed', 'cancelled', 'partial'):
            save(output/'raw.json',request('/benchmarks/'+identifier+'/export?format=json'))
            return record
        if time.monotonic()-start >= timeout:
            raise TimeoutError('Collection deadline reached; use --resume with this config/output. Do not recreate the experiment.')
        time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--resume',action='store_true')
    args = parser.parse_args()
    result = collect(args.base_url, json.loads(args.config.read_text()), args.out, token=os.environ.get('CAMPUS_BENCHMARK_TOKEN'), timeout=args.timeout,resume=args.resume)
    print(result['id'], result['state'])

if __name__ == '__main__':
    main()
