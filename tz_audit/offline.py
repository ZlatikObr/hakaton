"""Локальная проверка без LLM: общие правила закупочной документации.

Правила не опираются на названия товаров, формулировки или эталонные примеры:
они проверяют структуру и типовые требования к ТЗ (коды ОКПД 2 по справочнику,
обозначения стандартов, гарантию, эквиваленты, незаполненные поля, сопутствующие
работы, противоречивые значения). Все выводы помечаются для проверки сотрудником:
это предварительный скрининг, а не замена смысловому анализу моделью.
"""
import re
from collections import defaultdict

from .crosschecks import crosschecks
from .models import Anchor, Citation, Edit, Finding, Review
from .reference import reference, norm_source, norm_line

OKPD_FULL = re.compile(r'(?<![\d.])\d{2}\.\d{2}\.\d{2}\.\d{3}(?![\d.])')
# Пятизначные номера от 50000 используются для национальных стандартов (ГОСТ Р).
GOST_WITHOUT_R = re.compile(r'ГОСТ\s+(?!Р\b)([5-7]\d{4})(?=[\s\-–.,;:)]|$)')
ANALOG = re.compile(r'или\s+аналог\w*', re.I)
EQUIVALENT = re.compile(r'эквивалент', re.I)
# Обозначение модели или артикула: латиница и не менее двух цифр (C1000, CFX96, A1517001).
MODEL_CODE = re.compile(r'(?<![\w-])(?=[A-Za-z0-9-]*[A-Za-z])(?=(?:[A-Za-z-]*\d){2})[A-Za-z][A-Za-z0-9-]{3,}(?![\w-])')
NOT_A_MODEL = re.compile(r'^(?:IP|USB|RJ|HDMI|DDR|PCI|PCIE|WI-?FI|LTE|CAT|ISO|IEC|EN|DIN|ASTM|AISI|GOST|SN|DN|PN|AC|DC|HD|UHD|FHD|CPU|GPU|SSD|HDD|RAM|LED|LCD|RAL|TN|IPS|SATA|NVME|AWG|UTP|FTP|SFP|[A-Z])-?\d', re.I)
PLACEHOLDER = re.compile(r'_{4,}|\[\s*(?:указать|заполнить|вписать)[^\]]*\]|\(\s*(?:указать|заполнить)[^)]*\)|<\s*(?:указать|заполнить)[^>]*>|(?<!\w)[XХ]{2,}(?!\w)', re.I)
REQUIRED_DATA = re.compile(r'срок|количеств|гарант|адрес|мест\w* (?:поставки|выполнения|оказания)|цен|сумм|объ[её]м', re.I)
SIGNATURE = re.compile(r'подпис|м\.\s?п\.|ф\.?\s?и\.?\s?о\.?|расшифровк', re.I)
SERVICES = re.compile(r'монтаж\w*|пусконаладоч\w*|пусконаладк\w*|ввод\w* в эксплуатацию|инструктаж\w*|обучени\w*|установк\w* оборудования', re.I)
OBLIGATION = re.compile(r'должен|должна|должны|обязан|осуществля|выполня|входит|включа|обеспечива|производит', re.I)
QTY_CELL = re.compile(r'(?:^|\|)\s*\d+(?:[.,]\d+)?\s*(?:\||$)|(?<!\w)(?:шт|компл|ед|упак)\.?(?!\w)', re.I)
REGIME = re.compile(r'национальн\w+\s+режим\w*|нац\.\s?режим|стран\w+ происхождени\w*|реестр\w* российской', re.I)
# Характеристики, относящиеся к изделию целиком: разные значения у одного изделия — повод сверить.
WHOLE_OBJECT = r'грузоподъ[её]мност\w*|мощност\w*|напряжени\w+ питания|производительност\w*|ёмкост\w*|емкост\w*'
PARAMETER = re.compile(r'(' + WHOLE_OBJECT + r')\s*(?:[:\-–—])?\s*(не менее|не более|до|от|не ниже|не выше|около)?\s*(\d+(?:[.,]\d+)?)\s*(кг|т|кВт|Вт|В|л|А·ч|Ач)(?![\w/])', re.I)
# Табличная форма «Грузоподъёмность, кг | 300».
PARAMETER_CELL = re.compile(r'(' + WHOLE_OBJECT + r')\s*,\s*(кг|т|кВт|Вт|В|л|А·ч|Ач)\s*\|\s*(\d+(?:[.,]\d+)?)(?![\d.,])', re.I)


