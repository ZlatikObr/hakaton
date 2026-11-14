"""Apply validated local replacements to original OOXML; preserve unrelated ZIP members.
A replacement may span text runs but never cells, fields, drawings or tracked changes.
"""
import io
import re
from copy import deepcopy
from difflib import SequenceMatcher
from itertools import count
from datetime import datetime, timezone
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as ET
from .document import parse_docx, text_of, W
from .privacy import TOKEN, SPACE_FOLD

XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def mapped_blocks(roots):
    result = {}
    index = 0
    for part, root in roots.items():
        body = root.find(W+'body') if part == 'word/document.xml' else root
        for node in body:
            candidates = []
            if node.tag == W+'tbl':
                for row in node.findall(W+'tr'):
                    paragraphs = row.findall('.//'+W+'p')
                    cells = ['\n'.join(text_of(p) for p in c.findall('.//'+W+'p') if text_of(p)) for c in row.findall(W+'tc')]
                    candidates.append((' | '.join(cells), paragraphs))
            elif node.tag == W+'p':
                candidates.append((text_of(node), [node]))
            elif node.tag in (W+'footnote', W+'endnote', W+'sdt'):
                candidates.extend((text_of(p), [p]) for p in node.findall('.//'+W+'p'))
            for text, paragraphs in candidates:
                if text:
                    index += 1
                    result[f'b{index:04}'] = (text, paragraphs)
    return result


def minimal_diffs(before, after):
    """Word-level differences: surrounding context never becomes a revision."""
    left = re.findall(r'\w+|[^\w\s]|\s+', before)
    right = re.findall(r'\w+|[^\w\s]|\s+', after)
    offsets = [0]
    for token in left:
        offsets.append(offsets[-1] + len(token))
    differences = [(offsets[a], offsets[b], ''.join(right[c:d]))
                   for tag, a, b, c, d in SequenceMatcher(None, [v.translate(SPACE_FOLD) for v in left], [v.translate(SPACE_FOLD) for v in right], autojunk=False).get_opcodes()
                   if tag != 'equal']
    merged = []
    for start, stop, value in differences:
        if merged and not before[merged[-1][1]:start].strip():
            a, b, replacement = merged[-1]
            merged[-1] = (a, stop, replacement + before[b:start] + value)
        else:
            merged.append((start, stop, value))
    return merged


def broad_edit(before, after):
    diffs = minimal_diffs(before, after)
    changed = ''.join(before[a:b] + value for a, b, value in diffs)
    return len(changed) > 160 or len(re.findall(r'\w+', changed)) > 12


def complex_paragraph(paragraph):
    if any(paragraph.find('.//'+W+tag) is not None for tag in
           ['fldChar', 'instrText', 'drawing', 'pict', 'ins', 'del', 'br', 'cr']):
        return True
    # lastRenderedPageBreak is a cached pagination hint, not a real page break.
    return any(any(child.tag not in {W+'rPr', W+'t', W+'tab', W+'lastRenderedPageBreak'} for child in run)
               or (run.find(W+'tab') is not None and run.find(W+'t') is not None)
               for run in paragraph.iter(W+'r'))


