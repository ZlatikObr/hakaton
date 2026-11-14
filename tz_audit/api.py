"""Local document workbench. HttpOnly same-site session; no remote assets."""
import hmac
import hashlib
import json
import re
import os
import secrets
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field
from .agent import AgentConfig, OpenRouter
from .document import MAX_FILE, DocumentError
from .models import Strict
from .network import NetworkError
from .privacy import PrivacyError, check_release, release_digest, privacy_profiles
from .service import Service, JobError
from .accounts import Accounts, AuthError, SESSION_TTL
from .progress import RunControl, RunCancelled
from .model_profiles import MODELS, route_info

STATIC = Path(__file__).with_name('static')


def download_name(name, fallback):
    # Имя файла для скачивания: без управляющих символов и разделителей путей.
    clean = re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]+', ' ', name or '')
    clean = re.sub(r'\s+', ' ', clean).strip(' .')[:150]
    return clean or fallback


def configured_public_origin():
    """Canonical HTTPS origin behind our authenticated reverse proxy.

    Do not infer this from client-controlled forwarded headers. Local mode keeps
    its existing Host/Origin checks and non-Secure localhost session cookie.
    """
    raw = os.getenv('TZ_PUBLIC_ORIGIN', '')
    if not raw:
        return '', ''
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname or ''
        labels = host.split('.')
        valid_host = len(host) <= 253 and len(labels) >= 2 and all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in labels)
        if (raw != raw.strip() or any(c.isspace() for c in raw)
                or parsed.scheme != 'https' or not valid_host
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError:
        raise RuntimeError('TZ_PUBLIC_ORIGIN должен содержать только HTTPS-домен без пути и параметров.') from None
    return 'https://' + host, host


def create_app(data_dir=None):
    public_origin, public_host = configured_public_origin()
    app = FastAPI(title='Редактор технических заданий', version='0.3.0', docs_url=None, redoc_url=None)
    service = Service(data_dir or os.getenv('TZ_DATA_DIR', 'runs'))
    anonymous = os.getenv('TZ_PUBLIC_ACCESS', '').lower() == 'true'
    api_token = os.getenv('TZ_API_TOKEN') or secrets.token_urlsafe(32)
    hosts = ['127.0.0.1', 'localhost', '[::1]'] + (['testserver'] if data_dir is not None else [])
    if public_host:
        hosts.append(public_host)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    runtime = {'key': os.getenv('OPENROUTER_API_KEY', ''), 'model': os.getenv('OPENROUTER_MODEL', AgentConfig.model)}
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tz-review')
    progress, state_lock = {}, threading.Lock()

    # Необязательный личный кабинет: вход по коду из письма, история проверок.
    accounts = Accounts(str(service.root))
    secure_cookie = os.getenv('TZ_COOKIE_SECURE', 'false').lower() == 'true' or bool(public_origin)

    def current_user(request):
        return accounts.user(request.cookies.get('tz_user', ''))

    def owns(request, job_id):
        # Публичный режим: задание доступно только браузеру, который его создал.
        marker = service.root / job_id / '.owner'
        return marker.is_file() and hmac.compare_digest(marker.read_text(), getattr(request.state, 'owner', ''))

    def public_quota(kind):
        if not anonymous:
            return
        limit = int(os.getenv('TZ_PUBLIC_RUNS_PER_DAY', '30')) if kind == 'run' else 100
        path = service.root / '.public-quota.sqlite3'
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, timeout=10)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS usage (kind TEXT, at REAL)')
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM usage WHERE at < ?', (time.time()-86400,))
                count = db.execute('SELECT count(*) FROM usage WHERE kind=?', (kind,)).fetchone()[0]
                if count >= limit:
                    raise HTTPException(429, 'Достигнут суточный лимит сервиса. Попробуйте позже.')
                db.execute('INSERT INTO usage VALUES (?, ?)', (kind, time.time()))
        finally:
            db.close()

    def authorize(request: Request, authorization: str = Header(default='')):
        if anonymous:
            token = request.cookies.get('tz_visitor', '')
            if not re.fullmatch(r'[a-f0-9]{64}', token):
                raise HTTPException(401, 'Откройте главную страницу сайта.')
            owner = hashlib.sha256(token.encode()).hexdigest()
            request.state.owner = owner
            job_id = request.path_params.get('job_id')
            if job_id:
                if not re.fullmatch(r'[a-f0-9]{32}', job_id) or not owns(request, job_id):
                    raise HTTPException(404, 'Документ не найден.')
            origin = request.headers.get('origin')
            if (origin and origin != (public_origin or str(request.base_url).rstrip('/'))) or request.headers.get('sec-fetch-site') == 'cross-site':
                raise HTTPException(403, 'Запрос с другого сайта запрещён.')
            return
        if len(api_token) < 24:
            raise HTTPException(503, 'Задайте TZ_API_TOKEN длиной не менее 24 символов.')
        if hmac.compare_digest(authorization, 'Bearer ' + api_token):
            return
        cookie = request.cookies.get('tz_session', '')
        if not hmac.compare_digest(cookie, api_token):
            raise HTTPException(401, 'Откройте главную страницу для локальной сессии.')
        origin = request.headers.get('origin')
        expected_origin = public_origin or str(request.base_url).rstrip('/')
        if origin and origin != expected_origin:
            raise HTTPException(403, 'Запрос с другого сайта запрещён.')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'Запрос с другого сайта запрещён.')

    @app.middleware('http')
    async def secure_headers(request, call_next):
        if request.method in {'POST', 'PUT', 'PATCH'}:
            try: length = int(request.headers.get('content-length', '-1'))
            except ValueError: length = -1
            if length < 0 or length > MAX_FILE + 100_000:
                return JSONResponse({'detail': 'Необходим Content-Length; лимит запроса 12 МБ.'}, status_code=413)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Некорректный запрос. Входные данные скрыты.'}, status_code=422)

    async def safe_call(fn, *args):
        try:
            return await run_in_threadpool(fn, *args)
        except (DocumentError, PrivacyError, NetworkError, JobError) as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:
            raise HTTPException(500, 'Ошибка обработки. Входные данные скрыты.') from None

    if STATIC.is_dir():
        app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/')
    def home(request: Request):
        response = FileResponse(STATIC / 'index.html')
        if anonymous:
            token = request.cookies.get('tz_visitor', '')
            if not re.fullmatch(r'[a-f0-9]{64}', token):
                token = secrets.token_hex(32)
            response.set_cookie('tz_visitor', token, httponly=True, samesite='strict',
                secure=bool(public_origin), max_age=2592000)
            response.delete_cookie('tz_session')
            return response
        response.set_cookie('tz_session', api_token, httponly=True, samesite='strict',
            secure=bool(public_origin), max_age=86400)
        return response

    @app.get('/health')
    def health(): return {'status': 'ok'}

    @app.get('/settings', dependencies=[Depends(authorize)])
    def settings():
        return {'can_edit_key': not anonymous, 'has_key': bool(runtime['key']), 'model': runtime['model'], 'models': MODELS,
            'model_routes': {model: route_info(model) for model in MODELS},
            'search_backend': os.getenv('TZ_SEARCH_BACKEND', 'openrouter'),
            'privacy_profiles': privacy_profiles(), 'privacy_default': 'natasha'}

    class EmailBody(Strict):
        email: str = Field(min_length=3, max_length=254)

    class CodeBody(Strict):
        email: str = Field(min_length=3, max_length=254)
        code: str = Field(min_length=4, max_length=12)

    class AttachBody(Strict):
        job_id: str = Field(pattern=r'^[a-f0-9]{32}$')
        filename: str = Field(default='', max_length=300)

    @app.get('/me', dependencies=[Depends(authorize)])
    def me(request: Request):
        return {'email': current_user(request), 'mail_enabled': accounts.mailer.enabled,
            'demo': accounts.mailer.demo and not accounts.mailer.host}

    @app.post('/auth/request', dependencies=[Depends(authorize)])
    async def auth_request(body: EmailBody):
        public_quota('login')
        try:
            code = await run_in_threadpool(accounts.request_code, body.email)
        except AuthError as exc:
            raise HTTPException(400, str(exc)) from None
        return {'sent': True, **({'demo_code': code} if code else {})}

    @app.post('/auth/verify', dependencies=[Depends(authorize)])
    async def auth_verify(body: CodeBody):
        try:
            email, token = await run_in_threadpool(accounts.verify, body.email, body.code)
        except AuthError as exc:
            raise HTTPException(400, str(exc)) from None
        response = JSONResponse({'email': email})
        response.set_cookie('tz_user', token, httponly=True, samesite='lax', secure=secure_cookie, max_age=SESSION_TTL)
        return response

    @app.post('/auth/logout', dependencies=[Depends(authorize)])
    def auth_logout(request: Request):
        accounts.logout(request.cookies.get('tz_user', ''))
        response = JSONResponse({'email': None})
        response.delete_cookie('tz_user')
        return response

    @app.get('/my/jobs', dependencies=[Depends(authorize)])
    async def my_jobs(request: Request):
        email = current_user(request)
        if not email:
            raise HTTPException(401, 'Войдите по почте, чтобы видеть историю проверок.')
        items = []
        for job in accounts.jobs(email):
            try:
                path = service.path(job['job_id'])
            except JobError:
                continue
            item = {**job, 'status': 'prepared', 'findings': None, 'high': None, 'edits': None}
            report_file = path / 'report.json'
            if report_file.is_file():
                item['status'] = 'complete'
                try:
                    data = json.loads(report_file.read_text())
                    findings = data['review']['findings']
                    item.update(findings=len(findings), high=sum(f.get('severity') == 'high' for f in findings),
                        edits=data.get('stats', {}).get('applied_edits', 0))
                except Exception:
                    pass
            with state_lock:
                control = progress.get(job['job_id'])
            if control is not None and control.snapshot()['status'] in {'queued', 'running', 'cancelling'}:
                item['status'] = 'running'
            items.append(item)
        return {'email': email, 'jobs': items}

    @app.post('/my/jobs', dependencies=[Depends(authorize)])
    async def attach_job(request: Request, body: AttachBody):
        email = current_user(request)
        if not email:
            raise HTTPException(401, 'Войдите по почте, чтобы сохранять проверки.')
        await safe_call(service.path, body.job_id)
        if anonymous and not owns(request, body.job_id):
            raise HTTPException(404, 'Документ не найден.')
        await run_in_threadpool(accounts.attach, email, body.job_id, body.filename)
        return {'ok': True}

    class Settings(Strict):
        api_key: str = Field(min_length=16, max_length=300)

    @app.post('/settings', dependencies=[Depends(authorize)])
    def save_settings(body: Settings):
        if anonymous:
            raise HTTPException(403, 'Настройки подключения изменяет владелец сервера.')
        if any(c.isspace() for c in body.api_key) or not body.api_key.isascii():
            raise HTTPException(400, 'Проверьте формат API-ключа.')
        with state_lock:
            runtime['key'] = body.api_key
        return {'has_key': True}

    @app.post('/jobs', dependencies=[Depends(authorize)])
    async def prepare(request: Request, file: UploadFile = File(...), privacy_backend: str = Form('natasha')):
        try:
            if not (file.filename or '').lower().endswith('.docx'):
                raise HTTPException(400, 'Поддерживается только DOCX.')
            public_quota('upload')
            result = await safe_call(service.prepare, await file.read(MAX_FILE + 1), privacy_backend)
            if anonymous:
                from .service import write_private
                write_private(service.root / result['job_id'] / '.owner', request.state.owner)
            return result
        finally:
            await file.close()

    @app.get('/jobs/{job_id}', dependencies=[Depends(authorize)])
    async def preview(job_id: str):
        result = await safe_call(service.preview, job_id)
        _, _, public, _ = await safe_call(service.load, job_id)
        result['release_digest'] = release_digest(public)
        return result

    class Analyze(Strict):
        mode: Literal['offline', 'openrouter'] = 'offline'
        approved_digest: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
        model: str | None = Field(default=None, max_length=100)
        web_enabled: bool = True

    class Redaction(Strict):
        block_id: str = Field(pattern=r'^b[0-9]{4,6}$')
        quote: str = Field(min_length=1, max_length=4000)

    @app.post('/jobs/{job_id}/redactions', dependencies=[Depends(authorize)])
    async def redact_more(job_id: str, body: Redaction):
        return await safe_call(service.redact_more, job_id, body.block_id, body.quote)

    class SelectedChanges(Strict):
        report_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
        change_ids: list[str] = Field(min_length=1, max_length=480)

    @app.get('/jobs/{job_id}/proposals', dependencies=[Depends(authorize)])
    async def proposals(job_id: str):
        return await safe_call(service.proposals, job_id)

    @app.post('/jobs/{job_id}/apply-selected', dependencies=[Depends(authorize)])
    async def apply_selected(job_id: str, body: SelectedChanges, request: Request):
        public_quota('upload')
        result = await safe_call(service.apply_selected, job_id, body.report_digest, body.change_ids)
        if anonymous:
            from .service import write_private
            write_private(service.root / result['job_id'] / '.owner', request.state.owner)
        return result

    # Compatibility route for CLI/integrations: synchronous result.
    @app.post('/jobs/{job_id}/analyze', dependencies=[Depends(authorize)])
    async def analyze(job_id: str, body: Analyze):
        if anonymous:
            raise HTTPException(403, 'Используйте запуск через очередь обработки.')
        return await safe_call(service.analyze, job_id, body.mode, body.approved_digest)

    @app.post('/jobs/{job_id}/run', dependencies=[Depends(authorize)], status_code=202)
    async def run_job(job_id: str, body: Analyze):
        _, _, public, _ = await safe_call(service.load, job_id)
        model = body.model or runtime['model']
        if model not in MODELS:
            raise HTTPException(400, 'Выбранная модель не разрешена.')
        key = runtime['key']
        if body.mode == 'openrouter':
            if not key: raise HTTPException(503, 'Сервис проверки временно недоступен: на сервере не подключена модель.')
            await safe_call(check_release, public, body.approved_digest)
        with state_lock:
            if job_id in progress and progress[job_id].snapshot()['status'] in {'queued', 'running', 'cancelling'}:
                raise HTTPException(409, 'Документ уже обрабатывается.')
            if sum(p.snapshot()['status'] in {'queued', 'running', 'cancelling'} for p in progress.values()) >= 3:
                raise HTTPException(429, 'Очередь занята. Дождитесь завершения текущих документов.')
            if body.mode == 'openrouter':
                public_quota('run')
            control = RunControl()
            progress[job_id] = control
        def work():
            try:
                control.start()
                config = AgentConfig.from_env()
                config.model, config.web_enabled = model, body.web_enabled
                service.analyze(job_id, body.mode, body.approved_digest,
                    client=OpenRouter(key) if body.mode == 'openrouter' else None, config=config, control=control)
            except RunCancelled:
                control.cancelled()
            except Exception as exc:
                detail = str(exc) if isinstance(exc, (DocumentError, PrivacyError, NetworkError, JobError)) else 'Не удалось обработать документ. Исходный файл сохранён.'
                control.fail(detail)
        pool.submit(work)
        return {'job_id': job_id, 'status': 'queued'}

    @app.post('/jobs/{job_id}/cancel', dependencies=[Depends(authorize)])
    async def cancel_job(job_id: str):
        await safe_call(service.path, job_id)
        with state_lock:
            control = progress.get(job_id)
        if control is None:
            raise HTTPException(409, 'Активное задание не найдено.')
        control.cancel()
        return control.snapshot()

    @app.get('/jobs/{job_id}/status', dependencies=[Depends(authorize)])
    async def status(job_id: str):
        path = await safe_call(service.path, job_id)
        with state_lock:
            control = progress.get(job_id)
        if control is not None:
            return control.snapshot()
        return {'status': 'complete' if (path/'report.json').exists() else 'prepared', 'events': []}

    @app.get('/jobs/{job_id}/report.{extension}', dependencies=[Depends(authorize)])
    async def report(job_id: str, extension: Literal['json', 'docx'], name: str | None = None):
        path = await safe_call(service.path, job_id)
        file = path / ('report.'+extension)
        if not file.is_file() or not (path/'report.json').is_file(): raise HTTPException(404, 'Отчёт пока не создан.')
        return FileResponse(file, filename=download_name(name, 'review')+'.'+extension, media_type='application/json' if extension == 'json' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    @app.get('/jobs/{job_id}/corrected.docx', dependencies=[Depends(authorize)])
    async def corrected(job_id: str, name: str | None = None):
        path = await safe_call(service.path, job_id)
        if not (path/'report.json').is_file() or not (path/'corrected.docx').is_file(): raise HTTPException(404, 'Документ пока не готов.')
        return FileResponse(path/'corrected.docx', filename=download_name(name, 'specification-edited')+'.docx', media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    return app
