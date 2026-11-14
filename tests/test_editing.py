import io
from zipfile import ZipFile
from docx import Document
from tz_audit.document import parse_docx
from tz_audit.editing import apply_edits
from tz_audit.models import Review, Finding, Anchor, Edit
from tz_audit.privacy import Redactor

def review(block, before, after, status='document_issue'):
 return Review(summary='Точечное исправление проверенной внутренней ошибки.',findings=[Finding(category='characteristics',severity='medium',status=status,title='Уточнение технической характеристики',anchors=[Anchor(block_id=block.id,quote=block.text)],explanation='Значение приведено в соответствие с подтверждёнными данными.',recommendation='Исправить значение в указанной строке документа.',edits=[Edit(block_id=block.id,before=before,after=after)])])

def test_preserves_other_zip_parts_and_runs(docx_bytes):
 doc=Document(io.BytesIO(docx_bytes(['Техническое задание'])))
 p=doc.add_paragraph();p.add_run('Мощность ').bold=True;p.add_run('30');p.add_run('0 Вт').italic=True
 out=io.BytesIO();doc.save(out);raw=out.getvalue();parsed=parse_docx(raw)
 result,changes=apply_edits(raw,parsed,review(parsed.blocks[-1],'300','500'),Redactor(False))
 assert changes[0]['status']=='applied'
 edited=Document(io.BytesIO(result));assert parse_docx(result).blocks[-1].text=='Мощность 500 Вт'
 assert edited.paragraphs[-1].runs[0].bold and edited.paragraphs[-1].runs[-1].italic
 with ZipFile(io.BytesIO(raw)) as a,ZipFile(io.BytesIO(result)) as b:
  for name in a.namelist():
   if name not in {'word/document.xml', 'word/settings.xml'}:assert a.read(name)==b.read(name)

def test_table_cell_and_ambiguous_cases(docx_bytes):
 raw=docx_bytes(['ТЗ'],[['1','Мощность','300 Вт']]);parsed=parse_docx(raw)
 result,changes=apply_edits(raw,parsed,review(parsed.blocks[-1],'300','500'),Redactor(False))
 assert changes[0]['status']=='applied'
 assert parse_docx(result).blocks[-1].text=='1 | Мощность | 500 Вт'


def test_unedited_footer_keeps_original_xml_bytes():
 doc=Document();doc.add_paragraph('Мощность 300 Вт')
 doc.sections[0].footer.paragraphs[0].text='Служебный колонтитул'
 saved=io.BytesIO();doc.save(saved);raw=io.BytesIO()
 with ZipFile(saved) as source,ZipFile(raw,'w') as target:
  for info in source.infolist():
   content=source.read(info.filename)
   if info.filename=='word/footer1.xml':content+=b'\n'
   target.writestr(info,content)
 data=raw.getvalue();parsed=parse_docx(data)
 result,changes=apply_edits(data,parsed,review(parsed.blocks[0],'300','500'),Redactor(False))
 assert changes[0]['status']=='applied'
 with ZipFile(io.BytesIO(data)) as original,ZipFile(io.BytesIO(result)) as edited:
  for name in original.namelist():
   if name not in {'word/document.xml','word/settings.xml'}:assert original.read(name)==edited.read(name)

def test_manual_case_never_applied(docx_bytes):
 raw=docx_bytes(['Мощность 300 Вт']);parsed=parse_docx(raw)
 result,changes=apply_edits(raw,parsed,review(parsed.blocks[0],'300','500','needs_review'),Redactor(False))
 assert result==raw and changes[0]['status']=='skipped'

def test_overlapping_edits_skipped(docx_bytes):
 raw=docx_bytes(['Мощность 300 Вт']);parsed=parse_docx(raw);r=review(parsed.blocks[0],'300','500')
 r.findings[0].edits.append(Edit(block_id=parsed.blocks[0].id,before='300 Вт',after='600 Вт'))
 result,changes=apply_edits(raw,parsed,r,Redactor(False))
 assert result==raw and all(c['status']=='skipped' for c in changes)


