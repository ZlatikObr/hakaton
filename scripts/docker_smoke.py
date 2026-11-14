"""Integration smoke: no cloud APIs. Requires an already built tz-audit:local image."""
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
import httpx

root = Path(__file__).resolve().parents[1]
for path in root.glob('*исход*.docx'):
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--tmpfs', '/tmp', '--tmpfs', '/data:uid=10001,gid=10001,mode=700',
        '--mount', f'type=bind,src={path},dst=/input.docx,readonly',
        'tz-audit:local', 'python', '-m', 'tz_audit.cli', 'demo', '/input.docx'], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Container offline pipeline failed: ' + result.stderr)
    print('offline container OK:', path.name, flush=True)

token = secrets.token_hex(24)
env = {**os.environ, 'TZ_API_TOKEN': token}
container = subprocess.check_output(['docker', 'run', '-d', '--rm', '--read-only', '--cap-drop', 'ALL',
    '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp', '--tmpfs', '/data:uid=10001,gid=10001,mode=700',
    '-e', 'TZ_API_TOKEN', '-p', '127.0.0.1::8000', 'tz-audit:local'], env=env, text=True).strip()
try:
    binding = subprocess.check_output(['docker', 'port', container, '8000'], text=True).strip()
    base = 'http://' + binding
    with httpx.Client(base_url=base, timeout=60) as client:
        for _ in range(30):
            try:
                if client.get('/health').status_code == 200: break
            except httpx.TransportError: pass
            time.sleep(0.2)
        else: raise RuntimeError('Health check did not become ready')
        assert client.get('/jobs/'+'a'*32).status_code == 401
        headers = {'Authorization': 'Bearer '+token}
        fixture = next(root.glob('*исход*.docx'))
        result = client.post('/jobs', headers=headers, files={'file': ('input.docx', fixture.read_bytes())})
        result.raise_for_status()
        job = result.json()['job_id']
        assert client.get('/jobs/'+job, headers=headers).status_code == 200
        result = client.post('/jobs/'+job+'/analyze', headers=headers, json={'mode': 'offline'})
        result.raise_for_status()
        result = client.get('/jobs/'+job+'/report.docx', headers=headers)
        assert result.status_code == 200 and result.content.startswith(b'PK')
        print('authenticated container API upload/preview/analyze/download OK', flush=True)
finally:
    subprocess.run(['docker', 'stop', container], stdout=subprocess.DEVNULL, check=True, timeout=20)
