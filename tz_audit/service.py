import fcntl
import json
import os
import re
import secrets
import hashlib
import shutil
from contextlib import contextmanager
from pathlib import Path
from .agent import Agent, AgentConfig, OpenRouter
from .document import parse_document
from .models import Document, Report, Review
from .offline import baseline
from .privacy import Redactor, PrivacyError, public_payload, release_digest
from .report import render_report
from .validation import validate_review
from .editing import apply_edits
from .progress import RunControl

class JobError(ValueError):
    pass

def write_private(path, data):
    """Atomic replacement; raw documents and vaults are never world readable."""
    temp = path.with_name('.' + path.name + '.' + secrets.token_hex(8))
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data if isinstance(data, bytes) else data.encode())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2).encode()

class Service:
    def __init__(self, root='runs'):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def path(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise JobError('Некорректный идентификатор задания.')
        path = self.root / job_id
        if not path.is_dir() or path.is_symlink():
            raise JobError('Задание не найдено.')
        return path

    @contextmanager
    def lock(self, path):
        fd = os.open(path / '.lock', os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'w') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobError('Задание уже обрабатывается.') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def prepare(self, data, privacy_backend='natasha'):
        document, source_format = parse_document(data)
        redactor = Redactor(backend=privacy_backend)
        public = redactor.document(document)
        # Re-scan released JSON, not only standalone paragraphs.
        redactor.assert_safe(json.dumps(public_payload(public), ensure_ascii=False))
        job_id = secrets.token_hex(16)
        path = self.root / job_id
        path.mkdir(mode=0o700)
        write_private(path / ('source.' + source_format), data)
        write_private(path / 'document.json', json_bytes(document.model_dump()))
        write_private(path / 'public.json', json_bytes(public_payload(public)))
        write_private(path / 'vault.json', json_bytes(redactor.vault))
        manifest = {'job_id': job_id, 'release_digest': release_digest(public), 'redactions': redactor.counts,
            'privacy_backend': redactor.backend, 'blocks': len(public.blocks), 'warnings': document.warnings, 'source_format': source_format,
            'status': 'prepared', 'privacy_notice': 'Просмотрите public.json локально. NER не гарантирует обнаружение любых ПД. Внешний доступ требует подтверждения digest.'}
        write_private(path / 'manifest.json', json_bytes(manifest))
        return manifest

    def preview(self, job_id):
        path = self.path(job_id)
        return {**json.loads((path / 'manifest.json').read_text()), 'public': json.loads((path / 'public.json').read_text())}

    def load(self, job_id):
        path = self.path(job_id)
        original = Document.model_validate_json((path / 'document.json').read_bytes())
        payload = json.loads((path / 'public.json').read_text())
        if set(payload) != {'blocks'}:
            raise JobError('Некорректный public.json.')
        public = Document(sha256=original.sha256, blocks=[{**b, 'location': b['id']} for b in payload['blocks']])
        if [b.id for b in public.blocks] != [b.id for b in original.blocks]:
            raise JobError('Изменены идентификаторы или состав блоков.')
        manifest = json.loads((path / 'manifest.json').read_text())
        redactor = Redactor(backend=manifest.get('privacy_backend', 'natasha'))
        redactor.vault = json.loads((path / 'vault.json').read_text())
        return path, original, public, redactor

    def redact_more(self, job_id, block_id, quote):
        path = self.path(job_id)
        with self.lock(path):
            path, original, public, redactor = self.load(job_id)
            if (path / 'report.json').exists():
                raise JobError('Для изменений после анализа создайте новое задание.')
            block = next((b for b in public.blocks if b.id == block_id), None)
            from .privacy import TOKEN
            if not quote or len(quote) > 4000 or block is None or quote not in block.text or TOKEN.search(quote):
                raise JobError('Укажите точный ещё не скрытый фрагмент существующего блока.')
            token = f'⟦PRIVATE_{len(redactor.vault)+1:04}⟧'
            redactor.vault[token] = quote
            for b in public.blocks:
                b.text = b.text.replace(quote, token)
            write_private(path / 'vault.json', json_bytes(redactor.vault))
            write_private(path / 'public.json', json_bytes(public_payload(public)))
            manifest = json.loads((path / 'manifest.json').read_text())
            manifest['release_digest'] = release_digest(public)
            manifest['manual_redactions'] = manifest.get('manual_redactions', 0) + 1
            write_private(path / 'manifest.json', json_bytes(manifest))
            return manifest

    def analyze(self, job_id, mode='offline', approved_digest=None, *, client=None, config=None, control=None):
        control = control or RunControl()
        control.check()
        if mode not in {'offline', 'openrouter'}:
            raise JobError('Неизвестный режим анализа.')
        path = self.path(job_id)
        with self.lock(path):
            path, original, public, redactor = self.load(job_id)
            if (path / 'report.json').exists():
                raise JobError('Отчёт уже создан. Для нового запуска подготовьте новое задание.')
            control.check()
            control.emit('Проверяем локальные правила и разрешение на отправку')
            if mode == 'offline':
                result = baseline(public)
                review, sources = result if isinstance(result, tuple) else (result, [])
                review = validate_review(review, public, sources)
                stats, model = {}, None
            else:
                from .privacy import check_release
                check_release(public, approved_digest)
                config = config or AgentConfig.from_env()
                client = client or OpenRouter(os.getenv('OPENROUTER_API_KEY', ''))
                review, sources, stats = Agent(client, config).run(public, redactor, approved_digest, control=control)
                model = config.model
                if getattr(config, 'procurement', None):
                    stats['procurement'] = dict(config.procurement)
            # Local restoration ONLY in document anchors. Model-generated prose and external sources stay redacted.
            restored = review.model_copy(deep=True)
            original_by_id = {b.id: b for b in original.blocks}
            from .privacy import normalize
            for finding in restored.findings:
                for anchor in finding.anchors:
                    quote = redactor.restore(anchor.quote)
                    if quote not in normalize(original_by_id[anchor.block_id].text):
                        raise JobError('Цитата после восстановления не совпадает с исходником.')
                    anchor.quote = quote
            control.check()
            control.emit('Применяем минимальные правки и отмечаем изменения в Word')
            if (path / 'source.docx').exists():
                corrected, changes = apply_edits((path / 'source.docx').read_bytes(), original, review, redactor)
            else:
                # PDF нельзя исправить с сохранением оформления: правки остаются предложениями в отчете.
                corrected, changes = None, [{'id': f'f{n}-e{k}', 'operation': 'replace', 'finding': n, 'block_id': e.block_id,
                    'before': redactor.restore(e.before), 'after': redactor.restore(e.after), 'status': 'skipped', 'selectable': False,
                    'reason': 'Исходник в PDF: внесите правку в документ вручную.'}
                    for n, f in enumerate(review.findings, 1) for k, e in enumerate(f.edits, 1)]
            stats.update(applied_edits=sum(c['status'] == 'applied' for c in changes), proposed_edits=len(changes), tracked_changes=any(c['status'] == 'applied' for c in changes))
            report = Report(changes=changes, document_sha256=original.sha256, mode=mode, model=model, review=restored,
                sources=sources, stats=stats, warnings=original.warnings + [
                    'Правовой режим закупки и применимость норм требуют проверки сотрудником закупок.',
                    *(['Номера страниц не вычисляются; привязка выполнена к абзацам и строкам таблиц.'] if (path / 'source.docx').exists() else []),
                    'Отчёт и исходные цитаты могут содержать ПД; хранить локально с ограниченным доступом.',
                ])
            control.check()
            control.emit('Формируем отчёт и проверяем итоговый документ')
            docx = render_report(report, original)
            def publish():
                manifest = json.loads((path / 'manifest.json').read_text())
                manifest.update(status='complete', mode=mode)
                try:
                    if corrected is not None:
                        write_private(path / 'corrected.docx', corrected)
                    write_private(path / 'report.docx', docx)
                    write_private(path / 'review-public.json', review.model_dump_json(indent=2).encode())
                    write_private(path / 'report.json', report.model_dump_json(indent=2).encode())
                    write_private(path / 'manifest.json', json_bytes(manifest))
                except Exception:
                    for name in ['corrected.docx', 'report.docx', 'report.json', 'review-public.json']:
                        (path / name).unlink(missing_ok=True)
                    raise
            control.commit(publish, findings=len(report.review.findings), applied_edits=stats['applied_edits'])
            return report

    def proposals(self, job_id):
        path = self.path(job_id)
        if not (path / 'report.json').exists():
            raise JobError('Сначала дождитесь результата проверки.')
        raw = (path / 'report.json').read_bytes()
        report = Report.model_validate_json(raw)
        return {'report_digest': hashlib.sha256(raw).hexdigest(),
            'changes': [c for c in report.changes if c.get('selectable') and c['status'] == 'skipped']}

    def apply_selected(self, job_id, report_digest, change_ids):
        path = self.path(job_id)
        with self.lock(path):
            if not (path / 'source.docx').exists():
                raise JobError('Для PDF правки в документ не вносятся: перенесите их вручную по отчёту.')
            options = self.proposals(job_id)
            allowed = {c['id'] for c in options['changes']}
            if report_digest != options['report_digest']:
                raise JobError('Результат изменился. Обновите список предложений.')
            if not change_ids or len(set(change_ids)) != len(change_ids) or not set(change_ids) <= allowed:
                raise JobError('Выберите существующие доступные предложения без повторов.')
            if not (path / 'review-public.json').exists():
                raise JobError('Для этого старого результата требуется повторный анализ.')
            _, original, public, redactor = self.load(job_id)
            report = Report.model_validate_json((path / 'report.json').read_bytes())
            for source in report.sources:
                redactor.allow_public_url(source.url)
            review = validate_review(Review.model_validate_json((path / 'review-public.json').read_bytes()), public, report.sources)
            approved = sorted(set(report.stats.get('approved_changes', [])) | set(change_ids))
            corrected, changes = apply_edits((path / 'source.docx').read_bytes(), original, review, redactor, approved_changes=approved)
            unapplied = [c for c in changes if c['id'] in change_ids and c['status'] != 'applied']
            if unapplied:
                raise JobError('Выбранные предложения конфликтуют или не могут быть применены к Word. Выберите их по отдельности.')
            report.changes = changes
            report.stats.update(applied_edits=sum(c['status'] == 'applied' for c in changes), proposed_edits=len(changes), tracked_changes=any(c['status']=='applied' for c in changes), approved_changes=approved, parent_job=job_id)
            docx = render_report(report, original)
            new_id = secrets.token_hex(16)
            target = self.root / new_id; target.mkdir(mode=0o700)
            try:
                for name in ('source.docx','document.json','public.json','vault.json','review-public.json'):
                    write_private(target / name, (path / name).read_bytes())
                manifest = json.loads((path / 'manifest.json').read_text())
                manifest.update(job_id=new_id, parent_job=job_id, status='complete')
                write_private(target / 'corrected.docx', corrected)
                write_private(target / 'report.docx', docx)
                write_private(target / 'report.json', report.model_dump_json(indent=2))
                write_private(target / 'manifest.json', json_bytes(manifest))
            except Exception:
                shutil.rmtree(target)
                raise
            return {'job_id': new_id, 'parent_job': job_id, 'applied_edits': report.stats['applied_edits']}
