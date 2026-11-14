import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from tz_audit.service import Service, JobError
from tz_audit.api import create_app
from tz_audit.privacy import PrivacyError, release_digest
from tz_audit.models import Review, COVERAGE_TOPICS


def test_end_to_end_offline(tmp_path, docx_bytes):
    service = Service(tmp_path / 'data')
    data = docx_bytes(['Гарантийный срок на выполненные работы с даты товарной накладной.', 'Контактное лицо: Иван Петров; test@example.com'])
    manifest = service.prepare(data)
    path = service.path(manifest['job_id'])
    assert 'test@example.com' not in (path / 'public.json').read_text()
    assert (path / 'vault.json').stat().st_mode & 0o777 == 0o600
    report = service.analyze(manifest['job_id'])
    assert report.mode == 'offline' and len(report.review.findings) == 1
    assert (path / 'report.docx').read_bytes().startswith(b'PK')
    with pytest.raises(JobError): service.analyze(manifest['job_id'])


def test_service_release_required(tmp_path, docx_bytes):
    service = Service(tmp_path)
    manifest = service.prepare(docx_bytes(['Техническое задание. Мощность 50 Вт.']))
    with pytest.raises(PrivacyError): service.analyze(manifest['job_id'], 'openrouter')


def test_traversal(tmp_path):
    with pytest.raises(JobError): Service(tmp_path).preview('../source.docx')


def test_api_auth_and_offline(tmp_path, monkeypatch, docx_bytes):
    monkeypatch.setenv('TZ_API_TOKEN', 'x'*32)
    client = TestClient(create_app(tmp_path))
    assert client.get('/health').status_code == 200
    assert client.post('/jobs', files={'file': ('a.docx', b'invalid')}).status_code == 401
    headers = {'Authorization': 'Bearer ' + 'x'*32}
    response = client.post('/jobs', headers=headers, files={'file': ('a.docx', docx_bytes(['ТЗ. Поставка оборудования.']))})
    assert response.status_code == 200, response.text
    job = response.json()['job_id']
    assert client.get('/jobs/'+job, headers=headers).status_code == 200
    response = client.post('/jobs/'+job+'/analyze', headers=headers, json={'mode': 'offline'})
    assert response.status_code == 200, response.text
    response = client.get('/jobs/'+job+'/report.docx', headers=headers)
    assert response.status_code == 200 and response.content.startswith(b'PK')
    assert response.headers['cache-control'] == 'no-store'


def test_api_errors_do_not_echo(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_API_TOKEN', 'x'*32)
    client = TestClient(create_app(tmp_path))
    response = client.post('/jobs/'+'a'*32+'/analyze', headers={'Authorization': 'Bearer '+'x'*32}, json={'mode': 'test@example.com'})
    assert response.status_code == 422 and 'test@example.com' not in response.text


def test_samples_parse_and_privacy():
    from tz_audit.document import parse_docx
    from tz_audit.privacy import Redactor, public_payload
    root = Path(__file__).resolve().parents[1]
    paths = list((root / 'examples').glob('*исход*.docx'))
    if not paths:
        pytest.skip('Пользовательские примеры не включаются в пакет')
    for path in paths:
        original = parse_docx(path.read_bytes())
        r = Redactor()
        public = r.document(original)
        r.assert_safe(json.dumps(public_payload(public), ensure_ascii=False))
        assert len(original.blocks) == len(public.blocks)
        assert any(b.kind == 'table_row' for b in original.blocks)


def test_manual_redaction_invalidates_release(tmp_path, docx_bytes):
    service = Service(tmp_path)
    manifest = service.prepare(docx_bytes(['Внутренний идентификатор: private-unrecognized-value.']))
    old = manifest['release_digest']
    changed = service.redact_more(manifest['job_id'], 'b0001', 'private-unrecognized-value')
    assert changed['release_digest'] != old
    preview = service.preview(manifest['job_id'])
    assert 'private-unrecognized-value' not in json.dumps(preview)
    with pytest.raises(PrivacyError): service.analyze(manifest['job_id'], 'openrouter', old)


def test_real_ner_model_context(tmp_path, docx_bytes):
    from tz_audit.agent import AgentConfig
    class Fake:
        def complete(self, messages, config):
            assert 'test@example.com' not in json.dumps(messages)
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{
                'id': 'review_1', 'type': 'function', 'function': {'name': 'submit_review', 'arguments': json.dumps({
                    'summary': 'Мок-проверка передачи обезличенного контекста. Смысловой анализ не выполнялся.', 'findings': [], 'coverage': [{'topic': t, 'block_ids':['b0002'], 'result':'checked','note':'Проверены данные указанного блока.'} for t in COVERAGE_TOPICS]})}
            }]}}]}
    service = Service(tmp_path)
    manifest = service.prepare(docx_bytes(['Контактное лицо: Иван Петров; test@example.com', 'Поставка оборудования.']))
    report = service.analyze(manifest['job_id'], 'openrouter', manifest['release_digest'], client=Fake(), config=AgentConfig())
    assert report.stats['steps'] == 2


