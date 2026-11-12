"""Model-free acceptance for an empty, isolated local Space container."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:17860')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path)
    mode.add_argument('--readback', type=Path)
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    origin = urllib.parse.urlparse(base)
    if origin.scheme != 'http' or origin.hostname not in {'127.0.0.1', 'localhost'}:
        raise ValueError('This mutating packaging check is restricted to local loopback HTTP.')
    if args.output and args.output.exists():
        raise ValueError('Output already exists; do not overwrite a previous smoke receipt.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'})
        with opener.open(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw.decode()

    for attempt in range(30):
        try:
            if request('/health')['status'] == 'ok':
                break
        except (OSError, ValueError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Container never became ready.')

    if args.readback:
        receipt = json.loads(args.readback.read_text(encoding='utf-8'))
        skill = request('/api/v1/skills/' + receipt['skill_id'])
        loop = request('/api/v1/harness/' + receipt['loop_id'])
        snapshot = request('/api/v1/lifecycle/' + receipt['skill_id'] + '/snapshot/history')
        assert skill['skill_id'] == receipt['skill_id'] and skill['version'] == receipt['version']
        assert loop['loop_id'] == receipt['loop_id']
        assert loop['attempts'][0]['verifier_passed'] is True
        assert receipt['snapshot_commit'] in json.dumps(snapshot)
        print(json.dumps({'restart_readback': 'passed', 'skill_id': receipt['skill_id'], 'loop_id': receipt['loop_id'], 'snapshot_commit': receipt['snapshot_commit'], 'model_calls': 0}))
        return

    listing = request('/api/v1/skills')
    assert listing == [], 'Refusing to mutate a non-empty store.'
    html = request('/wiki')
    assert '<div id="root">' in html
    asset = re.search(r'src="(/assets/[^"]+)"', html)
    assert asset and len(request(asset.group(1))) > 100
    assert 'swagger-ui' in request('/docs')
    assert '/api/v1/skills' in request('/openapi.json')['paths']
    with socket.create_connection((origin.hostname, origin.port or 80), timeout=5) as channel:
        key = base64.b64encode(os.urandom(16)).decode()
        headers = ['GET /ws HTTP/1.1', 'Host: ' + origin.netloc, 'Upgrade: websocket', 'Connection: Upgrade', 'Sec-WebSocket-Key: ' + key, 'Sec-WebSocket-Version: 13', '', '']
        channel.sendall((chr(13) + chr(10)).join(headers).encode())
        upgrade = channel.recv(4096).decode(errors='replace')
        assert '101 Switching Protocols' in upgrade, upgrade
    specs = [{'type': 'json_equals', 'path': 'output.text', 'value': 'space-smoke'}]
    skill = request('/api/v1/skills', {
        'name': 'space_preflight_echo', 'description': 'Deterministic container packaging check; no model calls.',
        'state': 'S2',
        'interface': {'input_schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}, 'output_schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}},
        'implementation': {'code': "output['text'] = input_data['text']"},
        'evaluation': {'verifier_specs': specs},
        'provenance': {'source_type': 'manual', 'created_by_agent': 'space-packaging-smoke'},
    })
    skill_id = skill['skill_id']
    loop = request('/api/v1/harness/' + skill_id + '/verify-loop', {
        'harness': 'local_skillwiki', 'max_attempts': 1, 'allow_repair': False, 'promote_on_pass': False,
        'test_cases': [{'test_id': 'space-native-echo', 'name': 'echo smoke', 'goal': 'Return the supplied text', 'input_data': {'text': 'space-smoke'}, 'verifier_specs': specs}],
    })
    assert loop['attempt_count'] == 1 and loop['attempts'][0]['verifier_passed'] is True, loop
    assert loop['attempts'][0]['output']['output']['text'] == 'space-smoke', loop
    snapshot = request('/api/v1/lifecycle/' + skill_id + '/snapshot', {'author': 'space-review', 'message': 'Container packaging smoke snapshot'})
    receipt = {'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'model_calls': 0, 'frontend': True, 'api': True, 'websocket_upgrade': True, 'initial_store_empty': True, 'skill_id': skill_id, 'version': skill['version'], 'loop_id': loop['loop_id'], 'evidence_path': loop['evidence_path'], 'native_verifier_passed': True, 'snapshot_commit': snapshot['commit'], 'snapshot_path': snapshot['snapshot_path'], 'published': False}
    args.output.write_text(json.dumps(receipt, indent=2) + chr(10), encoding='utf-8')
    print(json.dumps(receipt))

if __name__ == '__main__':
    main()
