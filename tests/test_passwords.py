import smtplib
import sqlite3
import pytest
from fastapi.testclient import TestClient
from tz_audit.accounts import Accounts, AuthError, LOCK_TIME, MAX_ATTEMPTS
from tz_audit.api import create_app


class Clock:
    def __init__(self): self.now = 1_000_000.0
    def __call__(self): return self.now


class RealMailer:
    """Как настоящая почта: письма уходят, код виден только в «ящике»."""
    demo, host, sender, enabled, delivers = False, 'smtp.test', 'bot@test.ru', True, True
    def __init__(self): self.sent = []
    def send_code(self, email, code): self.sent.append((email, code)); return True


class DemoMailer(RealMailer):
    demo, host, sender, delivers = True, '', '', False
    def send_code(self, email, code): return False


@pytest.fixture
def clock(): return Clock()


@pytest.fixture
def accounts(tmp_path, clock): return Accounts(str(tmp_path), mailer=RealMailer(), clock=clock)


def code_login(accounts, email='u@example.com'):
    accounts.request_code(email)
    return accounts.verify(email, accounts.mailer.sent[-1][1])


def test_password_login_after_setting_it(accounts):
    email, token = code_login(accounts)
    assert not accounts.has_password(email)
    accounts.set_password(email, 'correct horse', keep_token=token)
    assert accounts.has_password(email)
    user, new_token = accounts.login_password('U@Example.com', 'correct horse')
    assert user == email and accounts.user(new_token) == email and accounts.user(token) == email


def test_password_is_hashed_with_salt(tmp_path, accounts):
    email, _ = code_login(accounts)
    accounts.set_password(email, 'correct horse')
    code_login(accounts, 'v@example.com'); accounts.set_password('v@example.com', 'correct horse')
    rows = sqlite3.connect(tmp_path / 'accounts.sqlite3').execute('select pw_hash from users').fetchall()
    assert len({r[0] for r in rows}) == 2 and all(r[0].startswith('scrypt$') and 'correct' not in r[0] for r in rows)


@pytest.mark.parametrize('password', ['short', ' leading space', 'trailing ', 'u@example.com', 'x' * 129, ''])
def test_weak_passwords_rejected(accounts, password):
    email, _ = code_login(accounts)
    with pytest.raises(AuthError): accounts.set_password(email, password)


def test_wrong_password_and_unknown_user_give_same_error(accounts):
    email, _ = code_login(accounts); accounts.set_password(email, 'correct horse')
    with pytest.raises(AuthError, match='Неверная почта или пароль'): accounts.login_password(email, 'wrong pass')
    with pytest.raises(AuthError, match='Неверная почта или пароль'): accounts.login_password('nobody@example.com', 'correct horse')
    with pytest.raises(AuthError, match='Неверная почта или пароль'): accounts.login_password('nopass@example.com', '')


def test_lockout_after_failed_attempts_and_code_login_unlocks(accounts, clock):
    email, _ = code_login(accounts); accounts.set_password(email, 'correct horse')
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(AuthError, match='Неверная'): accounts.login_password(email, 'wrong pass')
    with pytest.raises(AuthError, match='Слишком много'): accounts.login_password(email, 'correct horse')
    clock.now += LOCK_TIME + 1
    accounts.login_password(email, 'correct horse')
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(AuthError): accounts.login_password(email, 'wrong pass')
    clock.now += 61
    code_login(accounts)  # владение почтой подтверждено: блокировка снимается
    accounts.login_password(email, 'correct horse')


def test_changing_password_ends_other_sessions(accounts):
    email, phone = code_login(accounts); accounts.set_password(email, 'first password')
    _, laptop = accounts.login_password(email, 'first password')
    accounts.set_password(email, 'second password', keep_token=laptop)
    assert accounts.user(laptop) == email and accounts.user(phone) is None
    with pytest.raises(AuthError): accounts.login_password(email, 'first password')