def replace_runs(paragraph, before, after, revision_ids=None, *, checked=False, author='ТЗ Контроль'):
    if not checked and complex_paragraph(paragraph):
        return 'Сложное поле, разрыв или исправление Word требует ручного редактирования.'
    text = ''.join(n.text or '' for n in paragraph.iter(W+'t'))
    if text.count(before) != 1:
        return 'Фрагмент не имеет единственного точного совпадения в абзаце.'
    base = text.index(before)
    revision_ids = revision_ids if revision_ids is not None else count(1)
    date = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def piece(run, value, deleted=False):
        result = ET.Element(W+'r', dict(run.attrib))
        props = run.find(W+'rPr')
        if props is not None:
            result.append(deepcopy(props))
        node = ET.SubElement(result, W+('delText' if deleted else 't'))
        node.set(XML_SPACE, 'preserve')
        node.text = value
        return result

    def revision(tag, run, value):
        node = ET.Element(W+tag, {W+'id': str(next(revision_ids)), W+'author': author, W+'date': date})
        node.append(piece(run, value, tag == 'del'))
        return node

    for a, b, replacement in reversed(minimal_diffs(before, after)):
        start, stop = base+a, base+b
        offset, inserted, insertion_style = 0, False, None
        runs = list(paragraph.iter(W+'r'))
        # Deleted runs have delText, not t. Earlier edits are processed right-to-left.
        for run in runs:
            value = ''.join(n.text or '' for n in run.findall(W+'t'))
            left, right = offset, offset+len(value)
            offset = right
            hit = right > start and left < stop
            if start == stop:
                hit = bool(value) and left <= start <= right and not inserted
            if not hit:
                continue
            if insertion_style is None:
                insertion_style = run
            lo, hi = max(0, start-left), min(len(value), stop-left)
            parts = []
            if value[:lo]:
                parts.append(piece(run, value[:lo]))
            if value[lo:hi]:
                parts.append(revision('del', run, value[lo:hi]))
            if not inserted and right >= stop:
                if replacement:
                    parts.append(revision('ins', insertion_style, replacement))
                inserted = True
            if value[hi:]:
                parts.append(piece(run, value[hi:]))
            parent, index = run.getparent(), run.getparent().index(run)
            parent.remove(run)
            for i, part in enumerate(parts):
                parent.insert(index+i, part)
    return None


def enable_revision_display(settings):
    for tag in ['revisionView', 'trackRevisions', 'doNotTrackMoves', 'doNotTrackFormatting']:
        node = settings.find(W+tag)
        if node is None:
            node = ET.Element(W+tag)
            # OOXML places revision settings before protection, tabs and compatibility.
            predecessors = {'writeProtection','view','zoom','removePersonalInformation','removeDateAndTime','doNotDisplayPageBoundaries','displayBackgroundShape','printPostScriptOverText','printFractionalCharacterWidth','printFormsData','embedTrueTypeFonts','embedSystemFonts','saveSubsetFonts','saveFormsData','mirrorMargins','alignBordersAndEdges','bordersDoNotSurroundHeader','bordersDoNotSurroundFooter','gutterAtTop','hideSpellingErrors','hideGrammaticalErrors','activeWritingStyle','proofState','formsDesign','attachedTemplate','linkStyles','stylePaneFormatFilter','stylePaneSortMethod','documentType','mailMerge'}
            predecessors.update(['revisionView','trackRevisions','doNotTrackMoves','doNotTrackFormatting'][:['revisionView','trackRevisions','doNotTrackMoves','doNotTrackFormatting'].index(tag)])
            index = next((i for i, child in enumerate(settings) if ET.QName(child).localname not in predecessors), len(settings))
            settings.insert(index, node)
        if tag == 'revisionView':
            for name in ['markup', 'insDel', 'formatting']:
                node.set(W+name, 'true')
        else:
            node.set(W+'val', 'true')


def insert_row(anchor, cells, revision_ids, template=None):
    row = next((p for p in anchor.iterancestors() if p.tag == W+'tr'), None)
    if row is None or len(row.findall(W+'tc')) != len(cells):
        return None, 'Количество ячеек новой строки не совпадает с таблицей.'
    source = template if template is not None else row
    if any(source.find('.//'+W+tag) is not None for tag in ('vMerge', 'hMerge', 'gridSpan', 'tblHeader', 'ins', 'del', 'drawing', 'fldChar', 'sdt', 'bookmarkStart', 'bookmarkEnd')) or any(complex_paragraph(p) for p in source.findall('.//'+W+'p')) or source.find('.//'+W+'tbl') is not None:
        return None, 'Сложная или объединённая строка требует ручного редактирования Word.'
    new = deepcopy(source)
    date = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    def mark(parent):
        return ET.SubElement(parent, W+'ins', {W+'id': str(next(revision_ids)), W+'author': 'ТЗ Контроль', W+'date': date})
    props = new.find(W+'trPr')
    if props is None:
        props = ET.Element(W+'trPr'); new.insert(0, props)
    mark(props)
    for cell, value in zip(new.findall(W+'tc'), cells):
        template = cell.find(W+'p')
        p = ET.Element(W+'p')
        if template is not None and template.find(W+'pPr') is not None:
            p.append(deepcopy(template.find(W+'pPr')))
        for child in list(cell):
            if child.tag != W+'tcPr': cell.remove(child)
        run = ET.SubElement(mark(p), W+'r')
        style = template.find('.//'+W+'rPr') if template is not None else None
        if style is not None: run.append(deepcopy(style))
        t = ET.SubElement(run, W+'t', {XML_SPACE: 'preserve'}); t.text = value
        cell.append(p)
    row.addnext(new)
    return new, None


