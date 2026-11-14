"""Run only prepared, locally reviewed public fixtures; credentials stay in the server.
No automatic approval for arbitrary files. Inspect each public.json before using this runner.
"""
import json
import time
from pathlib import Path
import httpx
from tz_audit.service import Service
from tz_audit.privacy import public_payload, release_digest

CASES=['mpei_goods','updk_mfu','updk_mfu_seeded','user_lift']


def main():
    rows=[]
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=60) as client:
        client.get('/').raise_for_status()
        settings=client.get('/settings').json()
        assert settings['has_key']
        for name in CASES:
            manifest=json.loads(Path(f'artifacts/public-{name}-prepared.json').read_text())
            job=manifest['job_id'];_,original,public,redactor=Service('runs').load(job)
            payload=json.dumps(public_payload(public),ensure_ascii=False)
            redactor.assert_safe(payload)
            assert all(value not in payload for value in ['Иванов Иван Иванович','contact@example.com','+7 (000) 123-45-67'])
            assert release_digest(public)==manifest['release_digest']
            start=time.monotonic();last=0
            response=client.post(f'/jobs/{job}/run',json={'mode':'openrouter','model':settings['model'],'approved_digest':manifest['release_digest'],'web_enabled':False})
            response.raise_for_status()
            print(json.dumps({'case':name,'job_id':job,'event':'started'}),flush=True)
            while time.monotonic()-start<550:
                state=client.get(f'/jobs/{job}/status').json()
                for event in state.get('events',[]):
                    if event['seq']>last:
                        print(json.dumps({'case':name,'event':event['message']},ensure_ascii=False),flush=True)
                        last=event['seq']
                if state['status'] in {'complete','failed','cancelled'}:break
                time.sleep(1)
            else:
                client.post(f'/jobs/{job}/cancel',json={})
                state={'status':'timeout'}
            row={'case':name,'job_id':job,'status':state['status'],'seconds':round(time.monotonic()-start,1),'web_enabled':False,'privacy_backend':redactor.backend}
            if state['status']=='complete':
                report=client.get(f'/jobs/{job}/report.json').json()
                row.update(findings=len(report['review']['findings']),applied_edits=report['stats']['applied_edits'],stats=report['stats'],model=report['model'])
                for extension in ['report.json','report.docx','corrected.docx']:
                    response=client.get(f'/jobs/{job}/{extension}');response.raise_for_status()
                    path=Path('artifacts/public-evaluation-v2')/name/extension;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(response.content);path.chmod(0o600)
                Path(f'artifacts/public-evaluation-v2/{name}/progress.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
            elif 'error' in state:row['error']=state['error']
            rows.append(row)
            Path('artifacts/public-evaluation-v2-index.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
            print(json.dumps(row,ensure_ascii=False),flush=True)
    return rows


if __name__=='__main__':main()