def test_single_changed_cell_in_row_quote_is_narrowed(docx_bytes):
 raw=docx_bytes(['ТЗ'],[['Мощность','300 Вт']]);parsed=parse_docx(raw)
 result,changes=apply_edits(raw,parsed,review(parsed.blocks[-1],'Мощность | 300 Вт','Мощность | 500 Вт'),Redactor(False))
 assert changes[0]['status']=='applied'
 assert changes[0]['before']=='300 Вт'
 assert parse_docx(result).blocks[-1].text=='Мощность | 500 Вт'


def test_changes_across_two_cells_remain_manual(docx_bytes):
 raw=docx_bytes(['ТЗ'],[['Мощность','300 Вт']]);parsed=parse_docx(raw)
 result,changes=apply_edits(raw,parsed,review(parsed.blocks[-1],'Мощность | 300 Вт','Напряжение | 500 В'),Redactor(False))
 assert result==raw and changes[0]['status']=='skipped'


def test_normative_changes_cannot_be_self_certified(docx_bytes):
 from tz_audit.validation import validate_review
 raw=docx_bytes(['Применить ГОСТ 53782-2010']);parsed=parse_docx(raw)
 r=review(parsed.blocks[0],'ГОСТ 53782-2010','ГОСТ Р 53782-2010')
 validate_review(r,parsed,[])
 result,changes=apply_edits(raw,parsed,r,Redactor(False))
 assert r.findings[0].edits[0]._review_reason and result==raw


def test_revisions_accept_reject_and_minimal_context(docx_bytes):
 from lxml import etree as ET
 from tz_audit.document import W, text_of
 raw=docx_bytes(['Мощность 300 Вт; количество 4 шт.'])
 original=parse_docx(raw);r=review(original.blocks[0],original.blocks[0].text,'Мощность 500 Вт; количество 4 шт.')
 result,changes=apply_edits(raw,original,r,Redactor(False))
 with ZipFile(io.BytesIO(result)) as z:
  root=ET.fromstring(z.read('word/document.xml'))
  assert [n.text for n in root.iter(W+'delText')]==['300']
  assert [text_of(n) for n in root.iter(W+'ins')]==['500']
  ids=[n.get(W+'id') for n in root.iter() if n.tag in {W+'del',W+'ins'}]
  assert len(ids)==len(set(ids))
  assert ET.fromstring(z.read('word/settings.xml')).find(W+'revisionView').get(W+'insDel')=='true'
  # Reject all revisions must reconstruct the original text exactly.
  for n in list(root.iter(W+'ins')):n.getparent().remove(n)
  for n in list(root.iter(W+'del')):
   n.tag=W+'sdt'
   for t in n.iter(W+'delText'):t.tag=W+'t'
  assert text_of(root)==original.blocks[0].text


def test_multiple_edits_and_pure_insert_delete(docx_bytes):
 raw=docx_bytes(['Мощность 300 Вт; количество 4 шт.']);original=parse_docx(raw)
 r=review(original.blocks[0],'Мощность 300','Мощность 500')
 r.findings[0].edits.append(Edit(block_id='b0001',before='количество 4 шт.',after='количество не менее 6 шт.'))
 result,changes=apply_edits(raw,original,r,Redactor(False))
 assert all(c['status']=='applied' for c in changes)
 assert parse_docx(result).blocks[0].text=='Мощность 500 Вт; количество не менее 6 шт.'
 for before,after in [('Товар новый.','Товар новый, заводской.'),('Товар новый и заводской.','Товар новый.')]:
  raw=docx_bytes([before]);original=parse_docx(raw)
  result,_=apply_edits(raw,original,review(original.blocks[0],before,after),Redactor(False))
  assert parse_docx(result).blocks[0].text==after


def test_broad_rewrite_is_proposal(docx_bytes):
 from tz_audit.validation import validate_review
 before='Гарантийный срок на выполненные работы устанавливается не менее 24 месяцев с момента подписания товарной накладной и отсутствия претензий к качеству поставленного товара.'
 after='Гарантийный срок на выполненные работы устанавливается не менее 24 месяцев с момента подписания акта о приемке выполненных работ (форма КС-2) и акта ввода лифта в эксплуатацию.'
 raw=docx_bytes([before]);original=parse_docx(raw);r=review(original.blocks[0],before,after)
 validate_review(r,original,[])
 assert r.findings[0].edits[0]._review_reason
 result,changes=apply_edits(raw,original,r,Redactor(False))
 assert result==raw and changes[0]['status']=='skipped'


