from tz_audit.crosschecks import crosschecks, retain_unaddressed
from tz_audit.models import Block, Coverage, Document, Review
from tz_audit.validation import validate_review


def doc(*texts):
    return Document(sha256='a'*64, blocks=[Block(id=f'b{i:04d}', text=t, location='test') for i, t in enumerate(texts, 1)])


def test_dimensions_are_review_questions_not_automatic_replacements():
    source = doc('Проём двери: 900×2200 мм', 'Облицовка: 900х2100 мм')
    checks = crosschecks(source)
    assert len(checks) == 1 and checks[0].status == 'needs_review'
    assert not checks[0].edits
    assert len(checks[0].anchors) == 2
    validate_review(Review(summary='Локальные сопоставления размеров.', findings=checks), source, [])


def test_dimensions_dont_compare_units_equal_sizes_or_parts_of_3d():
    assert not crosschecks(doc('Размер: 900×2200 мм', 'Размер: 900х2200 мм'))
    assert not crosschecks(doc('Размер: 900×2200 мм', 'Размер: 900х2100 см'))
    assert not crosschecks(doc('Размер: 500х900х2200 мм', 'Размер: 900х2100 мм'))
    assert not crosschecks(doc('Разрешение печати: 1800 х 600 dpi', 'Разрешение сканирования: 600х600 т/д'))


def test_heading_proposal_uses_explicit_scope_and_stays_optional():
    source = doc('Предмет закупки: выполнение работ по замене насоса', 'Спецификация на выполнение ремонтных работ')
    checks = crosschecks(source)
    assert len(checks) == 1
    assert checks[0].edits[0].after == 'работ по замене насоса'
    assert checks[0].status == 'needs_review'
    assert not crosschecks(doc('Ремонт насоса', 'Спецификация на выполнение ремонтных работ'))


def test_omitted_check_cannot_disappear_or_claim_checked_coverage():
    checks = crosschecks(doc('Проём 900х2200', 'Облицовка 900х2100'))
    review = Review(summary='Проверка завершена, замечаний не найдено.', findings=[], coverage=[
        Coverage(topic='numbers', result='checked', block_ids=['b0001'], note='Размеры проверены в документе.')])
    assert retain_unaddressed(review, checks) == 1
    assert review.coverage[0].result == 'unverified'
    assert retain_unaddressed(review, checks) == 0
    assert len(review.findings) == 1


def test_quoting_a_row_number_does_not_cover_dimension_check():
    checks = crosschecks(doc('1 | Проём 900х2200', '2 | Облицовка 900х2100'))
    unrelated = checks[0].model_copy(deep=True)
    unrelated.anchors[0].quote = '1'
    unrelated.anchors[1].quote = '2'
    review = Review(summary='Проверены номера строк таблицы.', findings=[unrelated])
    assert retain_unaddressed(review, checks) == 1