def _line(block, match):
    """Короткая дословная цитата: строка или ячейка таблицы, содержащая совпадение."""
    text = block.text
    cuts = [text.rfind('\n', 0, match.start()) + 1]
    cell = text.rfind(' | ', 0, match.start())
    if cell != -1:
        cuts.append(cell + 3)
    start = max(cuts)
    ends = [i for i in (text.find('\n', match.end()), text.find(' | ', match.end())) if i != -1]
    quote = text[start:min(ends) if ends else len(text)].strip()
    return (quote if quote and quote in text else match.group())[:1500]


def _quote(block, match, minimum=20):
    quote = _line(block, match)
    return quote if len(quote) >= minimum else block.text[:1500]


def _subject_block(blocks):
    for block in blocks:
        if re.search(r'предмет\w*\s+(?:закупки|договора|контракта)|наименование\s+(?:объекта|товара|работ|услуг)', block.text, re.I):
            return block
    return next((b for b in blocks if b.kind == 'paragraph' and len(b.text) > 15), blocks[0])


REGISTRY_DENIAL = re.compile(r'(?:наличие|включение)[^.\n]{0,80}реестр[^.\n]{0,80}не является[^.\n]{0,40}требовани\w*|страна происхождения[^.\n]{0,30}(?:не имеет значения|любая|не важна)', re.I)


