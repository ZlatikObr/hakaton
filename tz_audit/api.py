"""Local document workbench. HttpOnly same-site session; no remote assets."""
import hmac
import json
import re
import os
import secrets
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


def create_app(data_dir=None):
    app = FastAPI(title='Редактор технических заданий', version='0.3.0', docs_url=None, redoc_url=None)
    service = Service(data_dir or os.getenv('TZ_DATA_DIR', 'runs'))
    api_token = os.getenv('TZ_API_TOKEN') or secrets.token_urlsafe(32)
    hosts = ['127.0.0.1', 'localhost', '[::1]'] + (['testserver'] if data_dir is not None else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    runtime = {'key': os.getenv('OPENROUTER_API_KEY', ''), 'model': os.getenv('OPENROUTER_MODEL', AgentConfig.model)}
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tz-review')
    progress, state_lock = {}, threading.Lock()

    def authorize(request: Request, authorization: str = Header(default='')):
        if len(api_token) < 24:
            raise HTTPException(503, 'Задайте TZ_API_TOKEN длиной не менее 24 символов.')
        if hmac.compare_digest(authorization, 'Bearer ' + api_token):
            return
        cookie = request.cookies.get('tz_session', '')
        if not hmac.compare_digest(cookie, api_token):
            raise HTTPException(401, 'Откройте главную страницу для локальной сессии.')
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
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
    def home():
        response = FileResponse(STATIC / 'index.html')
        response.set_cookie('tz_session', api_token, httponly=True, samesite='strict', max_age=86400)
        return response

    @app.get('/health')
    def health(): return {'status': 'ok'}

    @app.get('/settings', dependencies=[Depends(authorize)])
    def settings():
        return {'has_key': bool(runtime['key']), 'model': runtime['model'], 'models': MODELS,
            'model_routes': {model: route_info(model) for model in MODELS},
            'search_backend': os.getenv('TZ_SEARCH_BACKEND', 'openrouter'),
            'privacy_profiles': privacy_profiles(), 'privacy_default': 'natasha'}

    # Необязательный личный кабинет: вход по коду из письма, история проверок.
    accounts = Accounts(str(service.root))
    secure_cookie = os.getenv('TZ_COOKIE_SECURE', 'false').lower() == 'true'

    def current_user(request):
        return accounts.user(request.cookies.get('tz_user', ''))

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
        await run_in_threadpool(accounts.attach, email, body.job_id, body.filename)
        return {'ok': True}

    class Settings(Strict):
        api_key: str = Field(min_length=16, max_length=300)

    @app.post('/settings', dependencies=[Depends(authorize)])
    def save_settings(body: Settings):
        if any(c.isspace() for c in body.api_key) or not body.api_key.isascii():
            raise HTTPException(400, 'Проверьте формат API-ключа.')
        with state_lock:
            runtime['key'] = body.api_key
        return {'has_key': True}

    @app.post('/jobs', dependencies=[Depends(authorize)])
    async def prepare(file: UploadFile = File(...), privacy_backend: str = Form('natasha')):
        try:
            if not (file.filename or '').lower().endswith('.docx'):
                raise HTTPException(400, 'Поддерживается только DOCX.')
            return await safe_call(service.prepare, await file.read(MAX_FILE + 1), privacy_backend)
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
    async def apply_selected(job_id: str, body: SelectedChanges):
        return await safe_call(service.apply_selected, job_id, body.report_digest, body.change_ids)

    # Compatibility route for CLI/integrations: synchronous result.
    @app.post('/jobs/{job_id}/analyze', dependencies=[Depends(authorize)])
    async def analyze(job_id: str, body: Analyze):
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
