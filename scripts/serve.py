"""Start the local workbench. Never print environment values or credentials."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def load_local_env():
    # Parse assignments only; no shell interpolation or execution.
    for name in ['.env', 'env', '.env.local']:
        path = ROOT/name
        if not path.is_file(): continue
        for line in path.read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            if line.startswith('export '): line=line[7:]
            key,value=line.split('=',1);key=key.strip();value=value.strip()
            if key not in {'OPENROUTER_API_KEY','OPENROUTER_MODEL','TZ_API_TOKEN','TZ_DATA_DIR','TZ_MAX_STEPS','TZ_MAX_OUTPUT_TOKENS','TZ_WEB_ENABLED','TZ_SEARCH_BACKEND','TZ_SEARCH_MODEL','BRAVE_API_KEY',
                'SMTP_HOST','SMTP_PORT','SMTP_USER','SMTP_PASSWORD','SMTP_FROM','TZ_AUTH_DEMO','TZ_COOKIE_SECURE',
                'LLM_API_URL','LLM_API_KEY','LLM_MODEL_NAME','TZ_RETENTION_HOURS'}: continue
            if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'": value=value[1:-1]
            elif ' #' in value: value=value.split(' #',1)[0].rstrip()
            if value: os.environ.setdefault(key, value)
    # Defaults only: explicit environment and .env values always win.
    from tz_audit.model_profiles import DEFAULT_MODEL, SEARCH_MODEL
    os.environ.setdefault('OPENROUTER_MODEL', DEFAULT_MODEL)
    os.environ.setdefault('TZ_SEARCH_MODEL', SEARCH_MODEL)
    os.environ.setdefault('TZ_WEB_ENABLED', 'true')

if __name__=='__main__':
    load_local_env()
    import uvicorn
    uvicorn.run('tz_audit.api:create_app',factory=True,host='127.0.0.1',port=int(os.getenv('TZ_PORT','8000')),access_log=False,log_level='warning')
