"""Reapply current local validation/editing to an existing model response. No network calls."""
import argparse
import json
from pathlib import Path
from tz_audit.models import Report
from tz_audit.service import Service, write_private, json_bytes
from tz_audit.validation import validate_review
from tz_audit.editing import apply_edits
from tz_audit.report import render_report


def revalidate(job_id, root='runs'):
    service=Service(root);old=service.path(job_id)
    previous=Report.model_validate_json((old/'report.json').read_bytes())
    data=(old/'source.docx').read_bytes()
    manifest=service.prepare(data,'natasha')
    path,original,public,redactor=service.load(manifest['job_id'])
    review=previous.review.model_copy(deep=True)
    for finding in review.findings:
        for anchor in finding.anchors:anchor.quote=redactor.redact(anchor.quote)
    validate_review(review,public,previous.sources)
    corrected,changes=apply_edits(data,original,review,redactor)
    restored=review.model_copy(deep=True)
    for finding in restored.findings:
        for anchor in finding.anchors:anchor.quote=redactor.restore(anchor.quote)
    report=previous.model_copy(deep=True)
    report.review,report.changes=restored,changes
    report.stats.update(applied_edits=sum(c['status']=='applied' for c in changes),proposed_edits=len(changes),tracked_changes=any(c['status']=='applied' for c in changes),revalidated_from=job_id,new_model_calls=0)
    report.warnings.append('Сохранённый ответ модели повторно проверен локально; новых запросов к модели не было. Статистика токенов и стоимости относится к исходному ответу.')
    write_private(path/'corrected.docx',corrected);write_private(path/'report.docx',render_report(report,original));write_private(path/'report.json',report.model_dump_json(indent=2).encode())
    manifest.update(status='complete',mode=report.mode,revalidated_from=job_id)
    write_private(path/'manifest.json',json_bytes(manifest))
    return {'source_job_id':job_id,'job_id':manifest['job_id'],'applied_edits':report.stats['applied_edits'],'proposed_edits':len(changes)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('job_id');args=parser.parse_args()
    print(json.dumps(revalidate(args.job_id),ensure_ascii=False))
