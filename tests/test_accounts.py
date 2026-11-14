import io
import re
import sqlite3
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from tz_audit.accounts import Accounts, AuthError, Mailer, normalize_email, CODE_TTL, RESEND_AFTER, MAX_ATTEMPTS, SESSION_TTL
from tz_audit.api import create_app, download_name

TOKEN = 'x' * 32
AUTH = {'Authorization': 'Bearer ' + TOKEN}


class Clock:
    def __init__(self): self.now = 1_000_000.0
    def __call__(self): return self.now


class FakeMailer:
    demo, host = False, 'smtp.test'
    enabled = True
    def __init__(self, fail=False): self.sent, self.fail = [], fail
    def send_code(self, email, code):
        if self.fail: raise AuthError('Не удалось отправить письмо. Попробуйте еще раз позже.')
        self.sent.append((email, code)); return True


@pytest.fixture
def clock(): return Clock()


@pytest.fixture
def mailer(): return FakeMailer()


@pytest.fixture
def accounts(tmp_path, mailer, clock): return Accounts(str(tmp_path), mailer=mailer, clock=clock)


def login(accounts, mailer, email='User@Example.com'):
    accounts.request_code(email)
    return accounts.verify(email, mailer.sent[-1][1])


# --- модуль accounts ---

@pytest.mark.parametrize('raw,expected', [(' User@Example.COM ', 'user@example.com'), ('a.b+c@mail.ru', 'a.b+c@mail.ru')])
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize('raw', ['', None, 'no-at', 'a@b', 'a b@c.ru', 'a@b.c', 'x' * 65 + '@mail.ru', 'a@' + 'b' * 250 + '.ru'])
def test_normalize_email_rejects(raw):
    with pytest.raises(AuthError): normalize_email(raw)


def test_code_login_session_and_logout(accounts, mailer):
    assert accounts.request_code('User@Example.com') is None
    email, code = mailer.sent[0]
    assert email == 'user@example.com' and re.fullmatch(r'\d{6}', code)
    user, token = accounts.verify('user@example.com', code[:3] + ' ' + code[3:])
    assert user == 'user@example.com' and len(token) >= 32
    assert accounts.user(token) == 'user@example.com'
    accounts.logout(token)
    assert accounts.user(token) is None


def test_code_is_single_use(accounts, mailer):
    accounts.request_code('u@example.com'); code = mailer.sent[0][1]
    accounts.verify('u@example.com', code)
    with pytest.raises(AuthError, match='устарел'): accounts.verify('u@example.com', code)


def test_code_expires(accounts, mailer, clock):
    accounts.request_code('u@example.com')
    clock.now += CODE_TTL + 1
    with pytest.raises(AuthError, match='устарел'): accounts.verify('u@example.com', mailer.sent[0][1])


def test_wrong_attempts_are_counted_and_lock_the_code(accounts, mailer):
    accounts.request_code('u@example.com'); code = mailer.sent[0][1]
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(AuthError, match='Неверный'): accounts.verify('u@example.com', wrong)
    with pytest.raises(AuthError, match='Слишком много'): accounts.verify('u@example.com', code)


def test_resend_is_rate_limited(accounts, mailer, clock):
    accounts.request_code('u@example.com')
    with pytest.raises(AuthError, match='через минуту'): accounts.request_code('u@example.com')
    clock.now += RESEND_AFTER + 1
    accounts.request_code('u@example.com')
    old, new = mailer.sent[0][1], mailer.sent[1][1]
    if old != new:
        with pytest.raises(AuthError): accounts.verify('u@example.com', old)
    accounts.verify('u@example.com', new)


def test_failed_mail_does_not_block_retry(tmp_path, clock):
    mailer = FakeMailer(fail=True)
    accounts = Accounts(str(tmp_path), mailer=mailer, clock=clock)
    with pytest.raises(AuthError, match='письмо'): accounts.request_code('u@example.com')
    mailer.fail = False
    accounts.request_code('u@example.com')
    assert len(mailer.sent) == 1


def test_session_expires(accounts, mailer, clock):
    _, token = login(accounts, mailer)
    clock.now += SESSION_TTL + 1
    assert accounts.user(token) is None


