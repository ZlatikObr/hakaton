import json
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tz_audit.agent import Agent, AgentConfig, SYSTEM, AUDIT
from tz_audit.api import create_app
from tz_audit.models import Procurement, COVERAGE_TOPICS, Review
from tz_audit.privacy import Redactor, release_digest
from tests.test_agent import Fake, reply, audited


def test_procurement_defaults_and_description():
    assert Procurement().model_dump() == {'law': 'unknown', 'smp': 'unknown', 'national_regime': 'unknown'}
    text = Procurement(law='44-ФЗ', smp='yes', national_regime='no').describe()
    assert '44-ФЗ' in text and 'СМП' in text and 'не применяется' in text
    assert 'определи по предмету' in Procurement().describe()
    with pytest.raises(ValidationError): Procurement(law='94-ФЗ')
    with pytest.raises(ValidationError): Procurement(smp='maybe', extra=1)


def test_prompts_require_equivalents_regime_and_new_topics():
    assert 'или эквивалент' in SYSTEM and '1875' in SYSTEM and 'СМП' in SYSTEM
    assert 'Не предполагай применение 44-ФЗ' not in SYSTEM
    assert 'девять тем' in AUDIT and 'competition —' in AUDIT and 'regime —' in AUDIT
    assert set(COVERAGE_TOPICS) >= {'competition', 'regime'} and len(COVERAGE_TOPICS) == 9


def test_agent_sends_procurement_context_in_both_passes(public_doc, review):
    fake = Fake([reply('submit_review', review.model_dump()), audited(review)])
    config = AgentConfig(procurement={'law': '223-ФЗ', 'smp': 'yes', 'national_regime': 'unknown'})
    Agent(fake, config).run(public_doc, Redactor(False), release_digest(public_doc))
    first, second = (json.loads(call[1]['content']) for call in fake.calls)
    for payload in (first, second):
        assert '223-ФЗ' in payload['procurement_context'] and 'СМП' in payload['procurement_context']


def test_agent_without_procurement_says_regime_unknown(public_doc, review):
    fake = Fake([reply('submit_review', review.model_dump()), audited(review)])
    Agent(fake).run(public_doc, Redactor(False), release_digest(public_doc))
    assert 'Закон не указан' in json.loads(fake.calls[0][1]['content'])['procurement_context']


def test_second_pass_must_cover_competition_and_regime(public_doc, review):
    partial = review.model_dump()
    partial['coverage'] = [{'topic': t, 'block_ids': ['b0001'], 'result': 'checked', 'note': 'Проверены данные указанного блока.'}
                           for t in COVERAGE_TOPICS if t not in {'competition', 'regime'}]
    fake = Fake([reply('submit_review', review.model_dump()), reply('submit_review', partial), audited(review)])
    _, _, stats = Agent(fake).run(public_doc, Redactor(False), release_digest(public_doc))
    assert stats['steps'] == 3 and 'competition' in fake.calls[2][-1]['content']


def test_api_validates_and_passes_procurement(tmp_path, monkeypatch, docx_bytes):
    monkeypatch.setenv('TZ_API_TOKEN', 'x' * 32)
    captured = {}
    import tz_audit.service as service_module
    real = service_module.Service.analyze
    def spy(self, job_id, mode='offline', approved_digest=None, *, client=None, config=None, control=None):
        captured['procurement'] = config.procurement
        return real(self, job_id, mode, approved_digest, client=client, config=config, control=control)
    monkeypatch.setattr(service_module.Service, 'analyze', spy)
    client = TestClient(create_app(tmp_path)); h = {'Authorization': 'Bearer ' + 'x' * 32}
    job = client.post('/jobs', headers=h, files={'file': ('a.docx', docx_bytes(['Поставка лифта.']))}).json()['job_id']
    bad = client.post(f'/jobs/{job}/run', headers=h, json={'mode': 'offline', 'procurement': {'law': '94-ФЗ'}})
    assert bad.status_code == 422
    ok = client.post(f'/jobs/{job}/run', headers=h, json={'mode': 'offline', 'procurement': {'law': '44-ФЗ', 'smp': 'no', 'national_regime': 'yes'}})
    assert ok.status_code == 202
    import time
    for _ in range(100):
        if client.get(f'/jobs/{job}/status', headers=h).json()['status'] == 'complete': break
        time.sleep(0.05)
    assert captured['procurement'] == {'law': '44-ФЗ', 'smp': 'no', 'national_regime': 'yes'}
