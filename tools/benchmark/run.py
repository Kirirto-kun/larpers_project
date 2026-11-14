"""HTTP-only benchmark collector. No direct scheduler/database access."""
import argparse
import json
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

def collect(base_url, config, output, *, token=None, timeout=1800, interval=2):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Expected HTTP(S) base URL without credentials')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Use HTTPS outside loopback to protect credentials')
    output.mkdir(parents=True, exist_ok=True)
    headers = {'Accept':'application/json'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base_url.rstrip('/')+'/api/v1'+path, body, {**headers, **({'Content-Type':'application/json'} if body else {})})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    (output/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    created = request('/benchmarks', config)
    identifier = urllib.parse.quote(created['id'], safe='')
    (output/'created.json').write_text(json.dumps(created, indent=2)+'\n')
    start = time.monotonic()
    snapshots = output/'snapshots.jsonl'
    while True:
        record = request('/benchmarks/'+identifier)
        with snapshots.open('a') as stream:
            stream.write(json.dumps(record)+'\n')
        if record['state'] in ('succeeded', 'failed', 'cancelled', 'partial'):
            exported = request('/benchmarks/'+identifier+'/export?format=json')
            (output/'raw.json').write_text(json.dumps(exported, indent=2)+'\n')
            return record
        if time.monotonic()-start >= timeout:
            raise TimeoutError('Collection deadline reached; experiment remains on server. Resume observation using saved id; do not recreate it.')
        time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args()
    result = collect(args.base_url, json.loads(args.config.read_text()), args.out, token=os.environ.get('CAMPUS_BENCHMARK_TOKEN'), timeout=args.timeout)
    print(result['id'], result['state'])

if __name__ == '__main__':
    main()