def test_passwords_disabled_without_real_mail(tmp_path, clock):
    accounts = Accounts(str(tmp_path), mailer=DemoMailer(), clock=clock)
    accounts.request_code('u@example.com')
    with pytest.raises(AuthError, match='после подключения почты'): accounts.set_password('u@example.com', 'correct horse')
    with pytest.raises(AuthError, match='после подключения почты'): accounts.login_password('u@example.com', 'correct horse')


# --- API в публичном режиме с настоящей почтой ---

@pytest.fixture
def mail_app(tmp_path, monkeypatch):
    sent = []
    class FakeSMTP:
        def __init__(self, host, port, timeout): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, password): pass
        def send_message(self, message): sent.append((message['To'], message.get_content()))
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTP)
    for k, v in {'TZ_PUBLIC_ACCESS': 'true', 'TZ_PUBLIC_ORIGIN': 'https://planwithai.online', 'SMTP_HOST': 'smtp.yandex.ru',
                 'SMTP_PORT': '465', 'SMTP_USER': 'quantara-lab@yandex.ru', 'SMTP_PASSWORD': 'app-pass', 'SMTP_FROM': 'quantara-lab@yandex.ru'}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv('TZ_AUTH_DEMO', raising=False)
    return create_app(tmp_path), sent


def visitor(app):
    c = TestClient(app, base_url='https://planwithai.online', headers={'origin': 'https://planwithai.online'})
    c.get('/'); return c


def mail_login(client, sent, email='u@example.com'):
    assert client.post('/auth/request', json={'email': email}).json() == {'sent': True}
    code = __import__('re').search(r'\d{6}', sent[-1][1]).group()
    return client.post('/auth/verify', json={'email': email, 'code': code})


def test_api_password_flow_and_cross_device_history(mail_app, docx_bytes):
    app, sent = mail_app
    laptop = visitor(app)
    assert laptop.get('/me').json()['password_enabled'] is True
    assert laptop.post('/auth/set-password', json={'password': 'correct horse'}).status_code == 401
    r = mail_login(laptop, sent)
    assert r.json() == {'email': 'u@example.com', 'has_password': False} and sent[-1][0] == 'u@example.com'
    assert laptop.post('/auth/set-password', json={'password': 'short'}).status_code == 400
    assert laptop.post('/auth/set-password', json={'password': 'correct horse'}).json() == {'email': 'u@example.com', 'has_password': True}
    assert laptop.get('/me').json()['has_password'] is True
    job = laptop.post('/jobs', files={'file': ('a.docx', docx_bytes(['Поставка лифта.']))}).json()['job_id']
    assert laptop.post('/my/jobs', json={'job_id': job, 'filename': 'Лифт.docx'}).json() == {'ok': True}

    phone = visitor(app)
    assert phone.get('/jobs/' + job).status_code == 404
    bad = phone.post('/auth/password', json={'email': 'u@example.com', 'password': 'wrong pass'})
    assert bad.status_code == 400 and 'wrong' not in bad.text
    ok = phone.post('/auth/password', json={'email': 'u@example.com', 'password': 'correct horse'})
    assert ok.status_code == 200 and 'secure' in ok.headers['set-cookie'].lower()
    assert [j['job_id'] for j in phone.get('/my/jobs').json()['jobs']] == [job]
    assert phone.get('/jobs/' + job).status_code == 200  # своя проверка открывается и с другого устройства

    stranger = visitor(app)
    mail_login(stranger, sent, 'other@example.com')
    assert stranger.get('/jobs/' + job).status_code == 404


def test_api_password_disabled_in_demo(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_API_TOKEN', 'x' * 32); monkeypatch.setenv('TZ_AUTH_DEMO', 'true')
    for k in ('SMTP_HOST', 'SMTP_FROM', 'SMTP_USER'): monkeypatch.delenv(k, raising=False)
    client = TestClient(create_app(tmp_path))
    h = {'Authorization': 'Bearer ' + 'x' * 32}
    assert client.get('/me', headers=h).json()['password_enabled'] is False
    r = client.post('/auth/password', headers=h, json={'email': 'u@example.com', 'password': 'correct horse'})
    assert r.status_code == 400 and 'почты' in r.json()['detail']
