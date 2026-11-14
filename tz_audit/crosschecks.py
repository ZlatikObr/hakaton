"""Local cross-references, not a source of engineering or legal truth.

These checks only use the released document. Ambiguous pairs stay manual;
neither a majority vote nor a nearby dimension establishes the correct value.
"""
import re
from itertools import combinations

from .models import Anchor, Edit, Finding

DIMENSION = re.compile(r'(?<![\w.,])\d{2,5}(?:[.,]\d+)?\s*[xх×]\s*\d{2,5}(?:[.,]\d+)?(?![\d.,]|\s*[xх×]\s*\d)', re.I)
UNIT = re.compile(r'^\s*(мм|см|м)\b', re.I)
# «Предмет закупки: выполнение работ по <вид работ> <объект>».
SCOPE = re.compile(r'предмет\w*\s+(?:закупки|договора|контракта)\s*:.*?работ\w*\s+по\s+([а-яё]+)\s+([^\n.;|]{3,120})', re.I)
# Вид работ в заголовке спецификации: «ремонтных работ», «работ по монтажу».
HEADING_WORK = re.compile(r'([а-яё]+ных)\s+работ\b|работ\s+по\s+([а-яё]+)', re.I)


def crosschecks(document):
    findings, dimensions = [], []
    kinds = {b.id: b.kind for b in document.blocks}
    for block in document.blocks:
        # Printer/scanner resolution is not a physical size. Different models
        # and print/scan modes routinely have different pixel counts.
        if re.search(r'\b(?:dpi|т/д|пиксел\w*|разрешени\w*)\b', block.text, re.I):
            continue
        for match in DIMENSION.finditer(block.text):
            # Do not extract the last two components of a 3-D size.
            if re.search(r'\d\s*[xх×]\s*$', block.text[:match.start()], re.I):
                continue
            pair = tuple(float(n.replace(',', '.')) for n in re.split(r'\s*[xх×]\s*', match.group(), flags=re.I))
            unit = UNIT.match(block.text[match.end():])
            quote = block.text[max(0, match.start()-100):min(len(block.text), match.end()+100)]
            dimensions.append((block.id, match.group(), pair, unit.group(1).lower() if unit else '', quote))
            if len(dimensions) >= 120:
                break
        if len(dimensions) >= 120:
            break
    seen = set()
    for left, right in combinations(dimensions, 2):
        # Same axis and explicitly compatible units only; unknown units are
        # evidence to inspect, never an implicit conversion.
        if left[0] == right[0] or (left[3] and right[3] and left[3] != right[3]):
            continue
        # Different rows of one specification usually describe different items.
        if kinds[left[0]] == kinds[right[0]] == 'table_row':
            continue
        if sum(a == b for a, b in zip(left[2], right[2])) != 1:
            continue
        key = (left[0], right[0], left[2], right[2])
        if key in seen:
            continue
        seen.add(key)
        findings.append(Finding(category='characteristics', severity='medium', status='needs_review',
            title='Сопоставить близкие размеры в разных разделах',
            anchors=[Anchor(block_id=left[0], quote=left[4]), Anchor(block_id=right[0], quote=right[4])],
            explanation=f'Локальная сверка обнаружила размеры {left[1]} и {right[1]} с одной совпадающей стороной. Это ещё не доказывает ошибку: размеры могут относиться к разным объектам или единицам.',
            recommendation='Сопоставить назначения и единицы обоих размеров. Если они описывают один объект, согласовать размер и исправить только ошибочное значение. Автоматический выбор не выполнен.'))
        if len(findings) >= 10:
            break
    # A specification heading should name the same kind of work as the explicit
    # scope. Keep this optional: one kind of work can legitimately include another.
    scope = next(((b, match) for b in document.blocks if (match := SCOPE.search(b.text))), None)
    if scope:
        source, match = scope
        work, subject = match.group(1), match.group(2).strip()
        for block in document.blocks:
            if block.id == source.id or len(block.text) > 250 or not re.search(r'спецификаци[яи]\b', block.text, re.I):
                continue
            old = HEADING_WORK.search(block.text)
            kind = old and (old.group(1) or old.group(2))
            if kind and kind.casefold()[:5] != work.casefold()[:5]:
                findings.append(Finding(category='completeness', severity='low', status='needs_review',
                    title='Согласовать название спецификации с предметом закупки',
                    anchors=[Anchor(block_id=source.id, quote=source.text[:6000]), Anchor(block_id=block.id, quote=block.text)],
                    explanation=f'В предмете закупки указаны работы по {work} {subject}, а заголовок спецификации называет другой вид работ («{old.group()}»).',
                    recommendation='Если спецификация относится ко всему предмету закупки, привести название работ в заголовке к формулировке предмета.',
                    edits=[Edit(block_id=block.id, before=old.group(), after=f'работ по {work} {subject}')]))
                if len(findings) >= 12:
                    break
    return findings


def retain_unaddressed(review, checks):
    """Keep omitted local checks visible without claiming that the model checked them."""
    added = 0
    for check in checks:
        # Referencing only a block is insufficient: a table row can hold many facts.
        covered = any(all(any(a.block_id == expected.block_id and
                      (expected.quote in a.quote or a.quote in expected.quote) and
                      (check.category != 'characteristics' or any(d.group() in a.quote for d in DIMENSION.finditer(expected.quote)))
                      for a in finding.anchors) for expected in check.anchors)
                      for finding in review.findings)
        if covered:
            continue
        topic = 'numbers' if check.category == 'characteristics' else 'object'
        for coverage in review.coverage:
            if coverage.topic == topic:
                coverage.result = 'unverified'
                coverage.note = 'Модель пропустила локальное сопоставление; оно добавлено в замечания и требует проверки сотрудником.'
                coverage.block_ids = list(dict.fromkeys(coverage.block_ids + [a.block_id for a in check.anchors]))[:30]
        if len(review.findings) >= 80:
            # Preserve model results; still make incomplete local coverage explicit.
            review.summary = review.summary[:3700] + ' Часть локальных сопоставлений не вошла в лимит отчёта; полнота не подтверждена.'
            continue
        review.findings.append(check.model_copy(deep=True))
        added += 1
    if added:
        review.summary = f'Локальная сверка добавила вопросов для ручной проверки: {added}. Вывод модели до дополнения: ' + review.summary[:3700]
    return added