@pytest.mark.parametrize('token', ['', None, 'unknown', 'x' * 101])
def test_unknown_tokens(accounts, token):
    assert accounts.user(token) is None


def test_secrets_are_stored_only_as_hashes(tmp_path, accounts, mailer):
    accounts.request_code('u@example.com'); code = mailer.sent[0][1]
    _, token = accounts.verify('u@example.com', code)
    accounts.request_code('v@example.com'); pending = mailer.sent[1][1]
    dump = '\n'.join(sqlite3.connect(tmp_path / 'accounts.sqlite3').iterdump())
    assert token not in dump and ':' + pending not in dump and "'" + pending + "'" not in dump
    assert (tmp_path / '.accounts-pepper').stat().st_mode & 0o777 == 0o600


def test_pepper_survives_restart(tmp_path, mailer, clock):
    first = Accounts(str(tmp_path), mailer=mailer, clock=clock)
    _, token = login(first, mailer)
    assert Accounts(str(tmp_path), mailer=mailer, clock=clock).user(token) == 'user@example.com'


def test_jobs_are_per_user_newest_first_and_sanitized(accounts, clock):
    accounts.attach('a@example.com', '1' * 32, 'Первое.docx')
    clock.now += 10
    accounts.attach('a@example.com', '2' * 32, 'Второе\x00\n.docx')
    accounts.attach('b@example.com', '3' * 32, '')
    clock.now += 10
    accounts.attach('a@example.com', '1' * 32, 'Переименовано.docx')
    jobs = accounts.jobs('a@example.com')
    assert [j['job_id'] for j in jobs] == ['2' * 32, '1' * 32]
    assert jobs[0]['filename'] == 'Второе  .docx' and jobs[1]['filename'] == 'Переименовано.docx'
    assert accounts.jobs('b@example.com')[0]['filename'] == 'Документ'
    assert accounts.jobs('c@example.com') == []


def test_mailer_modes():
    assert not Mailer({}).enabled
    with pytest.raises(AuthError, match='не настроен'): Mailer({}).send_code('u@example.com', '123456')
    demo = Mailer({'TZ_AUTH_DEMO': 'TRUE'})
    assert demo.enabled and demo.send_code('u@example.com', '123456') is False
    assert Mailer({'SMTP_HOST': 'smtp.test', 'SMTP_FROM': 'bot@test.ru'}).enabled


def test_mailer_smtp_failure_is_hidden(monkeypatch):
    import smtplib
    def boom(*a, **k): raise OSError('connection refused to secret-host')
    monkeypatch.setattr(smtplib, 'SMTP_SSL', boom)
    with pytest.raises(AuthError) as info:
        Mailer({'SMTP_HOST': 'smtp.test', 'SMTP_FROM': 'bot@test.ru'}).send_code('u@example.com', '123456')
    assert 'secret-host' not in str(info.value)


def test_mailer_sends_code_over_ssl(monkeypatch):
    import smtplib
    sent = {}
    class Fake:
        def __init__(self, host, port, timeout): sent['addr'] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, password): sent['login'] = user
        def send_message(self, message): sent['message'] = message
    monkeypatch.setattr(smtplib, 'SMTP_SSL', Fake)
    env = {'SMTP_HOST': 'smtp.test', 'SMTP_USER': 'bot@test.ru', 'SMTP_PASSWORD': 'p'}
    assert Mailer(env).send_code('u@example.com', '654321') is True
    assert sent['addr'] == ('smtp.test', 465) and sent['login'] == 'bot@test.ru'
    assert sent['message']['To'] == 'u@example.com' and sent['message']['From'] == 'bot@test.ru'
    assert '654321' in sent['message'].get_content()


# --- эндпоинты ---

@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_API_TOKEN', TOKEN)
    monkeypatch.setenv('TZ_AUTH_DEMO', 'true')
    for name in ('SMTP_HOST', 'SMTP_FROM', 'SMTP_USER'): monkeypatch.delenv(name, raising=False)
    return TestClient(create_app(tmp_path))


def demo_login(client, email='user@example.com'):
    response = client.post('/auth/request', headers=AUTH, json={'email': email})
    assert response.status_code == 200, response.text
    code = response.json()['demo_code']
    response = client.post('/auth/verify', headers=AUTH, json={'email': email, 'code': code})
    assert response.status_code == 200, response.text
    return response


