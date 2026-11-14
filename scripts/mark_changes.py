"""Mark text differences between two matching DOCX versions; no model or network.
This comparison preserves the supplied edits, including edits requiring manual review.
"""
import argparse
import io
import re
from itertools import count
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as ET
from tz_audit.document import parse_docx, text_of, W
from tz_audit.editing import replace_runs, enable_revision_display


def compare(source, edited):
    before, after = parse_docx(source), parse_docx(edited)
    with ZipFile(io.BytesIO(source)) as old, ZipFile(io.BytesIO(edited)) as new:
        parts=['word/document.xml']+sorted(n for n in old.namelist() if re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml',n))
        roots={p:ET.fromstring(old.read(p),parser=ET.XMLParser(resolve_entities=False,no_network=True)) for p in parts}
        ids=count(1+max((int(v) for root in roots.values() for n in root.iter() for k,v in n.attrib.items() if k==W+'id' and v.isdigit()),default=0))
        changed=0
        for part,root in roots.items():
            modified=ET.fromstring(new.read(part),parser=ET.XMLParser(resolve_entities=False,no_network=True))
            left,right=list(root.iter(W+'p')),list(modified.iter(W+'p'))
            if len(left)!=len(right):raise ValueError('Структура абзацев различается; требуется отдельное сравнение.')
            for p,q in zip(left,right):
                a,b=text_of(p),text_of(q)
                if a==b:continue
                if not a:raise ValueError('Добавленный пустой абзац требует отдельного сравнения.')
                error=replace_runs(p,a,b,ids,author='Сравнение версий')
                if error:raise ValueError(error)
                changed+=1
            # Prevent isolated numbered headings in the comparison view, where markup changes pagination.
            for p in left:
                label=text_of(p)
                runs=[r for r in p.iter(W+'r') if r.find(W+'t') is not None]
                if len(label)<120 and re.match(r'^\d+[.]\s',label) and runs and all(r.find(W+'rPr/'+W+'b') is not None for r in runs):
                    props=p.find(W+'pPr')
                    if props is None:props=ET.Element(W+'pPr');p.insert(0,props)
                    if props.find(W+'keepNext') is None:props.insert(0,ET.Element(W+'keepNext'))
        if 'word/settings.xml' in old.namelist():
            roots['word/settings.xml']=ET.fromstring(old.read('word/settings.xml'))
            enable_revision_display(roots['word/settings.xml'])
        out=io.BytesIO()
        with ZipFile(out,'w',compression=ZIP_DEFLATED) as z:
            for info in old.infolist():
                z.writestr(info,ET.tostring(roots[info.filename],xml_declaration=True,encoding='UTF-8',standalone=True) if info.filename in roots else old.read(info.filename))
    result=out.getvalue()
    if [b.text for b in parse_docx(result).blocks]!=[b.text for b in after.blocks]:
        raise ValueError('Результат сравнения не совпал с переданной новой редакцией.')
    return result,changed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('edited',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();data,changed=compare(args.source.read_bytes(),args.edited.read_bytes())
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(data);args.output.chmod(0o600)
    print(f'Отмечено изменённых абзацев/ячеек: {changed}.')
