"""Conservative local baseline, explicitly not a semantic LLM substitute."""
import re
from .models import Anchor, Finding, Review

def baseline(document):
    findings = []
    kit_findings = {}
    def add(category, title, blocks, explanation, recommendation, external=False):
        anchors = []
        for block in blocks[:4]:
            quote = block.text[:3000]
            if category == 'competition':
                quote = next((line for line in block.text.split('\n') if 'национального режима' in line or 'нац.режим' in line), quote)
            if category == 'acceptance':
                quote = next((line for line in block.text.split('\n') if 'пусконаладка и инструктаж' in line.casefold()), quote)
            anchors.append(Anchor(block_id=block.id, quote=quote))
        if category == 'classification':
            item = re.search(r'(?:комплект[^|\n]*|Электроизмерительный стенд)', blocks[0].text, re.I)
            key = item.group().strip().casefold() if item else None
            if key and key in kit_findings:
                kit_findings[key].anchors.extend(anchors)
                return
        finding = Finding(category=category, severity='medium', status='needs_review', title=title,
            anchors=anchors, explanation=explanation,
            recommendation=recommendation, basis='external' if external else 'document')
        findings.append(finding)
        if category == 'classification' and key:
            kit_findings[key] = finding
    blocks = document.blocks
    text = '\n'.join(b.text for b in blocks)
    for b in blocks:
        t = b.text.casefold()
        if 'гарант' in t and 'работ' in t and ('товарной накладной' in t or 'приемку товара' in t):
            add('warranty', 'Основание начала гарантии на работы требует уточнения', [b],
                'Гарантия на работы привязана к приёмке товара. Для смешанной поставки и работ события приёмки могут различаться.',
                'Разделить гарантии на товар и работы; согласовать документы и даты приёмки для каждой части.')
        if re.search(r'\b(?:компл\.|комплект)', t) and re.search(r'\b\d{2}\.\d{2}\.\d{2}\.\d{3}\b', t):
            if b.kind == 'table_row' and len(findings) < 18:
                add('classification', 'Проверить состав комплекта и применимость кода ОКПД 2', [b],
                    'У комплекта указан общий код. По этой строке нельзя подтвердить, что он охватывает все самостоятельные товары; это не доказательство ошибки.',
                    'Сопоставить состав с классификатором; при необходимости выделить самостоятельные товары с количеством и кодом.', True)
        if 'пусконаладка и инструктаж' in t and 'в случаях' in t:
            add('acceptance', 'Уточнить позиции с пусконаладкой и инструктажем', [b],
                'Общая отсылка сама по себе не задаёт перечень позиций, объём и критерии приёмки этих услуг.',
                'Проверить по каждой позиции необходимость ПНР и инструктажа; указать объём, результат и акт приёмки.')
        if 'национального режима' in t or 'нац.режим' in t:
            add('competition', 'Применимость национального режима требует отдельной проверки', [b],
                'Применимость зависит от закупочного режима, даты и предмета. Из общего текста ТЗ эти условия не следуют.',
                'Проверить режим закупки и актуальные требования по позициям; не делать вывод только по этой фразе.', True)
    return Review(summary=f'Локальная предварительная проверка. Количество замечаний для ручного рассмотрения: {len(findings)}. Семантическая проверка моделью и проверка актуальности норм не выполнялись.', findings=findings)
