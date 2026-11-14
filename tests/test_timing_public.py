import os
import time
import pytest
from fastapi.testclient import TestClient
from tz_audit.api import create_app
from tz_audit.timing import Timings, purge_expired, document_chars

TOKEN = 'x' * 32
AUTH = {'Authorization': 'Bearer ' + TOKEN}


def test_estimate_grows_with_document_and_calibrates(tmp_path):
    t = Timings(tmp_path)
    small, big = t.estimate('openrouter', 2_000), t.estimate('openrouter', 40_000)
    assert 120 <= small < big <= 900
    assert t.estimate('offline', 40_000) < 60
    for chars in (10_000, 20_000, 30_000):
        t.record('openrouter', chars, 2 * (180 + 0.008 * chars))  # на этом сервере все вдвое дольше
    assert t.estimate('openrouter', 20_000) == pytest.approx(2 * (180 + 160), abs=2)
    t.record('unknown', 10, 10)
    assert 'unknown' not in t._load()
    assert (tmp_path / '.timings.json').stat().st_mode & 0o777 == 0o600


def test_estimate_ignores_broken_file(tmp_path):
    (tmp_path / '.timings.json').write_text('{oops')
    assert Timings(tmp_path).estimate('openrouter', 10_000) == 260


def test_purge_removes_only_expired_inactive_jobs(tmp_path):
    old, fresh, active = ('a' * 32, 'b' * 32, 'c' * 32)
    for name in (old, fresh, active):
        (tmp_path / name).mkdir(); (tmp_path / name / 'source.docx').write_bytes(b'x')
    (tmp_path / 'accounts.sqlite3').write_bytes(b'')
    past = time.time() - 30 * 3600
    for name in (old, active):
        for p in [tmp_path / name, tmp_path / name / 'source.docx']: os.utime(p, (past, past))
    assert purge_expired(tmp_path, 24, frozenset({active})) == [old]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([fresh, active, 'accounts.sqlite3'])
    assert purge_expired(tmp_path, 0) == []


def test_preview_estimate_and_delete(tmp_path, monkeypatch, docx_bytes):
    monkeypatch.setenv('TZ_API_TOKEN', TOKEN)
    client = TestClient(create_app(tmp_path))
    assert client.get('/settings', headers=AUTH).json()['retention_hours'] == 24
    job = client.post('/jobs', headers=AUTH, files={'file': ('a.docx', docx_bytes(['Поставка лифта. ' * 200]))}).json()['job_id']
    est = client.get('/jobs/' + job, headers=AUTH).json()['estimate_seconds']
    assert est['openrouter'] > est['offline'] > 0
    assert client.post('/jobs/' + job + '/run', headers=AUTH, json={'mode': 'offline'}).status_code == 202
    for _ in range(100):
        if client.get('/jobs/' + job + '/status', headers=AUTH).json()['status'] == 'complete': break
        time.sleep(0.05)
    assert 'offline' in Timings(tmp_path)._load()
    assert client.delete('/jobs/' + job, headers=AUTH).json() == {'deleted': True}
    assert not (tmp_path / job).exists()
    assert client.get('/jobs/' + job, headers=AUTH).status_code == 400


@pytest.fixture
def public_app(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_PUBLIC_ACCESS', 'true')
    monkeypatch.setenv('TZ_PUBLIC_ORIGIN', 'https://planwithai.online')
    monkeypatch.setenv('TZ_AUTH_DEMO', 'true')
    monkeypatch.delenv('TZ_API_TOKEN', raising=False)
    return create_app(tmp_path)


def visitor(app):
    client = TestClient(app, base_url='https://planwithai.online', headers={'origin': 'https://planwithai.online'})
    assert client.get('/').status_code == 200 and 'tz_visitor' in client.cookies
    return client


def test_public_mode_binds_jobs_to_visitor(public_app, docx_bytes):
    a, b = visitor(public_app), visitor(public_app)
    job = a.post('/jobs', files={'file': ('a.docx', docx_bytes(['Поставка лифта.']))}).json()['job_id']
    assert a.get('/jobs/' + job).status_code == 200
    assert b.get('/jobs/' + job).status_code == 404
    assert b.delete('/jobs/' + job).status_code == 404
    assert a.post('/jobs/' + job + '/analyze', json={'mode': 'offline'}).status_code == 403
    assert a.post('/settings', json={'api_key': 'k' * 20}).status_code == 403
    code = b.post('/auth/request', json={'email': 'b@example.com'}).json()['demo_code']
    assert b.post('/auth/verify', json={'email': 'b@example.com', 'code': code}).status_code == 200
    assert b.post('/my/jobs', json={'job_id': job}).status_code == 404
    assert a.delete('/jobs/' + job).json() == {'deleted': True}


def test_public_mode_rejects_foreign_host_and_origin(public_app):
    assert TestClient(public_app, base_url='https://evil.example').get('/').status_code == 400
    client = visitor(public_app)
    assert client.get('/me', headers={'origin': 'https://evil.example'}).status_code == 403
    assert TestClient(public_app, base_url='https://planwithai.online').get('/me').status_code == 401


def test_public_login_cookie_is_secure(public_app):
    client = visitor(public_app)
    code = client.post('/auth/request', json={'email': 'u@example.com'}).json()['demo_code']
    response = client.post('/auth/verify', json={'email': 'u@example.com', 'code': code})
    assert 'secure' in response.headers['set-cookie'].lower()


def test_legal_pages_are_served(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_API_TOKEN', TOKEN)
    client = TestClient(create_app(tmp_path))
    assert 'не является юридической консультацией' in client.get('/static/terms.html').text
    assert '152-ФЗ' in client.get('/static/privacy.html').text