def test_nonbreaking_spaces_anchor_without_incidental_whitespace_revisions(docx_bytes):
 from lxml import etree as ET
 from tz_audit.document import W
 from tz_audit.privacy import normalize
 raw=docx_bytes(['Гарантия 6\u00a0месяцев.']);original=parse_docx(raw)
 r=review(original.blocks[0],'Гарантия 6 месяцев.','Гарантия 12 месяцев.')
 result,changes=apply_edits(raw,original,r,Redactor(False))
 assert changes[0]['status']=='applied'
 assert parse_docx(result).blocks[0].text=='Гарантия 12\u00a0месяцев.'
 with ZipFile(io.BytesIO(result)) as z:
  root=ET.fromstring(z.read('word/document.xml'))
  assert [t.text for t in root.iter(W+'delText')]==['6']
 assert normalize(original.blocks[0].text)=='Гарантия 6 месяцев.'


def test_unit_renaming_stays_manual(docx_bytes):
 from tz_audit.validation import validate_review
 raw=docx_bytes(['Разрешение 600 т/д']);original=parse_docx(raw)
 r=review(original.blocks[0],'600 т/д','600 dpi');validate_review(r,original,[])
 assert r.findings[0].edits[0]._review_reason
 assert apply_edits(raw,original,r,Redactor(False))[0]==raw


def test_separate_indentation_tab_preserved(docx_bytes):
 from lxml import etree as ET
 from tz_audit.document import W
 doc=Document();p=doc.add_paragraph();p.add_run('\t');p.add_run('Оборудование должно осуществлять проверку.')
 buf=io.BytesIO();doc.save(buf);raw=buf.getvalue();original=parse_docx(raw)
 result,changes=apply_edits(raw,original,review(original.blocks[0],'осуществлять','проходить'),Redactor(False))
 assert changes[0]['status']=='applied'
 with ZipFile(io.BytesIO(result)) as z:assert len(ET.fromstring(z.read('word/document.xml')).findall('.//'+W+'tab'))==1
 assert parse_docx(result).blocks[0].text=='Оборудование должно проходить проверку.'


def test_unit_spacing_and_independent_edit_decisions(docx_bytes):
 from tz_audit.validation import validate_review
 raw=docx_bytes(['Лифт грузоодъемностью500кг; кабины лифты; разрешение 600 т/д'])
 original=parse_docx(raw);r=review(original.blocks[0],'грузоодъемностью500кг','грузоподъемностью 500 кг')
 r.findings[0].edits += [Edit(block_id='b0001',before='кабины лифты',after='кабины лифта'),Edit(block_id='b0001',before='600 т/д',after='600 dpi')]
 validate_review(r,original,[])
 result,changes=apply_edits(raw,original,r,Redactor(False))
 assert [c['status'] for c in changes]==['applied','applied','skipped']
 assert changes[2]['selectable']
 assert parse_docx(result).blocks[0].text=='Лифт грузоподъемностью 500 кг; кабины лифта; разрешение 600 т/д'


def test_reference_spelling_requires_actual_evidence(docx_bytes):
 from tz_audit.models import Source, Citation
 from tz_audit.validation import validate_review
 raw=docx_bytes(['Применить ГОСТ 53782-2010']);original=parse_docx(raw)
 r=review(original.blocks[0],'ГОСТ 53782-2010','ГОСТ Р 53782-2010',status='needs_review')
 r.findings[0].category='standards';r.findings[0].basis='external'
 quote='ГОСТ Р 53782-2010 Лифты. Правила и методы оценки соответствия'
 r.findings[0].citations=[Citation(source_id='s1',quote=quote)]
 source=Source(id='s1',url='https://www.gost.ru/example',title='Стандарт',text=quote,retrieved_at='2026-11-13',sha256='x')
 validate_review(r,original,[source]);result,changes=apply_edits(raw,original,r,Redactor(False))
 assert changes[0]['status']=='applied'
 assert parse_docx(result).blocks[0].text=='Применить ГОСТ Р 53782-2010'


