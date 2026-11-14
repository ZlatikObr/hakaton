"""Authorized live check; reads credentials internally and prints only safe status.
Only the supplied, locally inspected example specifications are accepted.
"""
import json
import os
import sys
import time
from pathlib import Path
from scripts.serve import load_local_env
from tz_audit.service import Service
from tz_audit.agent import AgentConfig, OpenRouter
from tz_audit.document import parse_docx
from tz_audit.privacy import Redactor, public_payload

CASES = ['examples/Лифт - ТЗ исходное.docx','examples/Метрология - ТЗ исходное.docx']

class ObservedClient(OpenRouter):
    def complete(self, messages, config):
        feedback=[m['content'] for m in messages[-5:] if m['role']=='tool' and 'error' in m.get('content','')]
        print(json.dumps({'event':'model_request','messages':len(messages),'feedback':feedback},ensure_ascii=False),flush=True)
        response=super().complete(messages,config)
        choice=response.get('choices',[{}])[0]
        calls=choice.get('message',{}).get('tool_calls',[])
        print(json.dumps({'event':'model_response','finish':choice.get('finish_reason'),
            'tools':[{'name':c.get('function',{}).get('name'),'argument_chars':len(c.get('function',{}).get('arguments',''))} for c in calls],
            'usage':{k:v for k,v in (response.get('usage') or {}).items() if k in ['prompt_tokens','completion_tokens','cost']}},ensure_ascii=False),flush=True)
        return response

def main():
    load_local_env()
    service = Service('artifacts/live')
    index = []
    for name in (CASES[-1:] if '--metrology' in sys.argv else CASES):
        started=time.monotonic()
        manifest=service.prepare(Path(name).read_bytes())
        # The supplied samples have been inspected locally in this task; additionally
        # enforce automatic DLP and exact release binding. No blanket approval for new uploads.
        _,original,public,redactor=service.load(manifest['job_id'])
        redactor.assert_safe(json.dumps(public_payload(public),ensure_ascii=False))
        print('START',name,'job',manifest['job_id'],flush=True)
        config=AgentConfig.from_env();config.web_enabled='--no-web' not in sys.argv;config.max_output_tokens=12000
        try:
            report=service.analyze(manifest['job_id'],'openrouter',manifest['release_digest'],config=config,client=ObservedClient(os.environ.get('OPENROUTER_API_KEY','')))
            row={'input':name,'job_id':manifest['job_id'],'status':'complete','findings':len(report.review.findings),
                 'applied_edits':report.stats.get('applied_edits',0),'sources':len(report.sources),'stats':report.stats,
                 'seconds':round(time.monotonic()-started,1)}
        except Exception as exc:
            from tz_audit.network import NetworkError
            from tz_audit.privacy import PrivacyError
            from tz_audit.service import JobError
            message=str(exc) if isinstance(exc,(NetworkError,PrivacyError,JobError)) else 'Processing failed; details withheld.'
            import traceback
            frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__)]
            row={'input':name,'job_id':manifest['job_id'],'status':'failed','error':message,'frames':frames,'seconds':round(time.monotonic()-started,1)}
        index.append(row)
        Path('artifacts/live-index-metrology.json' if '--metrology' in sys.argv else 'artifacts/live-index-offline-web.json' if '--no-web' in sys.argv else 'artifacts/live-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2))
        print(json.dumps(row,ensure_ascii=False),flush=True)
    return 0 if all(x['status']=='complete' for x in index) else 1

if __name__=='__main__':sys.exit(main())