def test_local_web_session_and_csrf(tmp_path, monkeypatch):
    monkeypatch.delenv('TZ_API_TOKEN',raising=False)
    client=TestClient(create_app(tmp_path))
    assert client.get('/settings').status_code==401
    home=client.get('/')
    assert home.status_code==200
    assert 'httponly' in home.headers['set-cookie'].lower()
    assert 'samesite=strict' in home.headers['set-cookie'].lower()
    assert client.get('/settings').status_code==200
    assert client.post('/settings',headers={'origin':'https://evil.example'},json={'api_key':'a'*30}).status_code==403
    assert client.get('/',headers={'host':'evil.example'}).status_code==400
    assert client.get('/static/app.js').status_code==200
    assert "script-src 'self'" in home.headers['content-security-policy']


def test_key_not_returned(tmp_path, monkeypatch):
    monkeypatch.delenv('TZ_API_TOKEN',raising=False)
    client=TestClient(create_app(tmp_path));client.get('/')
    secret='sk-fake-secret-for-unit-testing-only'
    result=client.post('/settings',json={'api_key':secret})
    assert result.status_code==200 and secret not in result.text
    assert secret not in client.get('/settings').text


def test_privacy_choice_persisted_and_validated(tmp_path,docx_bytes):
    service=Service(tmp_path)
    manifest=service.prepare(docx_bytes(['Иван Петров; test@example.com']), 'rules')
    assert manifest['privacy_backend']=='rules'
    assert service.load(manifest['job_id'])[3].backend=='rules'
    client=TestClient(create_app(tmp_path/'api'));client.get('/')
    profiles=client.get('/settings').json()['privacy_profiles']
    assert {p['id'] for p in profiles}=={'ensemble','natasha','spacy','rules'}
    r=client.post('/jobs',data={'privacy_backend':'unknown'},files={'file':('test.docx',docx_bytes(['ТЗ']))})
    assert r.status_code==400


def test_selected_proposal_creates_new_version_without_model(tmp_path,docx_bytes,monkeypatch):
    from tz_audit.models import Finding,Anchor,Edit,RowInsertion
    from tz_audit.document import parse_docx
    raw=docx_bytes(['Лифт грузовой'],[['1','Работы','1','шт.']])
    review=Review(summary='Проверка добавления отдельной позиции и спорной правки.',findings=[Finding(category='completeness',severity='medium',status='needs_review',title='Добавить отдельную позицию',anchors=[Anchor(block_id='b0002',quote='1 | Работы | 1 | шт.')],explanation='Оборудование отсутствует в спецификации.',recommendation='Выделить оборудование отдельной строкой.',edits=[Edit(block_id='b0002',before='Работы',after='Монтаж')],row_insertions=[RowInsertion(after_block_id='b0002',cells=['2','Лифт грузовой','1','шт.'])])])
    monkeypatch.setattr('tz_audit.service.baseline',lambda public:review)
    service=Service(tmp_path/'jobs');job=service.prepare(raw,privacy_backend='rules')['job_id']
    report=service.analyze(job);original_report=(service.path(job)/'report.json').read_bytes()
    assert report.stats['applied_edits']==0
    choices=service.proposals(job)
    with pytest.raises(JobError):service.apply_selected(job,'0'*64,['f1-r1'])
    with pytest.raises(JobError):service.apply_selected(job,choices['report_digest'],['f1-r99'])
    created=service.apply_selected(job,choices['report_digest'],['f1-r1'])
    assert created['job_id']!=job and created['applied_edits']==1
    assert (service.path(job)/'report.json').read_bytes()==original_report
    document=parse_docx((service.path(created['job_id'])/'corrected.docx').read_bytes())
    assert document.blocks[-1].text=='2 | Лифт грузовой | 1 | шт.'
    next_choices=service.proposals(created['job_id'])
    again=service.apply_selected(created['job_id'],next_choices['report_digest'],['f1-e1'])
    assert again['applied_edits']==2
    assert len(parse_docx((service.path(again['job_id'])/'corrected.docx').read_bytes()).blocks)==3
