import io
from zipfile import ZipFile
import pytest
from tz_audit.document import parse_docx, DocumentError, MAX_FILE

def test_order_and_tables(docx_bytes):
    doc = parse_docx(docx_bytes(['Раздел 1', 'Требование'], [['Номер', 'Мощность'], ['1', '50 Вт']]))
    assert [b.id for b in doc.blocks] == ['b0001', 'b0002', 'b0003', 'b0004']
    assert doc.blocks[-1].text == '1 | 50 Вт'
    assert 'таблица 1, строка 2' in doc.blocks[-1].location

@pytest.mark.parametrize('data', [b'', b'bad zip', b'x'*(MAX_FILE+1)])
def test_bad_file(data):
    with pytest.raises(DocumentError): parse_docx(data)

def test_xml_entity_attack():
    out = io.BytesIO()
    with ZipFile(out, 'w') as archive:
        archive.writestr('word/document.xml', '<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><foo>&x;</foo>')
    with pytest.raises(DocumentError): parse_docx(out.getvalue())

def test_empty(docx_bytes):
    with pytest.raises(DocumentError): parse_docx(docx_bytes([]))

def test_header_and_split_runs(docx_bytes):
    from docx import Document
    doc = Document(io.BytesIO(docx_bytes(['ТЗ'])))
    p = doc.add_paragraph(); p.add_run('test'); p.add_run('@example.com')
    doc.sections[0].header.paragraphs[0].text = 'Контактное лицо: Петров Иван'
    out = io.BytesIO(); doc.save(out)
    parsed = parse_docx(out.getvalue())
    assert any('test@example.com' in b.text for b in parsed.blocks)
    assert any(b.kind == 'header' for b in parsed.blocks)
