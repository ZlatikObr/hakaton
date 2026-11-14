import re
from collections import Counter
from .models import Review, COVERAGE_TOPICS
from .privacy import TOKEN
from .editing import broad_edit

class EvidenceError(ValueError):
    pass

# A digit adjacent to a unit is valid: 500кг and 500 кг must be equivalent.
UNITS = re.compile(r'(?<![^\W\d_])(?:dpi|т/д|мм|см|кг|кВт|Вт|МГц|ГГц|Гб|GB|Мб|MB)(?!\w)', re.I)
NORM = re.compile(r'ГОСТ|ОКПД|сертификат|декларац|\b(?:44|223)-ФЗ', re.I)


def reference_spelling(edit, item):
    """Only the spelling of the SAME standard ID, backed by a fetched quote.
    This does not establish that the standard is current or applicable.
    """
    compact = lambda s: re.sub(r'\s+', ' ', s).strip()
    left, right = compact(edit.before), compact(edit.after)
    if re.sub(r'ГОСТ\s+Р\b', 'ГОСТ', right) != left or left == right:
        return False
    added = re.findall(r'ГОСТ\s+Р\s+\d{1,6}(?:[.\-/]\d+)+', right)
    return bool(added) and all(any(ref in compact(c.quote) for c in item.citations) for ref in added)


def edit_review_reason(item, edit):
    # Decisions belong to individual operations, never to the whole finding.
    if re.match(r'^\s*\d+[.)]\s', edit.before) and not re.match(r'^\s*\d+[.)]\s', edit.after):
        return 'Изменение нумерации требует проверки оформления Word.'
    if Counter(x.casefold() for x in UNITS.findall(edit.before)) != Counter(x.casefold() for x in UNITS.findall(edit.after)):
        return 'Изменение единиц или их обозначений требует решения сотрудника.'
    if broad_edit(edit.before, edit.after):
        return 'Объёмная переработка выходит за пределы минимальных автоматических правок.'
    if re.search(r'КС[-– ]?[23]|акт[а-я ]{0,35}ввод[а-я ]{0,20}эксплуатац', edit.after, re.I) and edit.after != edit.before:
        return 'Изменение документов приёмки или начала гарантии требует решения сотрудника.'
    if reference_spelling(edit, item):
        return ''
    if item.status != 'document_issue':
        return 'Правильное значение не подтверждено; требуется решение сотрудника.'
    if item.basis == 'external' or item.category in {'standards', 'classification', 'competition'} or NORM.search(edit.before + ' ' + edit.after):
        return 'Применимость внешней нормы или кода требует решения сотрудника.'
    return ''


def validate_review(review: Review, document, sources, *, require_coverage=False):
    blocks = {b.id: b for b in document.blocks}
    known = {s.id: s for s in sources}
    if require_coverage and {x.topic for x in review.coverage} != set(COVERAGE_TOPICS):
        raise EvidenceError(f'Второй проход должен заполнить coverage для всех {len(COVERAGE_TOPICS)} тем: ' + ', '.join(COVERAGE_TOPICS))
    if len({x.topic for x in review.coverage}) != len(review.coverage):
        raise EvidenceError('Темы coverage не должны повторяться.')
    for check in review.coverage:
        if any(b not in blocks for b in check.block_ids) or (check.result == 'checked' and not check.block_ids):
            raise EvidenceError('Укажи существующие block_ids для выполненных проверок coverage.')
    seen = set()
    for finding_number, item in enumerate(review.findings, 1):
        for anchor in item.anchors:
            if anchor.block_id not in blocks or anchor.quote not in blocks[anchor.block_id].text:
                raise EvidenceError(f'Цитата отсутствует в указанном блоке документа: замечание {finding_number}, блок {anchor.block_id}. Скопируй точную подстроку из исходного блока.')
        key = (item.category, tuple((a.block_id, a.quote) for a in item.anchors), item.title.casefold())
        if key in seen:
            raise EvidenceError('Повторяющаяся проблема.')
        seen.add(key)
        for citation in item.citations:
            source = known.get(citation.source_id)
            if source is None or citation.quote not in source.text:
                raise EvidenceError('Непроверяемая ссылка или цитата внешнего источника.')
        for edit in item.edits:
            if edit.block_id not in blocks or edit.before not in blocks[edit.block_id].text:
                raise EvidenceError(f'Заменяемый текст отсутствует в указанном блоке: замечание {finding_number}, блок {edit.block_id}.')
            if edit.block_id not in {a.block_id for a in item.anchors}:
                raise EvidenceError('Правка должна относиться к одному из процитированных блоков.')
            if edit.before == edit.after:
                raise EvidenceError('Правка не изменяет текст.')
            if Counter(TOKEN.findall(edit.before)) != Counter(TOKEN.findall(edit.after)):
                raise EvidenceError('Правка не должна менять, удалять или добавлять скрытые персональные данные.')
            edit._review_reason = edit_review_reason(item, edit)
        for row in item.row_insertions:
            block = blocks.get(row.after_block_id)
            if block is None or block.kind != 'table_row' or row.after_block_id not in {a.block_id for a in item.anchors}:
                raise EvidenceError('Новую строку можно вставить только после процитированной строки таблицы.')
            if any(len(c) > 2000 for c in row.cells) or sum(map(len, row.cells)) > 6000 or not any(c.strip() for c in row.cells):
                raise EvidenceError('Новая строка пуста или превышает лимит текста.')
            if TOKEN.search(' '.join(row.cells)):
                raise EvidenceError('Нельзя дублировать скрытые персональные данные в новой строке.')
            # Adding a contractual position always needs an explicit local choice.
            row._review_reason = 'Добавление позиции спецификации требует решения сотрудника.'
        if item.basis == 'external' and not item.citations:
            item.status = 'needs_review'
            note = 'Внешнее основание не подтверждено доступным источником.'
            if note not in item.explanation: item.explanation += ' ' + note
        if item.status == 'document_issue' and item.category == 'contradiction' and len(item.anchors) < 2:
            item.status = 'needs_review'
            note = 'Для автоматического устранения противоречия нужны цитаты обеих сторон; оставлено на проверку.'
            if note not in item.explanation: item.explanation += ' ' + note
    return review
