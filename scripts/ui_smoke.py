"""Live test of the local UI API; only authorized supplied samples, no credentials in output."""
import json
import time
from pathlib import Path
import httpx
from tz_audit.privacy import Redactor, public_payload, release_digest
from tz_audit.service import Service

name='Метрологическое_оборудование_исходник.docx'
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=60) as c:
    assert c.get('/').status_code==200
    settings=c.get('/settings').json()
    assert settings['has_key']
    result=c.post('/jobs',files={'file':(name,Path(name).read_bytes())});result.raise_for_status()
    job=result.json()['job_id']
    preview=c.get('/jobs/'+job).json()
    _,_,public,redactor=Service('runs').load(job)
    redactor.assert_safe(json.dumps(public_payload(public),ensure_ascii=False))
    assert release_digest(public)==preview['release_digest']
    start=time.monotonic()
    r=c.post('/jobs/'+job+'/run',json={'mode':'openrouter','approved_digest':preview['release_digest'],'model':settings['model'],'web_enabled':True});r.raise_for_status()
    print(json.dumps({'job':job,'status':'started'},ensure_ascii=False),flush=True)
    while time.monotonic()-start<600:
        state=c.get('/jobs/'+job+'/status').json()
        if state['status']=='failed':raise RuntimeError(state['error'])
        if state['status']=='complete':break
        time.sleep(1)
    else:raise RuntimeError('UI analysis timeout')
    report=c.get('/jobs/'+job+'/report.json').json()
    for filename in ['corrected.docx','report.docx']:
        data=c.get('/jobs/'+job+'/'+filename);assert data.status_code==200 and data.content.startswith(b'PK')
    result={'job_id':job,'input':name,'status':'complete','seconds':round(time.monotonic()-start,1),'stats':report['stats'],'findings':len(report['review']['findings']),'sources':len(report['sources'])}
    Path('artifacts/ui-live-index.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)