def baseline(document):
    """Вернуть (Review, sources): замечания и выписки справочника, на которые они ссылаются."""
    blocks = document.blocks
    text = '\n'.join(b.text for b in blocks)
    findings, sources = [], []
    ref = reference()
    norm_ids = {}

    def norm_citation(norm_id, prefix):
        # Выдержка нормы — локальный источник; цитата дословная.
        if norm_id not in norm_ids:
            norm_ids[norm_id] = f'z{len(norm_ids) + 1}'
            sources.append(norm_source(norm_id, norm_ids[norm_id]))
        return Citation(source_id=norm_ids[norm_id], quote=norm_line(norm_id, prefix))

    def source_for(code):
        source = ref.source(code, f'r{len(sources) + 1}')
        sources.append(source)
        return source

    # 1. Коды ОКПД 2: наличие в классификаторе и меры национального режима.
    missing, regime_codes, seen = [], [], set()
    for block in blocks:
        for match in OKPD_FULL.finditer(block.text):
            code = match.group()
            if code in seen:
                continue
            seen.add(code)
            info = ref.describe(code)
            if not info['found']:
                missing.append((code, block, match))
            elif any('позиция' in m for m in info['national_regime']):
                regime_codes.append((code, block, match))
    for code, block, match in missing[:6]:
        source = source_for(code)
        findings.append(Finding(category='classification', severity='high', status='needs_review',
            title=f'Код {code} отсутствует в классификаторе ОКПД 2',
            anchors=[Anchor(block_id=block.id, quote=_line(block, match))],
            explanation=f'Код {code} не найден в действующей редакции ОКПД 2 (ОК 034-2014). Возможна опечатка или устаревший код; по неверному коду закупку могут отклонить или признать описание предмета некорректным.',
            recommendation='Подобрать код по наименованию товара или работы в классификаторе ОКПД 2 и заменить его; выбор кода согласовать с ответственным за закупку.',
            basis='external', citations=[Citation(source_id=source.id, quote=source.text[:2000])]))
    if regime_codes and not REGIME.search(text):
        anchors, citations = [], []
        for code, block, match in regime_codes[:4]:
            anchors.append(Anchor(block_id=block.id, quote=_line(block, match)))
            source = source_for(code)
            citations.append(Citation(source_id=source.id, quote=source.text[:2000]))
        findings.append(Finding(category='competition', severity='medium', status='needs_review',
            title='Для кодов ОКПД 2 действуют меры национального режима, в ТЗ они не учтены',
            anchors=anchors, citations=citations, basis='external',
            explanation='По локальному справочнику ПП РФ № 1875 для указанных кодов предусмотрены запрет, ограничение или преимущество для российских товаров. В тексте ТЗ нет требований к стране происхождения или указания на национальный режим.',
            recommendation='Проверить, применяется ли национальный режим к закупке, и при необходимости добавить требования к стране происхождения по ПП РФ № 1875.'))

    # 1.1. Фраза о том, что реестр или страна происхождения не важны, противоречит национальному режиму.
    for block in blocks:
        match = REGISTRY_DENIAL.search(block.text)
        if match:
            findings.append(Finding(category='competition', severity='high', status='needs_review',
                title='Формулировка о реестре или стране происхождения противоречит национальному режиму',
                anchors=[Anchor(block_id=block.id, quote=_line(block, match))], basis='external',
                citations=[norm_citation('44fz_33', '1.1. '), norm_citation('223fz_3_1_4', '1. ')],
                explanation='Фраза выводит из описания требования к стране происхождения и реестру российской продукции. При действии запрета, ограничения или преимущества по ПП РФ № 1875 товар описывается как российский, такая оговорка противоречит закону.',
                recommendation='Удалить фразу и указать требования к стране происхождения и подтверждающим документам по мерам ПП РФ № 1875 для кодов ОКПД 2 закупки.'))
            break

    # 2. Обозначения стандартов: номер национального стандарта без «Р».
    for block, match in [(b, m) for b in blocks for m in GOST_WITHOUT_R.finditer(b.text)][:6]:
        before = match.group()
        findings.append(Finding(category='standards', severity='medium', status='needs_review',
            title=f'Обозначение «ГОСТ {match.group(1)}» без буквы «Р»',
            anchors=[Anchor(block_id=block.id, quote=_line(block, match))],
            explanation=f'Номер {match.group(1)} относится к диапазону национальных стандартов, которые обозначаются «ГОСТ Р». Ссылка без «Р» не позволяет однозначно определить применимый стандарт.',
            recommendation='Проверить обозначение и действующую редакцию в Федеральном информационном фонде стандартов (standards.rst.gov.ru) и исправить на «ГОСТ Р …».',
            edits=[Edit(block_id=block.id, before=before, after='ГОСТ Р ' + match.group(1))]))

    # 3. Конкуренция: «или аналог» и модели без «или эквивалент».
    analogs = [(b, m) for b in blocks if (m := ANALOG.search(b.text))][:4]
    for block, match in analogs:
        findings.append(Finding(category='competition', severity='high', status='needs_review',
            title='Формулировка «или аналог» вместо «или эквивалент»',
            anchors=[Anchor(block_id=block.id, quote=_line(block, match))],
            explanation='Законодательство о закупках допускает указание товарного знака только со словами «или эквивалент». «Аналог» не задаёт критериев равнозначности и может трактоваться как ограничение конкуренции.',
            recommendation='Заменить «или аналог» на «или эквивалент» и указать параметры эквивалентности.',
            basis='external', citations=[norm_citation('44fz_33', 'а) ')],
            edits=[Edit(block_id=block.id, before=match.group(), after='или эквивалент')]))
    model_anchors = []
    for block in blocks:
        if EQUIVALENT.search(block.text) or re.search(r'ГОСТ|\bТУ\b|СНиП|\bСП\s\d', block.text):
            continue
        match = next((m for m in MODEL_CODE.finditer(block.text) if not NOT_A_MODEL.match(m.group())), None)
        if match:
            model_anchors.append(Anchor(block_id=block.id, quote=_line(block, match)))
        if len(model_anchors) >= 6:
            break
    if model_anchors:
        findings.append(Finding(category='competition', severity='medium', status='needs_review',
            title='Обозначения моделей или артикулов без «или эквивалент»',
            anchors=model_anchors, basis='external',
            explanation='В строках указаны обозначения, похожие на модели, артикулы или каталожные номера, без слов «или эквивалент». Если это требование к конкретному товару, круг участников сужается.',
            recommendation='Проверить, является ли обозначение моделью или артикулом. Если да — добавить «или эквивалент» и описать характеристики, по которым оценивается эквивалентность.'))

    # 4. Гарантия: условий нет или гарантия на работы отсчитывается от документа о поставке.
    if not re.search(r'гарант', text, re.I) and re.search(r'поставк|оборудовани|товар|работ', text, re.I):
        subject = _subject_block(blocks)
        findings.append(Finding(category='warranty', severity='low', status='needs_review',
            title='В ТЗ не найдены условия гарантии',
            anchors=[Anchor(block_id=subject.id, quote=subject.text[:1500])],
            explanation='Ни в одном разделе и таблице документа нет упоминания гарантии. Без гарантийного срока и порядка устранения недостатков заказчик не сможет предъявить требования поставщику.',
            recommendation='Добавить гарантийный срок, момент его начала (дата подписания документа о приёмке) и порядок устранения недостатков.'))
    goods_doc = re.compile(r'товарн\w*\s+накладн|\bУПД\b|универсальн\w*\s+передаточн|при[её]мк\w*\s+товар|поставк\w*\s+товар', re.I)
    for block in blocks:
        # Условие проверяется внутри одного предложения, а не всей ячейки.
        sentence = next((x for x in re.split(r'(?<=[.;])\s+|\n', block.text) if re.search(r'гарант', x, re.I)
                         and re.search(r'\bработ|\bуслуг', x, re.I) and goods_doc.search(x)
                         and re.search(r'срок\w*|исчисля|с даты|с момента', x, re.I)), None)
        if sentence:
            findings.append(Finding(category='warranty', severity='medium', status='needs_review',
                title='Начало гарантии на работы привязано к документу о поставке',
                anchors=[Anchor(block_id=block.id, quote=sentence.strip()[:1500])],
                explanation='Гарантия на работы отсчитывается от документа о передаче товара. Даты приёмки товара и выполненных работ могут различаться, и условие гарантии на работы становится неоднозначным.',
                recommendation='Разделить гарантию на товар и на работы; для работ указать начало с даты подписания документа о приёмке выполненных работ.'))
            break

    # 5. Незаполненные поля и шаблонные заглушки.
    placeholders = []
    for block in blocks:
        match = PLACEHOLDER.search(block.text)
        if match and not SIGNATURE.search(block.text) and REQUIRED_DATA.search(block.text):
            placeholders.append(Anchor(block_id=block.id, quote=_quote(block, match, len(match.group()) + 1)))
        if len(placeholders) >= 8:
            break
    if placeholders:
        findings.append(Finding(category='completeness', severity='medium', status='needs_review',
            title='Не заполнены обязательные сведения',
            anchors=placeholders,
            explanation='В документе остались пропуски или шаблонные заглушки там, где должны быть сроки, количества, адреса или условия. Неполное ТЗ не позволяет участнику сформировать предложение.',
            recommendation='Заполнить пропущенные значения или удалить неприменимые пункты шаблона.'))

    # 6. Сопутствующие работы упомянуты в требованиях, но не отражены в спецификации.
    rows = [b for b in blocks if b.kind == 'table_row']
    if len([b for b in rows if QTY_CELL.search(b.text)]) >= 2 and not any(SERVICES.search(b.text) for b in rows):
        for block in blocks:
            match = SERVICES.search(block.text) if block.kind == 'paragraph' else None
            if match and OBLIGATION.search(block.text):
                findings.append(Finding(category='completeness', severity='medium', status='needs_review',
                    title=f'Работы («{match.group()}») указаны в требованиях, но отсутствуют в спецификации',
                    anchors=[Anchor(block_id=block.id, quote=_quote(block, match))],
                    explanation='Текст ТЗ обязывает выполнить сопутствующие работы или услуги, но в таблице спецификации нет соответствующей позиции. Объём, цена и приёмка этих работ остаются неопределёнными.',
                    recommendation='Добавить в спецификацию отдельную позицию или явно указать, что работы включены в цену товара, с объёмом и порядком приёмки.'))
                break

    # 7. Комплект с единым кодом: возможно, нужна декомпозиция на самостоятельные товары.
    kits = [b for b in rows if re.search(r'(?<!\w)(?:компл\.?|комплект\w*|набор\w*)(?!\w)', b.text, re.I) and OKPD_FULL.search(b.text)]
    if kits:
        findings.append(Finding(category='classification', severity='low', status='needs_review',
            title='Комплекты с единым кодом ОКПД 2',
            anchors=[Anchor(block_id=b.id, quote=_quote(b, OKPD_FULL.search(b.text))) for b in kits[:6]],
            explanation='Для комплекта указан один код. Если в состав входят самостоятельные товары разного назначения, единый код может не охватывать их все; само по себе это не доказывает ошибку.',
            recommendation='Сверить состав комплекта с классификатором; при необходимости выделить самостоятельные товары отдельными позициями с количеством и кодом.'))

    # 8. Упоминание национального режима без определённых условий применения.
    regime = next(((b, m) for b in blocks if (m := re.search(r'национальн\w+\s+режим\w*|нац\.\s?режим', b.text, re.I))), None)
    if regime:
        block, match = regime
        findings.append(Finding(category='competition', severity='low', status='needs_review',
            title='Условия национального режима требуют проверки',
            anchors=[Anchor(block_id=block.id, quote=_line(block, match))], basis='external',
            explanation='Применимость национального режима зависит от закона, предмета и кодов ОКПД 2. Из текста фрагмента нельзя определить, какие меры действуют.',
            recommendation='Сверить коды ОКПД 2 с приложениями ПП РФ № 1875 и привести формулировку в соответствие с применимой мерой.'))

    # 9. Разные значения одной характеристики изделия: описание против таблиц.
    # Сравниваются только значения, если хотя бы одно стоит в тексте описания: разные
    # строки одной спецификации обычно относятся к разным товарам.
    values = defaultdict(dict)
    for block in blocks:
        hits = [(m.group(1), m.group(4), m.group(3), m) for m in PARAMETER.finditer(block.text) if not m.group(2)]
        hits += [(m.group(1), m.group(2), m.group(3), m) for m in PARAMETER_CELL.finditer(block.text)]
        for name, unit, value, match in hits:
            key = (name.casefold()[:9], unit.casefold())
            values[key].setdefault(value.replace(',', '.'), (block, match, name))
    for (_, unit), distinct in values.items():
        located = list(distinct.values())
        if len(distinct) >= 2 and len({b.id for b, _, _ in located}) >= 2 and any(b.kind == 'paragraph' for b, _, _ in located):
            pairs = list(distinct.items())[:3]
            findings.append(Finding(category='contradiction', severity='high', status='needs_review',
                title=f'Разные значения характеристики «{pairs[0][1][2].lower()}»',
                anchors=[Anchor(block_id=b.id, quote=_line(b, m)) for _, (b, m, _) in pairs],
                explanation='В разных частях документа для одной характеристики указаны разные значения: ' + ', '.join(f'{v} {unit}' for v, _ in pairs) + '. Если они относятся к одному изделию, участник не сможет определить требуемое значение.',
                recommendation='Уточнить у инициатора закупки требуемое значение и привести все упоминания к нему.'))

    # 10. Локальные сверки размеров и названия спецификации.
    findings.extend(crosschecks(document))

    findings = findings[:80]
    high = sum(f.severity == 'high' for f in findings)
    summary = (f'Локальная предварительная проверка без модели. Замечаний для рассмотрения: {len(findings)}, из них высокого риска: {high}. '
               + ('' if findings else 'Типовых нарушений по локальным правилам не найдено. ')
               + 'Смысловая проверка моделью, поиск норм в интернете и юридическая оценка не выполнялись; все выводы требуют решения сотрудника закупок.')
    return Review(summary=summary, findings=findings), sources
