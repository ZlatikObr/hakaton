import io
import pytest
from docx import Document
from tz_audit.models import Block, Document as Parsed, Review, Finding, Anchor

@pytest.fixture
def docx_bytes():
    def make(paragraphs, rows=None):
        doc = Document()
        for text in paragraphs:
            doc.add_paragraph(text)
        if rows:
            table = doc.add_table(rows=0, cols=len(rows[0]))
            for row in rows:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = value
        out = io.BytesIO(); doc.save(out)
        return out.getvalue()
    return make

@pytest.fixture
def public_doc():
    return Parsed(sha256='a'*64, blocks=[Block(id='b0001', text='Грузоподъемность 300 кг', location='b0001'),
        Block(id='b0002', text='Грузоподъемность 500 кг', location='b0002')])

@pytest.fixture
def review(public_doc):
    return Review(summary='В документе есть противоречие грузоподъемности.', findings=[Finding(
        category='contradiction', severity='high', status='document_issue', title='Различается грузоподъемность',
        anchors=[Anchor(block_id=b.id, quote=b.text) for b in public_doc.blocks],
        explanation='Для одного изделия указаны разные значения грузоподъемности.',
        recommendation='Уточнить у технического заказчика требуемую грузоподъемность и согласовать значения.')])
