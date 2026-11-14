import json
import pytest
from tz_audit.privacy import Redactor, PrivacyError, check_release, release_digest, public_payload

@pytest.mark.parametrize('private', ['test@example.com', '+7 (999) 123-45-67', '123-456-789 00',
    'Иванов И. И.', 'И. И. Петров', 'Паспорт 1234 567890', '123456789012',
    'John Smith', 'ул. Лесная, д. 3', 'дата рождения: 01.02.1990', '@private_handle'])
def test_regex_redaction(private):
    r = Redactor(use_ner=False)
    result = r.redact(private)
    assert private not in result
    assert r.restore(result) == private
    r.assert_safe(result)
    with pytest.raises(PrivacyError): r.assert_safe(private)

def test_names_local_ner():
    r = Redactor()
    text = 'Документ согласовали Иван Петров и Анна. Контакт: anna@example.com.'
    result = r.redact(text)
    assert 'Петров' not in result and 'Анна' not in result and 'anna@' not in result
    r.assert_safe(result)

def test_equipment_passport_kept():
    r = Redactor(use_ner=False)
    s = 'Поставщик передает паспорт оборудования и руководство по эксплуатации.'
    assert r.redact(s) == s


def test_external_pii_is_hidden_without_mutating_document_vault():
    r = Redactor(False)
    r.vault['⟦PRIVATE_0001⟧'] = 'Закрытое место'
    r.counts = {'ADDRESS': 1}
    before = (dict(r.vault), dict(r.counts), set(r.public_urls))
    result = r.redact_external('Закрытое место; test@example.com; Иванов И. И.; ⟦PRIVATE_0001⟧')
    assert 'Закрытое место' not in result and 'test@example.com' not in result and 'Иванов' not in result
    assert 'PRIVATE_' not in result  # Source markers can never restore as document PII.
    assert (r.vault,r.counts,r.public_urls) == before
    r.assert_safe(result)
    assert r.restore(result) == result


def test_external_ner_name_does_not_reclassify_approved_document_text(monkeypatch):
    r = Redactor(False)
    approved = 'Модель Северный.'
    r.assert_safe(approved)
    original_spans = Redactor.spans
    def contextual_spans(self, text):
        spans = original_spans(self, text)
        if 'Документ подписал Северный.' in text:
            start = text.index('Северный')
            spans.append((start,start+len('Северный'),'PERSON'))
        return spans
    monkeypatch.setattr(Redactor, 'spans', contextual_spans)
    assert 'Северный' not in r.redact_external('Документ подписал Северный.')
    assert r.vault == {}
    r.assert_safe(approved)

def test_no_json_cross_field_match():
    r = Redactor(use_ner=False)
    r.assert_safe(json.dumps({'a': 'Работы по адресу:', 'b': 'Срок 105 дней.'}, ensure_ascii=False))

def test_technical_values_preserved():
    r = Redactor(use_ner=False)
    s = 'ГОСТ Р 53780-2010, ОКПД2 43.29.19.120, 500 кг, 850х2100 мм, срок 105 дней'
    assert r.redact(s) == s

def test_release_binding(public_doc):
    digest = release_digest(public_doc)
    check_release(public_doc, digest)
    public_doc.blocks[0].text += ' changed'
    with pytest.raises(PrivacyError): check_release(public_doc, digest)
    with pytest.raises(PrivacyError): check_release(public_doc, None)

def test_invisible_email():
    r = Redactor(use_ner=False)
    assert 'example' not in r.redact('test@exam\u200bple.com')


@pytest.mark.parametrize('backend',['natasha','spacy','ensemble'])
def test_selectable_local_names_and_international_contacts(backend):
    r=Redactor(backend=backend)
    text='Документ подписал Иван Петров. Анна получила копию. Телефон +49 30 12345678; mail test@example.com'
    public=r.redact(text)
    for value in ['Иван','Петров','Анна','+49 30 12345678','test@example.com']:
        assert value not in public
    assert r.restore(public)==text
    r.assert_safe(public)


def test_rules_profile_and_unknown_detector():
    assert Redactor(backend='rules').redact('Иван Петров')=='Иван Петров'
    with pytest.raises(PrivacyError):Redactor(backend='remote')


@pytest.mark.parametrize('backend',['natasha','spacy','ensemble'])
def test_detectors_preserve_technical_terms(backend):
    text='Грузоподъемность 500 кг. ГОСТ Р 53782-2010. Метод Роквелла. Датчик Холла.'
    assert Redactor(backend=backend).redact(text)==text


@pytest.mark.parametrize('backend',['natasha','spacy','ensemble'])
def test_procurement_roles_and_known_name_boundaries(backend):
    r=Redactor(backend=backend)
    text='Поставщика | Товара | Заказчику | Акта | Предмет контракта | Заказчиком Товар | Товара Поставщиком'
    assert r.redact(text)==text
    r=Redactor(False)  # Isolate vault propagation from statistical NER decisions.
    r.vault['⟦PRIVATE_0001⟧']='Анна'
    assert r.redact('Стеклянная ванна.')=='Стеклянная ванна.'
    assert 'Анна' not in r.redact('Анна согласовала документ.')
