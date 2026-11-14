import io
import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from tz_audit.api import create_app
from tz_audit.document import parse_document, DocumentError
from tz_audit.reference import norms, norm_line
from tz_audit.models import Block, Document, Review
from tz_audit.offline import baseline
from tz_audit.agent import Agent
from tz_audit.privacy import Redactor, release_digest
from tests.test_agent import Fake, reply, audited

PDF = Path(__file__).parent / 'fixtures/public/tz_sample.pdf'
TOKEN = {'Authorization': 'Bearer ' + 'x' * 32}


def test_pdf_text_layer_is_parsed_with_pages():
    doc, fmt = parse_document(PDF.read_bytes())
    assert fmt == 'pdf' and doc.blocks[1].text == '1. Предмет закупки: поставка грузового лифта.'
    assert doc.blocks[1].location == 'страница 1, абзац 2' and 'PDF' in doc.warnings[0]


def test_scanned_and_broken_pdf_give_clear_errors():
    out = io.BytesIO(); w = PdfWriter(); w.add_blank_page(width=100, height=100); w.write(out)
    with pytest.raises(DocumentError, match='текстового слоя'): parse_document(out.getvalue())
    with pytest.raises(DocumentError, match='PDF'): parse_document(b'%PDF-1.4 broken')


def test_pdf_end_to_end_report_without_word_edits(tmp_path, monkeypatch):
    monkeypatch.setenv('TZ_API_TOKEN', 'x' * 32)
    client = TestClient(create_app(tmp_path))
    r = client.post('/jobs', headers=TOKEN, files={'file': ('ТЗ.pdf', PDF.read_bytes())})
    assert r.status_code == 200, r.text
    job = r.json()['job_id']
    assert client.get('/jobs/' + job, headers=TOKEN).json()['source_format'] == 'pdf'
    assert client.post(f'/jobs/{job}/analyze', headers=TOKEN, json={'mode': 'offline'}).status_code == 200
    report = client.get(f'/jobs/{job}/report.json', headers=TOKEN).json()
    assert report['review']['findings'] and all(c['status'] == 'skipped' for c in report['changes'])
    assert client.get(f'/jobs/{job}/report.docx', headers=TOKEN).status_code == 200
    assert client.get(f'/jobs/{job}/corrected.docx', headers=TOKEN).status_code == 404
    assert client.post('/jobs', headers=TOKEN, files={'file': ('a.txt', b'x')}).status_code == 400


def test_norms_are_verbatim_and_complete():
    n = norms()
    assert set(n) == {'44fz_33', '223fz_3_6_1', '223fz_3_1_4', 'gk_724', 'gk_755'}
    assert norm_line('44fz_33', 'а) ') == 'а) сопровождение такого указания словами "или эквивалент";'
    assert all(x['url'].startswith('https://www.consultant.ru/') and '(в ред.' not in x['text'] for x in n.values())


def test_offline_flags_registry_phrase_and_cites_law():
    doc = Document(sha256='c' * 64, blocks=[
        Block(id='b0001', text='Поставка измерительного оборудования.', location='b0001'),
        Block(id='b0002', text='Наличие оборудования в российском реестре не является техническим требованием настоящего ТЗ.', location='b0002'),
        Block(id='b0003', text='Органайзер Midstream или аналог', location='b0003')])
    review, sources = baseline(doc)
    reg = next(f for f in review.findings if 'реестре' in f.title)
    assert reg.severity == 'high' and {c.source_id for c in reg.citations} <= {s.id for s in sources}
    analog = next(f for f in review.findings if 'аналог' in f.title)
    src = next(s for s in sources if s.id == analog.citations[0].source_id)
    assert analog.citations[0].quote in src.text and 'consultant.ru' in src.url


def test_agent_read_norm_tool_gives_citable_text():
    doc = Document(sha256='d' * 64, blocks=[Block(id='b0001', text='Органайзер Midstream или аналог', location='b0001')])
    review = Review(summary='Формулировка ограничивает конкуренцию.', findings=[{
        'category': 'competition', 'severity': 'high', 'status': 'needs_review', 'title': '«Или аналог» вместо «или эквивалент»',
        'anchors': [{'block_id': 'b0001', 'quote': 'Midstream или аналог'}], 'basis': 'external',
        'explanation': 'Закон допускает указание товарного знака только со словами «или эквивалент».',
        'recommendation': 'Заменить на «или эквивалент».',
        'citations': [{'source_id': 'z1', 'quote': 'а) сопровождение такого указания словами "или эквивалент";'}]}])
    fake = Fake([reply('read_norm', {'norm_id': '44fz_33'}), reply('submit_review', review.model_dump()), audited(review)])
    result, sources, _ = Agent(fake).run(doc, Redactor(False), release_digest(doc))
    assert json.loads(fake.calls[1][-1]['content'])['source_id'] == 'z1'
    assert result.findings[0].citations[0].source_id == 'z1' and any(s.id == 'z1' for s in sources)
