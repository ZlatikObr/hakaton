"""Необязательный личный кабинет: вход по коду из письма и история проверок.

Без входа сервис работает как раньше. Вход нужен только для того, чтобы
проверки сохранялись в «Мои проверки». Коды и токены сессий хранятся
только в виде хешей. База: SQLite рядом с каталогом заданий.
"""
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from contextlib import contextmanager
from email.message import EmailMessage

EMAIL = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$')
CODE_TTL = 10 * 60
RESEND_AFTER = 60
MAX_ATTEMPTS = 5
SESSION_TTL = 30 * 24 * 3600
PASSWORD_MIN = 8
LOCK_TIME = 15 * 60


class AuthError(ValueError):
    pass


def normalize_email(email):
    email = (email or '').strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise AuthError('Проверьте адрес почты.')
    return email


class Mailer:
    """Отправка кода через SMTP. Настройки берутся из переменных окружения."""

    def __init__(self, env=os.environ):
        self.host = env.get('SMTP_HOST', '')
        self.port = int(env.get('SMTP_PORT', '465') or 465)
        self.user = env.get('SMTP_USER', '')
        self.password = env.get('SMTP_PASSWORD', '')
        self.sender = env.get('SMTP_FROM', self.user)
        self.demo = env.get('TZ_AUTH_DEMO', 'false').lower() == 'true'

    @property
    def enabled(self):
        return bool(self.host and self.sender) or self.demo

    @property
    def delivers(self):
        """Письма действительно отправляются: только тогда вход доказывает владение адресом."""
        return bool(self.host and self.sender)

    def send_code(self, email, code):
        if not (self.host and self.sender):
            if self.demo:
                return False
            raise AuthError('Вход по почте пока не настроен на сервере.')
        message = EmailMessage()
        message['Subject'] = 'Код входа в Квантара.Закупки: ' + code
        message['From'] = self.sender
        message['To'] = email
        message.set_content(f'Ваш код для входа: {code}\n\nКод действует 10 минут. Если вы не запрашивали вход, просто проигнорируйте это письмо.')
        try:
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=15) as smtp:
                    if self.user: smtp.login(self.user, self.password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
                    smtp.starttls()
                    if self.user: smtp.login(self.user, self.password)
                    smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            raise AuthError('Не удалось отправить письмо. Попробуйте еще раз позже.') from None
        return True


class Accounts:
    def __init__(self, root, mailer=None, clock=time.time):
        os.makedirs(root, mode=0o700, exist_ok=True)
        self.path = os.path.join(root, 'accounts.sqlite3')
        self.mailer = mailer or Mailer()
        self.clock = clock
        self.lock = threading.Lock()
        pepper_file = os.path.join(root, '.accounts-pepper')
        if not os.path.exists(pepper_file):
            fd = os.open(pepper_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(secrets.token_hex(32))
        with open(pepper_file) as handle:
            self.pepper = handle.read().strip().encode()
        with self.db() as db:
            db.executescript('''
                create table if not exists codes(email text primary key, code_hash text, expires real, attempts int, sent real);
                create table if not exists sessions(token_hash text primary key, email text, expires real);
                create table if not exists jobs(email text, job_id text, filename text, created real, primary key(email, job_id));
                create table if not exists users(email text primary key, pw_hash text, fails int default 0, locked_until real default 0, updated real);
            ''')

    @contextmanager
    def db(self):
        """Транзакция: фиксируется при успехе, откатывается при исключении; соединение закрывается."""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def digest(self, value):
        return hmac.new(self.pepper, value.encode(), hashlib.sha256).hexdigest()

    def request_code(self, email):
        """Создает код и отправляет письмо. Возвращает код только в демо-режиме без SMTP."""
        email = normalize_email(email)
        now = self.clock()
        with self.lock, self.db() as db:
            row = db.execute('select sent from codes where email=?', (email,)).fetchone()
            if row and now - row['sent'] < RESEND_AFTER:
                raise AuthError('Код уже отправлен. Повторить можно через минуту.')
            code = f'{secrets.randbelow(10**6):06d}'
            db.execute('replace into codes values(?,?,?,?,?)', (email, self.digest(email + ':' + code), now + CODE_TTL, 0, now))
        try:
            sent = self.mailer.send_code(email, code)
        except AuthError:
            # Письмо не ушло: код не действует, и повторить можно сразу.
            with self.lock, self.db() as db:
                db.execute('delete from codes where email=?', (email,))
            raise
        return None if sent else code

    def verify(self, email, code):
        email = normalize_email(email)
        code = re.sub(r'\D', '', code or '')
        now = self.clock()
        with self.lock, self.db() as db:
            row = db.execute('select * from codes where email=?', (email,)).fetchone()
            if not row or row['expires'] < now:
                raise AuthError('Код устарел. Запросите новый.')
            if row['attempts'] >= MAX_ATTEMPTS:
                raise AuthError('Слишком много попыток. Запросите новый код.')
            wrong = not hmac.compare_digest(row['code_hash'], self.digest(email + ':' + code))
            if wrong:
                # Счетчик фиксируется до ошибки: исключение внутри транзакции откатило бы его.
                db.execute('update codes set attempts=attempts+1 where email=?', (email,))
        if wrong:
            raise AuthError('Неверный код.')
        with self.lock, self.db() as db:
            # Код одноразовый: если его уже использовали параллельно, удаления не будет.
            if not db.execute('delete from codes where email=? and code_hash=?', (email, row['code_hash'])).rowcount:
                raise AuthError('Код устарел. Запросите новый.')
            # Вход по коду из письма снимает блокировку пароля: владение почтой подтверждено.
            db.execute('update users set fails=0, locked_until=0 where email=?', (email,))
            token = self._new_session(db, email, now)
        return email, token

    def _new_session(self, db, email, now):
        token = secrets.token_urlsafe(32)
        db.execute('insert into sessions values(?,?,?)', (self.digest(token), email, now + SESSION_TTL))
        db.execute('delete from sessions where expires<?', (now,))
        return token

    @staticmethod
    def _hash_password(password, salt=None):
        salt = salt or secrets.token_bytes(16)
        key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return f'scrypt$16384$8$1${salt.hex()}${key.hex()}'

    @staticmethod
    def _check_password(password, stored):
        try:
            _, n, r, p, salt, key = stored.split('$')
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(key) // 2)
        except (ValueError, AttributeError):
            return False
        return hmac.compare_digest(actual.hex(), key)

    def has_password(self, email):
        with self.db() as db:
            row = db.execute('select pw_hash from users where email=?', (email,)).fetchone()
        return bool(row and row['pw_hash'])

    def set_password(self, email, password, keep_token=None):
        """Задает или меняет пароль. Остальные сессии пользователя завершаются."""
        if not self.mailer.delivers:
            raise AuthError('Пароль можно задать после подключения почты на сервере.')
        password = password or ''
        if len(password) < PASSWORD_MIN or len(password) > 128:
            raise AuthError(f'Пароль должен быть длиной от {PASSWORD_MIN} до 128 символов.')
        if password.strip() != password or password.lower() == email.lower():
            raise AuthError('Пароль не должен совпадать с почтой и начинаться или заканчиваться пробелом.')
        pw_hash, now = self._hash_password(password), self.clock()
        with self.lock, self.db() as db:
            db.execute('insert into users(email, pw_hash, fails, locked_until, updated) values(?,?,0,0,?) '
                'on conflict(email) do update set pw_hash=excluded.pw_hash, fails=0, locked_until=0, updated=excluded.updated',
                (email, pw_hash, now))
            db.execute('delete from sessions where email=? and token_hash<>?', (email, self.digest(keep_token or '')))

    def login_password(self, email, password):
        if not self.mailer.delivers:
            raise AuthError('Вход по паролю станет доступен после подключения почты.')
        email = normalize_email(email)
        now = self.clock()
        with self.db() as db:
            row = db.execute('select * from users where email=?', (email,)).fetchone()
        if row and row['locked_until'] > now:
            raise AuthError('Слишком много попыток. Войдите по коду из письма или повторите через 15 минут.')
        # Хеш считается и для несуществующего адреса, чтобы по времени ответа нельзя было узнать о регистрации.
        ok = self._check_password(password or '', row['pw_hash'] if row and row['pw_hash'] else self._hash_password('x'))
        if not (row and row['pw_hash'] and ok):
            if row:
                with self.lock, self.db() as db:
                    fails = row['fails'] + 1
                    db.execute('update users set fails=?, locked_until=? where email=?',
                        (0 if fails >= MAX_ATTEMPTS else fails, now + LOCK_TIME if fails >= MAX_ATTEMPTS else 0, email))
            raise AuthError('Неверная почта или пароль.')
        with self.lock, self.db() as db:
            db.execute('update users set fails=0, locked_until=0 where email=?', (email,))
            token = self._new_session(db, email, now)
        return email, token

    def has_job(self, email, job_id):
        with self.db() as db:
            return db.execute('select 1 from jobs where email=? and job_id=?', (email, job_id)).fetchone() is not None

    def user(self, token):
        if not token or len(token) > 100:
            return None
        with self.db() as db:
            row = db.execute('select email, expires from sessions where token_hash=?', (self.digest(token),)).fetchone()
        return row['email'] if row and row['expires'] > self.clock() else None

    def logout(self, token):
        if token:
            with self.lock, self.db() as db:
                db.execute('delete from sessions where token_hash=?', (self.digest(token),))

    def attach(self, email, job_id, filename):
        filename = re.sub(r'[\x00-\x1f\x7f]', ' ', filename or '').strip()[:200] or 'Документ'
        with self.lock, self.db() as db:
            db.execute('insert into jobs values(?,?,?,?) on conflict(email, job_id) do update set filename=excluded.filename',
                (email, job_id, filename, self.clock()))

    def jobs(self, email):
        with self.db() as db:
            rows = db.execute('select job_id, filename, created from jobs where email=? order by created desc limit 200', (email,)).fetchall()
        return [dict(r) for r in rows]