def upload(client, docx_bytes, name='ТЗ Лифт.docx'):
    response = client.post('/jobs', headers=AUTH, files={'file': (name, docx_bytes(['Поставка лифта.', 'Гарантийный срок на выполненные работы с даты товарной накладной.']))})
    assert response.status_code == 200, response.text
    return response.json()['job_id']


def test_auth_endpoints_require_local_session(demo_client):
    for method, path in [('get', '/me'), ('post', '/auth/request'), ('post', '/auth/verify'), ('post', '/auth/logout'), ('get', '/my/jobs'), ('post', '/my/jobs')]:
        kwargs = {'json': {}} if method == 'post' else {}
        assert getattr(demo_client, method)(path, **kwargs).status_code == 401, path


def test_me_reports_demo_mode(demo_client):
    assert demo_client.get('/me', headers=AUTH).json() == {'email': None, 'mail_enabled': True, 'demo': True}


def test_me_without_mail_configured(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_API_TOKEN', TOKEN)
    for name in ('TZ_AUTH_DEMO', 'SMTP_HOST', 'SMTP_FROM', 'SMTP_USER'): monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(tmp_path))
    assert client.get('/me', headers=AUTH).json() == {'email': None, 'mail_enabled': False, 'demo': False}
    response = client.post('/auth/request', headers=AUTH, json={'email': 'user@example.com'})
    assert response.status_code == 400 and 'не настроен' in response.json()['detail']


def test_demo_login_sets_httponly_cookie_and_logout(demo_client):
    response = demo_login(demo_client)
    assert response.json() == {'email': 'user@example.com'}
    cookie = response.headers['set-cookie']
    assert 'tz_user=' in cookie and 'HttpOnly' in cookie and 'samesite=lax' in cookie.lower()
    assert demo_client.get('/me', headers=AUTH).json()['email'] == 'user@example.com'
    response = demo_client.post('/auth/logout', headers=AUTH, json={})
    assert response.status_code == 200 and response.json() == {'email': None}
    assert demo_client.get('/me', headers=AUTH).json()['email'] is None


def test_auth_errors_are_400_and_do_not_echo_input(demo_client):
    response = demo_client.post('/auth/request', headers=AUTH, json={'email': 'not-an-email'})
    assert response.status_code == 400 and 'not-an-email' not in response.text
    demo_client.post('/auth/request', headers=AUTH, json={'email': 'user@example.com'})
    response = demo_client.post('/auth/verify', headers=AUTH, json={'email': 'user@example.com', 'code': '99999999'})
    assert response.status_code == 400 and response.json()['detail'] == 'Неверный код.'
    response = demo_client.post('/auth/verify', headers=AUTH, json={'email': 'user@example.com', 'code': '1'})
    assert response.status_code == 422
    response = demo_client.post('/auth/request', headers=AUTH, json={'email': 'user@example.com'})
    assert response.status_code == 400 and 'минуту' in response.json()['detail']


def test_my_jobs_requires_login(demo_client):
    assert demo_client.get('/my/jobs', headers=AUTH).status_code == 401
    assert demo_client.post('/my/jobs', headers=AUTH, json={'job_id': 'a' * 32}).status_code == 401


def test_attach_validates_job(demo_client):
    demo_login(demo_client)
    assert demo_client.post('/my/jobs', headers=AUTH, json={'job_id': '../etc'}).status_code == 422
    response = demo_client.post('/my/jobs', headers=AUTH, json={'job_id': 'a' * 32})
    assert response.status_code == 400 and 'не найдено' in response.json()['detail']


def test_history_shows_status_and_counts(demo_client, docx_bytes):
    demo_login(demo_client)
    job = upload(demo_client, docx_bytes)
    assert demo_client.post('/my/jobs', headers=AUTH, json={'job_id': job, 'filename': 'ТЗ Лифт.docx'}).json() == {'ok': True}
    items = demo_client.get('/my/jobs', headers=AUTH).json()['jobs']
    assert [(i['job_id'], i['filename'], i['status'], i['findings']) for i in items] == [(job, 'ТЗ Лифт.docx', 'prepared', None)]
    response = demo_client.post('/jobs/' + job + '/analyze', headers=AUTH, json={'mode': 'offline'})
    assert response.status_code == 200, response.text
    item = demo_client.get('/my/jobs', headers=AUTH).json()['jobs'][0]
    assert item['status'] == 'complete' and item['findings'] >= 1 and item['high'] >= 0 and item['edits'] == 0


