import json
import pytest
from tz_audit.reference import reference, Reference, CODE_RE
from tz_audit.models import Block, Document, Review
from tz_audit.agent import Agent, AgentConfig
from tz_audit.privacy import Redactor, release_digest
from tests.test_agent import Fake, reply, audited


@pytest.fixture(scope='module')
def ref(): return reference()


def test_data_is_complete_and_verified(ref):
    assert len(ref.records) > 20000 and len(ref.positions) > 800
    assert ref.okpd_version == '01.10.2026' and ref.pp_version.startswith('2026-')


def test_corrupted_data_is_rejected(tmp_path):
    import shutil
    from tz_audit.reference import DATA
    for name in ('pp1875.json.gz', 'pp1875_ref.json.gz', 'pp1875_meta.json'): shutil.copy(DATA / name, tmp_path / name)
    meta = json.loads((tmp_path / 'pp1875_meta.json').read_text()); meta['data_hash_full'] = '0' * 64
    (tmp_path / 'pp1875_meta.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='контрольная сумма'): Reference(tmp_path)


@pytest.mark.parametrize('code,name,measure', [
    ('43.29.19.110', 'монтажу лифтов', None),
    ('43.29.19.120', 'дверей автоматического', None),
    ('28.22.16.112', 'Лифты грузовые', 'ограничение допуска'),
    ('14.14.30.110', 'Футболки', 'запрет допуска'),
    ('26.51.66.190', 'измерения или контроля', 'минимальная доля'),
])
def test_known_codes_from_reference_tz(ref, code, name, measure):
    text = ref.text(code)
    assert name in text
    assert (measure in text) if measure else 'мер ПП № 1875 для кода нет' in text


def test_wrong_code_shows_automotive_group(ref):
    assert 'Части и принадлежности для автотранспортных средств' in ref.text('29.32.30.260')


def test_unknown_code(ref):
    info = ref.describe('99.99.99.999')
    assert info['found'] is False and 'опечатка' in info['note']


def test_codes_are_found_in_document_but_not_dates_or_numbers(ref):
    doc = Document(sha256='a' * 64, blocks=[
        Block(id='b0001', text='1 | Работы по замене лифта | усл.ед. | 1 | 43.29.19.120', location='b0001'),
        Block(id='b0002', text='Дата 23.04.2025, цена 26.51 руб., версия 1.2.3, код 28.22.16.112; 43.29.19.120', location='b0002')])
    assert ref.codes_in(doc) == ['43.29.19.120', '28.22.16.112']


@pytest.mark.parametrize('query,expected', [('лифт грузовой', '28.22.16.112'), ('ручка шариковая', '32.99.12.110'),
                                            ('реагенты лабораторные', '20.59.52.199'), ('рюкзак', '15.12.12.192')])
def test_search_finds_candidates(ref, query, expected):
    assert expected in ref.search(query)


def test_search_ignores_empty_query(ref):
    assert ref.search('и в на') == [] and ref.search('') == []


def lift_doc():
    return Document(sha256='b' * 64, blocks=[
        Block(id='b0001', text='Предмет: замена грузового лифта', location='b0001'),
        Block(id='b0002', text='1 | Работы по замене грузового лифта | усл.ед. | 1 | 43.29.19.120', location='b0002')])


def code_finding(source_id):
    return Review(summary='Код работ не соответствует предмету закупки.', findings=[{
        'category': 'classification', 'severity': 'high', 'status': 'needs_review', 'title': 'Код ОКПД 2 относится к монтажу дверей',
        'anchors': [{'block_id': 'b0002', 'quote': '43.29.19.120'}], 'basis': 'external',
        'explanation': 'По классификатору код означает монтаж автоматических дверей, а не лифтов.',
        'recommendation': 'Заменить код на 43.29.19.110 после подтверждения.',
        'citations': [{'source_id': source_id, 'quote': 'Работы по монтажу дверей автоматического действия'}]}])


def test_agent_gives_reference_and_accepts_its_citations():
    review = code_finding('k1')
    fake = Fake([reply('submit_review', review.model_dump()), audited(review)])
    result, sources, stats = Agent(fake).run(lift_doc(), Redactor(False), release_digest(lift_doc()))
    system = fake.calls[0][0]['content']
    assert '43.29.19.120' in system and 'дверей автоматического действия' in system and '"k1"' in system
    assert 'дверей автоматического действия' in fake.calls[1][0]['content']  # и на втором проходе
    assert result.findings[0].citations[0].source_id == 'k1'
    assert any(s.id == 'k1' and 'rosstat' in s.url for s in sources) and stats['okpd2_codes'] == 1


def test_lookup_tool_returns_citable_candidates():
    review = code_finding('k1')
    fake = Fake([reply('lookup_okpd2', {'query': 'монтаж лифтов'}), reply('submit_review', review.model_dump()), audited(review)])
    _, sources, stats = Agent(fake, AgentConfig(web_enabled=False)).run(lift_doc(), Redactor(False), release_digest(lift_doc()))
    tool_result = json.loads(fake.calls[1][-1]['content'])
    codes = [r['code'] for r in tool_result['results']]
    assert '43.29.19.110' in codes and all(r['source_id'].startswith('k') for r in tool_result['results'])
    assert stats['okpd2_lookups'] == 1
    assert '43.29.19.110' in fake.calls[2][0]['content']  # кандидат попал в справочник второго прохода


def test_lookup_rejects_bad_arguments():
    review = code_finding('k1')
    fake = Fake([reply('lookup_okpd2', {'query': 'x', 'extra': 1}), reply('submit_review', review.model_dump()), audited(review)])
    Agent(fake).run(lift_doc(), Redactor(False), release_digest(lift_doc()))
    assert 'Аргументы не соответствуют' in fake.calls[1][-1]['content']


def test_reference_is_in_system_message_not_in_dlp_checked_payload():
    # Названия кодов — серверные данные; они не должны попадать под фильтр ПД вместе с документом.
    review = code_finding('k1')
    fake = Fake([reply('submit_review', review.model_dump()), audited(review)])
    Agent(fake).run(lift_doc(), Redactor(False), release_digest(lift_doc()))
    assert 'дверей автоматического' not in fake.calls[0][1]['content']