def apply_edits(data, original, review, redactor, *, approved_changes=()):
    from .validation import edit_review_reason
    from collections import Counter
    approved_changes = set(approved_changes)
    changes = []
    with ZipFile(io.BytesIO(data)) as archive:
        parts = ['word/document.xml'] + sorted(n for n in archive.namelist() if re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml', n))
        roots = {p: ET.fromstring(archive.read(p), parser=ET.XMLParser(resolve_entities=False, no_network=True)) for p in parts}
        dirty_roots = set()
        mapping = mapped_blocks(roots)
        row_templates = {}
        for block_id, (_, paragraphs) in mapping.items():
            if paragraphs:
                row = next((n for n in paragraphs[0].iterancestors() if n.tag == W+'tr'), None)
                if row is not None: row_templates[block_id] = deepcopy(row)
        revision_ids = count(1 + max((int(v) for root in roots.values() for n in root.iter() for k, v in n.attrib.items() if k == W+'id' and v.isdigit()), default=0))
        if list(mapping) != [b.id for b in original.blocks] or any(mapping[b.id][0] != b.text for b in original.blocks):
            raise ValueError('Не удалось однозначно связать правки с исходным Word.')
        planned, seen = [], set()
        for number, finding in enumerate(review.findings, 1):
            for edit_index, edit in enumerate(finding.edits, 1):
                public_before, public_after = edit.before, edit.after
                # Models may quote a whole row even for a single-cell replacement.
                # Narrow only when all other cells are identical; never distribute changes.
                old_cells, new_cells = public_before.split(' | '), public_after.split(' | ')
                if len(old_cells) > 1 and len(old_cells) == len(new_cells):
                    changed = [i for i, pair in enumerate(zip(old_cells, new_cells)) if pair[0] != pair[1]]
                    if len(changed) == 1:
                        public_before, public_after = old_cells[changed[0]], new_cells[changed[0]]
                before, after = redactor.restore(public_before), redactor.restore(public_after)
                change = {'id': f'f{number}-e{edit_index}', 'operation': 'replace', 'finding': number, 'block_id': edit.block_id, 'before': before, 'after': after, 'status': 'skipped', 'reason': '', 'selectable': False}
                changes.append(change)
                if Counter(TOKEN.findall(public_before)) != Counter(TOKEN.findall(public_after)):
                    change['reason'] = 'Нельзя изменять скрытые персональные данные.'
                    continue
                if TOKEN.search(before+after):
                    change['reason'] = 'Неизвестный токен скрытых данных.'
                    continue
                # New person/contact data invented by a model must not enter the document.
                try:
                    redactor.assert_safe(public_after)
                except ValueError:
                    change['reason'] = 'Новая формулировка требует локальной проверки персональных данных.'
                    continue
                entry = mapping.get(edit.block_id)
                paragraphs = entry[1] if entry else []
                matches = [p for p in paragraphs if before.translate(SPACE_FOLD) in text_of(p).translate(SPACE_FOLD)]
                if len(matches) != 1 or text_of(matches[0]).translate(SPACE_FOLD).count(before.translate(SPACE_FOLD)) != 1:
                    change['reason'] = 'Фрагмент пересекает ячейки или найден неоднозначно.'
                    continue
                p = matches[0]
                position = text_of(p).translate(SPACE_FOLD).index(before.translate(SPACE_FOLD))
                before = text_of(p)[position:position+len(before)]
                change['before'] = before
                key = (edit.block_id, before)
                if key in seen:
                    change['reason'] = 'Повторная или конфликтующая правка.'
                    continue
                seen.add(key)
                policy = edit_review_reason(finding, edit)
                if policy and change['id'] not in approved_changes:
                    change['reason'], change['selectable'] = policy, not complex_paragraph(p)
                    continue
                change['approved'] = change['id'] in approved_changes
                planned.append((p, before, after, change))
        # Resolve all overlaps on ORIGINAL paragraphs before applying any replacement.
        groups = {}
        for p, before, after, change in planned:
            text = ''.join(n.text or '' for n in p.iter(W+'t'))
            start = text.find(before)
            groups.setdefault(p, []).append((start, start+len(before), before, after, change))
        for p, edits in groups.items():
            if complex_paragraph(p):
                for edit in edits:
                    edit[4]['reason'] = 'Сложное поле, разрыв или исправление Word требует ручного редактирования.'
                continue
            for i, edit in enumerate(edits):
                if any(i != j and edit[0] < other[1] and other[0] < edit[1] for j, other in enumerate(edits)):
                    edit[4]['reason'] = 'Пересекающиеся правки требуют согласования.'
            for start, stop, before, after, change in sorted(edits, key=lambda x: x[0], reverse=True):
                if change['reason']:
                    continue
                error = replace_runs(p, before, after, revision_ids, checked=True)
                if error:
                    change['reason'] = error
                else:
                    dirty_roots.add(p.getroottree().getroot())
                    change['status'] = 'applied'
                    change['reason'] = 'Применена минимальная замена; удаление и вставка отмечены исправлениями Word.'
        row_keys, row_tails = set(), {}
        for number, finding in enumerate(review.findings, 1):
            for row_index, insertion in enumerate(finding.row_insertions, 1):
                change = {'id': f'f{number}-r{row_index}', 'operation': 'insert_row', 'finding': number, 'block_id': insertion.after_block_id, 'before': '(новая строка таблицы)', 'after': ' | '.join(insertion.cells), 'status': 'skipped', 'reason': '', 'selectable': False}
                changes.append(change)
                try:
                    redactor.assert_safe(' '.join(insertion.cells))
                except ValueError:
                    change['reason'] = 'Новая строка требует локальной проверки персональных данных.'; continue
                row_key = (insertion.after_block_id, tuple(insertion.cells))
                if TOKEN.search(change['after']) or row_key in row_keys:
                    change['reason'] = 'Скрытые данные или повторная вставка одинаковой строки.'; continue
                entry = mapping.get(insertion.after_block_id)
                if not entry or not entry[1]:
                    change['reason'] = 'Строка таблицы не найдена.'; continue
                # Validate on a detached copy before offering this action in the UI.
                anchor_row = next((n for n in entry[1][0].iterancestors() if n.tag == W+'tr'), None)
                if anchor_row is None:
                    change['reason'] = 'Блок не является строкой таблицы.'; continue
                temporary = ET.Element(W+'tbl'); temporary.append(deepcopy(row_templates[insertion.after_block_id]))
                _, error = insert_row(temporary.find('.//'+W+'p'), insertion.cells, count(1))
                if error:
                    change['reason'] = error; continue
                row_keys.add(row_key)
                if change['id'] not in approved_changes:
                    change['reason'], change['selectable'] = 'Добавление позиции спецификации требует решения сотрудника.', True
                    continue
                tail = row_tails.get(insertion.after_block_id)
                anchor = tail.find('.//'+W+'p') if tail is not None else entry[1][0]
                new, error = insert_row(anchor, insertion.cells, revision_ids, template=row_templates[insertion.after_block_id])
                if error:
                    change['reason'] = error; continue
                dirty_roots.add(new.getroottree().getroot())
                row_tails[insertion.after_block_id] = new
                change.update(status='applied', approved=True, reason='Добавлена согласованная строка с исправлениями Word.')
        if not any(c['status'] == 'applied' for c in changes):
            return data, changes
        if 'word/settings.xml' in archive.namelist():
            roots['word/settings.xml'] = ET.fromstring(archive.read('word/settings.xml'))
            enable_revision_display(roots['word/settings.xml'])
            dirty_roots.add(roots['word/settings.xml'])
        out = io.BytesIO()
        with ZipFile(out, 'w', compression=ZIP_DEFLATED) as target:
            for info in archive.infolist():
                root = roots.get(info.filename)
                content = ET.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True) if root in dirty_roots else archive.read(info.filename)
                target.writestr(info, content)
    result = out.getvalue()
    parse_docx(result)  # Output must still be a readable, bounded document.
    return result, changes