def test_insert_selected_row_tracks_and_can_be_rejected(docx_bytes):
 from tz_audit.models import RowInsertion
 from tz_audit.validation import validate_review
 from tz_audit.document import W, text_of
 from lxml import etree as ET
 raw=docx_bytes(['Спецификация'],[['1','Работы','1','шт.']]);original=parse_docx(raw)
 r=review(original.blocks[-1],'Работы','Монтаж')
 r.findings[0].row_insertions=[RowInsertion(after_block_id='b0002',cells=['2','Лифт грузовой','1','шт.'])]
 validate_review(r,original,[])
 provisional,changes=apply_edits(raw,original,r,Redactor(False))
 assert changes[1]['selectable'] and changes[1]['status']=='skipped'
 result,changes=apply_edits(raw,original,r,Redactor(False),approved_changes=['f1-r1'])
 assert all(c['status']=='applied' for c in changes)
 assert parse_docx(result).blocks[-1].text=='2 | Лифт грузовой | 1 | шт.'
 with ZipFile(io.BytesIO(result)) as archive:
  root=ET.fromstring(archive.read('word/document.xml'))
  rows=root.findall('.//'+W+'tr')
  assert len(rows)==2 and rows[-1].find(W+'trPr/'+W+'ins') is not None
  # Word rejects an inserted row as a unit; reject the remaining text revisions too.
  for row in rows:
   if row.find(W+'trPr/'+W+'ins') is not None:row.getparent().remove(row)
  for ins in list(root.iter(W+'ins')):ins.getparent().remove(ins)
  for deletion in root.iter(W+'del'):
   deletion.tag=W+'sdt'
   for node in deletion.iter(W+'delText'):node.tag=W+'t'
  assert text_of(root)=='Спецификация1Работы1шт.'


def test_row_shape_and_private_data_cannot_be_approved(docx_bytes):
 from tz_audit.models import RowInsertion
 raw=docx_bytes(['Спецификация'],[['1','Монтаж']]);original=parse_docx(raw)
 r=review(original.blocks[-1],'Монтаж','Работы');r.findings[0].edits=[]
 for cells in [['2','Лифт','лишняя ячейка'],['2','test@example.com']]:
  r.findings[0].row_insertions=[RowInsertion(after_block_id='b0002',cells=cells)]
  result,changes=apply_edits(raw,original,r,Redactor(False),approved_changes=['f1-r1'])
  assert result==raw and not changes[0]['selectable']


def test_cached_page_marker_does_not_block_minimal_edit():
 from docx.oxml import OxmlElement
 doc=Document();p=doc.add_paragraph('ГОСТ 53783-2010');p.runs[0]._r.insert(0,OxmlElement('w:lastRenderedPageBreak'))
 buf=io.BytesIO();doc.save(buf);raw=buf.getvalue();original=parse_docx(raw)
 r=review(original.blocks[0],'ГОСТ 53783-2010','ГОСТ Р 53783-2010',status='needs_review')
 result,changes=apply_edits(raw,original,r,Redactor(False))
 assert changes[0]['selectable']
 result,changes=apply_edits(raw,original,r,Redactor(False),approved_changes=['f1-e1'])
 assert changes[0]['status']=='applied' and parse_docx(result).blocks[0].text=='ГОСТ Р 53783-2010'


def test_multiple_new_rows_keep_requested_order(docx_bytes):
 from tz_audit.models import RowInsertion
 raw=docx_bytes(['Спецификация'],[['1','Монтаж']]);original=parse_docx(raw)
 r=review(original.blocks[-1],'Монтаж','Работы');r.findings[0].edits=[]
 r.findings[0].row_insertions=[RowInsertion(after_block_id='b0002',cells=['2','Оборудование']),RowInsertion(after_block_id='b0002',cells=['3','Запасные части'])]
 result,changes=apply_edits(raw,original,r,Redactor(False),approved_changes=['f1-r1','f1-r2'])
 assert all(c['status']=='applied' for c in changes)
 assert [b.text for b in parse_docx(result).blocks[-2:]]==['2 | Оборудование','3 | Запасные части']
