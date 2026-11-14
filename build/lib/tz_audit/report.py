"""Deterministic local DOCX rendering. The model never writes files or OOXML."""
from collections import Counter
from io import BytesIO
from docx import Document as WordDocument
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

CATEGORIES = {'contradiction': 'Противоречия', 'competition': 'Конкуренция', 'characteristics': 'Характеристики',
    'classification': 'ОКПД 2', 'warranty': 'Гарантия', 'standards': 'Нормативные документы',
    'completeness': 'Комплектность', 'acceptance': 'Приёмка', 'other': 'Прочее'}


def render_report(report, original):
    doc = WordDocument()
    for section in doc.sections:
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.top_margin = section.bottom_margin = Cm(2)
        section.left_margin = section.right_margin = Cm(2.2)
    for name in ['Normal', 'Title', 'Heading 1', 'Heading 2']:
        style = doc.styles[name]
        style.font.name = 'DejaVu Sans'
        style.font.color.rgb = RGBColor(0, 0, 0)
    doc.styles['Normal'].font.size = Pt(10)
    doc.styles['Normal'].paragraph_format.space_after = Pt(6)
    doc.styles['Normal'].paragraph_format.line_spacing = 1.12
    doc.styles['Title'].font.size = Pt(22)
    doc.styles['Heading 1'].font.size = Pt(14)
    doc.styles['Heading 2'].font.size = Pt(11)
    # Remove inherited Word template title rules and theme borders.
    for root in [doc.element, doc.styles.element]:
        for border in list(root.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    doc.core_properties.author = 'TZ Audit'
    doc.core_properties.title = 'Проверка технического задания на закупку'
    doc.core_properties.last_modified_by = 'TZ Audit'
    doc.add_paragraph('Проверка технического задания на закупку', 'Title')
    doc.add_paragraph(report.review.summary)
    doc.add_paragraph('Режим: ' + ('локальная предварительная проверка без модели и интернета' if report.mode == 'offline' else f'анализ моделью {report.model}'))
    doc.add_paragraph('Отчёт содержит рекомендации сотруднику закупок. Применённые правки отмечены в отдельной копии ТЗ; исходный файл сохранён. Спорные предложения можно выбрать отдельно в интерфейсе. Соответствие актуальному законодательству не удостоверяется.')
    doc.add_paragraph('SHA-256 исходного документа: ' + report.document_sha256)
    if report.warnings:
        doc.add_heading('Границы проверки', 1)
        for warning in report.warnings:
            doc.add_paragraph(warning)
    counts = Counter(f.category for f in report.review.findings)
    doc.add_heading('Сводка', 1)
    doc.add_paragraph(f'Найдено замечаний: {len(report.review.findings)}. ' + '; '.join(f'{CATEGORIES[k]} — {v}' for k, v in counts.items()))
    doc.add_paragraph(f'Применено правок в копии ТЗ: {report.stats.get("applied_edits", 0)}. Предложено: {report.stats.get("proposed_edits", 0)}.')
    if report.review.coverage:
        doc.add_heading('Повторная проверка полноты', 1)
        labels = {'object':'Предмет и заголовки','numbers':'Числа и размеры','scope':'Комплектность и спецификация','warranty_acceptance':'Гарантия и приёмка','classification':'Коды товаров и работ','standards':'Нормативные ссылки','language':'Опечатки и грамматика'}
        for check in report.review.coverage:
            state = {'checked':'Проверено по указанным данным','unverified':'Не хватает подтверждения','not_applicable':'Не относится к документу'}[check.result]
            doc.add_paragraph(f'{labels[check.topic]} — {state}. {check.note} Блоки: {", ".join(check.block_ids) or "—"}.')
    if report.stats.get('research_failures'):
        doc.add_heading('Недоступные источники', 1)
        for failure in report.stats['research_failures']:
            doc.add_paragraph(f'{failure["host"]}: {failure["reason"]}')
    original_blocks = {b.id: b for b in original.blocks}
    for index, item in enumerate(report.review.findings, 1):
        doc.add_heading(f'{index} {item.title}', 1)
        severity = {'high': 'высокий', 'medium': 'средний', 'low': 'низкий'}[item.severity]
        status = 'ошибка по тексту документа' if item.status == 'document_issue' else 'требует ручной проверки'
        meta = doc.add_paragraph(f'{CATEGORIES[item.category]} • Риск: {severity} • {status}')
        meta.paragraph_format.keep_with_next = True
        for anchor in item.anchors:
            location = original_blocks.get(anchor.block_id)
            locator = doc.add_paragraph(f'{anchor.block_id} — {location.location if location else "блок исходника"}')
            locator.paragraph_format.keep_with_next = True
            p = doc.add_paragraph()
            p.add_run('Фрагмент: ').bold = True
            p.add_run(anchor.quote)
        for label, text in [('Причина', item.explanation), ('Предлагаемое исправление', item.recommendation)]:
            p = doc.add_paragraph()
            p.add_run(label + ': ').bold = True
            p.add_run(text)
        for citation in item.citations:
            source = next(s for s in report.sources if s.id == citation.source_id)
            method = 'Выдержка поискового индекса; полная страница не прочитана' if source.retrieval_method == 'search_excerpt' else ('Прямое чтение; текст сокращён' if source.truncated else 'Прямое чтение')
            doc.add_paragraph(f'Источник {source.id}: {source.url}\n{method}. Получен: {source.retrieved_at}\nЦитата: {citation.quote}')
        if item.basis == 'external' and not item.citations:
            doc.add_paragraph('Проверенный внешний источник отсутствует. Вывод не является подтверждённым правовым нарушением.')
    if report.changes:
        doc.add_heading('Журнал правок', 1)
        for change in report.changes:
            state = 'Применено' if change['status'] == 'applied' else 'Не применено'
            doc.add_heading(f'{state} {change["block_id"]}', 2)
            doc.add_paragraph('Было: ' + change['before'])
            doc.add_paragraph('Стало: ' + change['after'])
            doc.add_paragraph(change['reason'])
            if change.get('approved'):
                doc.add_paragraph('Применено по выбору пользователя.')
    footer = doc.sections[0].footer.paragraphs[0]
    footer.add_run('Проверка ТЗ • ')
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), 'PAGE')
    footer._p.append(fld)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()