def test_history_is_private_per_user(demo_client, docx_bytes):
    demo_login(demo_client, 'a@example.com')
    job = upload(demo_client, docx_bytes)
    demo_client.post('/my/jobs', headers=AUTH, json={'job_id': job, 'filename': 'a.docx'})
    demo_client.post('/auth/logout', headers=AUTH, json={})
    demo_login(demo_client, 'b@example.com')
    assert demo_client.get('/my/jobs', headers=AUTH).json() == {'email': 'b@example.com', 'jobs': []}


def test_history_skips_deleted_jobs(demo_client, docx_bytes, tmp_path):
    import shutil
    demo_login(demo_client)
    job = upload(demo_client, docx_bytes)
    demo_client.post('/my/jobs', headers=AUTH, json={'job_id': job})
    shutil.rmtree(tmp_path / job)
    assert demo_client.get('/my/jobs', headers=AUTH).json()['jobs'] == []


# --- имена скачиваемых файлов и желтое выделение ---

@pytest.mark.parametrize('raw,expected', [
    ('ТЗ Лифт (исправленное)', 'ТЗ Лифт (исправленное)'),
    ('../../etc/passwd', 'etc passwd'),
    ('a\r\nb:c*?"<>|', 'a b c'),
    ('  ...  ', 'fallback'), (None, 'fallback'), ('я' * 300, 'я' * 150),
])
def test_download_name(raw, expected):
    assert download_name(raw, 'fallback') == expected


def test_downloads_use_given_names_and_highlight_edits(tmp_path, monkeypatch, docx_bytes):
    from tz_audit.document import parse_docx
    from tz_audit.editing import apply_edits
    from tz_audit.models import Review, Finding, Anchor, Edit
    from tz_audit.privacy import Redactor
    monkeypatch.setenv('TZ_API_TOKEN', TOKEN)
    client = TestClient(create_app(tmp_path))
    job = upload(client, docx_bytes)
    assert client.post('/jobs/' + job + '/analyze', headers=AUTH, json={'mode': 'offline'}).status_code == 200
    response = client.get('/jobs/' + job + '/report.docx', headers=AUTH, params={'name': 'Отчет проверки ТЗ Лифт'})
    assert response.status_code == 200
    from urllib.parse import unquote
    assert unquote(response.headers['content-disposition']).endswith('Отчет проверки ТЗ Лифт.docx')
    response = client.get('/jobs/' + job + '/corrected.docx', headers=AUTH, params={'name': 'ТЗ Лифт (исправленное)'})
    assert unquote(response.headers['content-disposition']).endswith('ТЗ Лифт (исправленное).docx')
    response = client.get('/jobs/' + job + '/corrected.docx', headers=AUTH)
    assert 'filename="specification-edited.docx"' in response.headers['content-disposition']

    raw = docx_bytes(['Мощность 300 Вт']); parsed = parse_docx(raw); block = parsed.blocks[0]
    review = Review(summary='Точечное исправление проверенной внутренней ошибки.', findings=[Finding(category='characteristics', severity='medium', status='document_issue', title='Уточнение технической характеристики', anchors=[Anchor(block_id=block.id, quote=block.text)], explanation='Значение приведено в соответствие с подтверждёнными данными.', recommendation='Исправить значение в указанной строке документа.', edits=[Edit(block_id=block.id, before='300', after='500')])])
    result, changes = apply_edits(raw, parsed, review, Redactor(False))
    assert changes[0]['status'] == 'applied'
    xml = ZipFile(io.BytesIO(result)).read('word/document.xml').decode()
    inserted = re.search(r'<w:ins [^>]*>(.*?)</w:ins>', xml, re.S).group(1)
    assert '<w:highlight w:val="yellow"' in inserted and '500' in inserted
    deleted = re.search(r'<w:del [^>]*>(.*?)</w:del>', xml, re.S).group(1)
    assert 'highlight' not in deleted
