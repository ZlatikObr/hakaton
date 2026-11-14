"""Check native revisions against the original, without contacting external services."""
import argparse
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as ET
from tz_audit.document import parse_docx, W


def rejected_bytes(data):
    out=io.BytesIO()
    with ZipFile(io.BytesIO(data)) as source, ZipFile(out,'w',compression=ZIP_DEFLATED) as target:
        for info in source.infolist():
            value=source.read(info.filename)
            if info.filename=='word/document.xml':
                root=ET.fromstring(value,ET.XMLParser(resolve_entities=False,no_network=True))
                for row in list(root.iter(W+'tr')):
                    if row.find(W+'trPr/'+W+'ins') is not None:
                        row.getparent().remove(row)
                for inserted in list(root.iter(W+'ins')):
                    inserted.getparent().remove(inserted)
                for deleted in list(root.iter(W+'del')):
                    for node in deleted.iter(W+'delText'):node.tag=W+'t'
                    parent=deleted.getparent();index=parent.index(deleted)
                    for child in list(deleted):parent.insert(index,child);index+=1
                    parent.remove(deleted)
                value=ET.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
            target.writestr(info,value)
    return out.getvalue()


def verify(original, corrected):
    source=parse_docx(original);output=parse_docx(corrected);rejected=parse_docx(rejected_bytes(corrected))
    assert [(b.kind,b.text) for b in source.blocks]==[(b.kind,b.text) for b in rejected.blocks], 'Rejecting revisions did not restore original text'
    with ZipFile(io.BytesIO(original)) as a,ZipFile(io.BytesIO(corrected)) as b:
        changed=[n for n in a.namelist() if a.read(n)!=b.read(n)]
        assert set(changed)<={'word/document.xml','word/settings.xml'}, 'Unrelated ZIP parts changed'
        root=ET.fromstring(b.read('word/document.xml'))
        revisions=[n for n in root.iter() if n.tag in {W+'ins',W+'del'}]
        ids=[n.get(W+'id') for n in revisions]
        assert len(ids)==len(set(ids)), 'Duplicate revision IDs'
        return {'source_blocks':len(source.blocks),'output_blocks':len(output.blocks),'insertions':len(list(root.iter(W+'ins'))),'deletions':len(list(root.iter(W+'del'))),'new_rows':len(root.findall('.//'+W+'trPr/'+W+'ins')),'changed_parts':changed,'reject_restores_original':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('original',type=Path);parser.add_argument('corrected',type=Path)
    args=parser.parse_args();print(json.dumps(verify(args.original.read_bytes(),args.corrected.read_bytes()),ensure_ascii=False,indent=2))
