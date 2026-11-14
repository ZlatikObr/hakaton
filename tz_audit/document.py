"""Read-only OOXML ingestion. Never extract archive paths or follow relationships."""
import hashlib
import io
import re
from zipfile import BadZipFile, ZipFile
from defusedxml import ElementTree as ET
from .models import Block, Document

MAX_FILE = 12 * 1024 * 1024
MAX_XML = 32 * 1024 * 1024
MAX_TEXT = 220_000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

class DocumentError(ValueError):
    pass

def text_of(node):
    # Deleted revisions and field instructions are not part of the visible text.
    out = []
    def walk(n):
        if n.tag == W + 'del':
            return
        if n.tag == W + 't':
            out.append(n.text or '')
        elif n.tag == W + 'tab':
            out.append('\t')
        elif n.tag in (W + 'br', W + 'cr'):
            out.append('\n')
        else:
            for child in n:
                walk(child)
    walk(node)
    return ''.join(out).strip()

def parse_docx(data: bytes) -> Document:
    if not data or len(data) > MAX_FILE:
        raise DocumentError('Пустой файл или превышен лимит 12 МБ.')
    blocks, warnings = [], []
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(x.file_size for x in infos) > MAX_XML:
                raise DocumentError('Превышен лимит распакованного DOCX.')
            if len({x.filename for x in infos}) != len(infos):
                raise DocumentError('Повторяющиеся части DOCX не поддерживаются.')
            names = {x.filename for x in infos}
            if 'word/document.xml' not in names:
                raise DocumentError('Файл не является DOCX.')
            if any(x.endswith('vbaProject.bin') for x in names):
                raise DocumentError('Документы с макросами не поддерживаются.')
            parts = ['word/document.xml'] + sorted(n for n in names if re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml', n))
            for part in parts:
                root = ET.fromstring(archive.read(part))
                body = root.find(W + 'body') if part == 'word/document.xml' else root
                if body is None:
                    raise DocumentError('Отсутствует тело документа.')
                if root.find('.//' + W + 'drawing') is not None or root.find('.//' + W + 'pict') is not None:
                    warnings.append('Изображения не анализируются; проверьте их отдельно, OCR не включён.')
                if root.find('.//' + W + 'del') is not None:
                    warnings.append('Удалённые исправления не анализируются; использована текущая видимая редакция.')
                if root.find('.//' + W + 'altChunk') is not None:
                    raise DocumentError('Вложенные HTML/altChunk не поддерживаются; сохраните обычный DOCX.')
                prefix = part.removeprefix('word/').removesuffix('.xml')
                section = ''
                table_n, para_n = 0, 0
                def add(txt, locator, kind):
                    if txt:
                        blocks.append(Block(id=f'b{len(blocks)+1:04}', text=txt, location=locator, kind=kind))
                for node in body:
                    if node.tag == W + 'tbl':
                        table_n += 1
                        for row_n, row in enumerate(node.findall(W + 'tr'), 1):
                            cells = ['\n'.join(text_of(p) for p in c.findall('.//' + W + 'p') if text_of(p)) for c in row.findall(W + 'tc')]
                            add(' | '.join(cells), f'{prefix}: таблица {table_n}, строка {row_n}', 'table_row')
                    elif node.tag == W + 'p':
                        para_n += 1
                        txt = text_of(node)
                        if len(txt) < 180 and re.match(r'^\d{1,2}[. ]', txt):
                            section = txt
                        kind = 'header' if prefix.startswith('header') else 'footer' if prefix.startswith('footer') else 'paragraph'
                        add(txt, f'{prefix}: абзац {para_n}' + (f'; раздел {section}' if section else ''), kind)
                    elif node.tag in (W+'footnote', W+'endnote', W+'sdt'):
                        for n, p in enumerate(node.findall('.//' + W + 'p')):
                            add(text_of(p), f'{prefix}: дополнительный абзац {len(blocks)+1}', 'note')
            if not blocks:
                raise DocumentError('Нет доступного текста. Сканированные документы требуют локального OCR.')
            if sum(len(b.text) for b in blocks) > MAX_TEXT:
                raise DocumentError('Превышен лимит 220 000 символов. Разделите документ локально.')
    except DocumentError:
        raise
    except (BadZipFile, KeyError, Exception) as exc:
        # Parser messages may contain private document content: never surface them.
        raise DocumentError('Повреждённый или неподдерживаемый DOCX.') from None
    return Document(sha256=hashlib.sha256(data).hexdigest(), blocks=blocks, warnings=list(dict.fromkeys(warnings)))
