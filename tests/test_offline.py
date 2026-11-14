"""Локальные правила: общие закупочные проверки без привязки к выданным примерам."""
from tz_audit.models import Block, Document
from tz_audit.offline import baseline
from tz_audit.validation import validate_review


def doc(*items):
    blocks = []
    for i, item in enumerate(items, 1):
        text, kind = item if isinstance(item, tuple) else (item, 'paragraph')
        blocks.append(Block(id=f'b{i:04d}', text=text, location='test', kind=kind))
    return Document(sha256='a' * 64, blocks=blocks)


def run(document):
    review, sources = baseline(document)
    validate_review(review, document, sources)
    return review.findings


def titles(findings):
    return [f.title for f in findings]


def test_unknown_okpd_code_cites_local_classifier():
    findings = run(doc('Предмет закупки: поставка товара. Гарантия 12 месяцев.', ('1 | Насос | шт. | 1 | 99.99.99.999', 'table_row')))
    found = [f for f in findings if f.category == 'classification' and '99.99.99.999' in f.title]
    assert found and found[0].citations and found[0].status == 'needs_review'


def test_national_standard_without_r_gets_optional_edit():
    findings = run(doc('Работы выполняются по ГОСТ 52941-2008. Гарантия 12 месяцев.'))
    item = next(f for f in findings if f.category == 'standards')
    assert item.edits[0].before == 'ГОСТ 52941' and item.edits[0].after == 'ГОСТ Р 52941'
    assert not [f for f in run(doc('Работы по ГОСТ Р 52941-2008 и ГОСТ 12.1.004. Гарантия 12 месяцев.')) if f.category == 'standards']


def test_analog_wording_and_model_without_equivalent():
    findings = run(doc('Гарантия 12 месяцев.', ('1 | Ручка Pilot или аналог | шт. | 10', 'table_row'), ('2 | Сканер модели XR2000 | шт. | 1', 'table_row')))
    assert 'Формулировка «или аналог» вместо «или эквивалент»' in titles(findings)
    assert 'Обозначения моделей или артикулов без «или эквивалент»' in titles(findings)
    clean = run(doc('Гарантия 12 месяцев.', ('1 | Сканер XR2000 или эквивалент | шт. | 1', 'table_row'), ('2 | Кабель USB3 с разъёмом RJ45, защита IP54 | шт. | 1', 'table_row')))
    assert not [f for f in clean if f.category == 'competition']


def test_missing_warranty_and_placeholders():
    findings = run(doc('Предмет закупки: поставка мебели.', 'Срок поставки: ________ дней.'))
    assert 'В ТЗ не найдены условия гарантии' in titles(findings)
    assert 'Не заполнены обязательные сведения' in titles(findings)
    assert not [f for f in run(doc('Предмет закупки: поставка мебели. Гарантия 12 месяцев.', 'Подпись ________ / расшифровка')) if f.category == 'completeness']


def test_services_required_but_absent_from_specification():
    findings = run(doc('Поставщик должен выполнить монтаж оборудования. Гарантия 12 месяцев.',
                       ('1 | Шкаф | шт. | 2', 'table_row'), ('2 | Стол | шт. | 3', 'table_row')))
    assert any(f.category == 'completeness' and 'спецификации' in f.title for f in findings)
    assert not [f for f in run(doc('Поставщик должен выполнить монтаж. Гарантия 12 месяцев.',
                                   ('1 | Шкаф | шт. | 2', 'table_row'), ('2 | Монтаж шкафа | усл. ед. | 1', 'table_row'))) if 'спецификации' in f.title]


def test_contradicting_whole_object_value():
    findings = run(doc('Кран мостовой, грузоподъемность 5 т. Гарантия 12 месяцев.', ('Грузоподъемность, т | 3', 'table_row')))
    item = next(f for f in findings if f.category == 'contradiction')
    assert len(item.anchors) == 2 and item.status == 'needs_review'
    # Разные товары в строках одной спецификации не сравниваются между собой.
    assert not [f for f in run(doc('Гарантия 12 месяцев.', ('1 | Мощность 50 Вт | шт.', 'table_row'), ('2 | Мощность 100 Вт | шт.', 'table_row'))) if f.category == 'contradiction']


def test_warranty_on_works_from_goods_document():
    findings = run(doc('Гарантийный срок на выполненные работы исчисляется с даты подписания товарной накладной.'))
    assert any(f.category == 'warranty' for f in findings)
    assert not [f for f in run(doc('Гарантийный срок на товар исчисляется с даты подписания товарной накладной.')) if f.category == 'warranty']


def test_clean_document_has_no_findings():
    findings = run(doc('Предмет закупки: поставка бумаги для офисной техники.',
                       'Гарантийный срок на товар — 12 месяцев с даты подписания документа о приёмке.',
                       ('1 | Бумага А4, плотность 80 г/м2 | упак. | 100', 'table_row')))
    assert findings == []
