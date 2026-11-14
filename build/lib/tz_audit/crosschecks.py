"""Local cross-references, not a source of engineering or legal truth.

These checks only use the released document. Ambiguous pairs stay manual;
neither a majority vote nor a nearby dimension establishes the correct value.
"""
import re
from itertools import combinations

from .models import Anchor, Edit, Finding

DIMENSION = re.compile(r'(?<![\w.,])\d{2,5}(?:[.,]\d+)?\s*[xх×]\s*\d{2,5}(?:[.,]\d+)?(?![\d.,]|\s*[xх×]\s*\d)', re.I)
UNIT = re.compile(r'^\s*(мм|см|м)\b', re.I)
SCOPE = re.compile(r'предмет\s+закупки\s*:.*?работ\s+по\s+замене\s+([^\n.;|]{3,120})', re.I)


def crosschecks(document):
    findings, dimensions = [], []
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
    # A specification heading should name the same work as the explicit scope.
    # Keep this optional: replacement can legitimately be a part of repairs.
    scope = next(((b, match) for b in document.blocks if (match := SCOPE.search(b.text))), None)
    if scope:
        source, match = scope
        subject = match.group(1).strip()
        for block in document.blocks:
            if len(block.text) > 250 or not re.search(r'спецификаци[яи]\b', block.text, re.I):
                continue
            old = re.search(r'ремонтных\s+работ', block.text, re.I)
            if old:
                findings.append(Finding(category='completeness', severity='low', status='needs_review',
                    title='Согласовать название спецификации с предметом закупки',
                    anchors=[Anchor(block_id=source.id, quote=source.text[:6000]), Anchor(block_id=block.id, quote=block.text)],
                    explanation='В предмете закупки явно названа замена оборудования, а заголовок спецификации содержит общее название ремонтных работ.',
                    recommendation='При подтверждении, что спецификация относится ко всему указанному предмету закупки, уточнить только название работ.',
                    edits=[Edit(block_id=block.id, before=old.group(), after='работ по замене ' + subject)]))
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
