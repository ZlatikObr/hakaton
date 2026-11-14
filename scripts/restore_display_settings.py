"""Restore the display/power preferences saved before the user's keep-awake request."""
import json
from pathlib import Path
import subprocess

backup = Path(__file__).resolve().parents[1] / 'artifacts/display-settings-before.json'
for item in json.loads(backup.read_text()):
    subprocess.run(['gsettings', 'set', item['schema'], item['key'], item['value']], check=True)
print('Прежние настройки экрана и автоматического сна восстановлены.')
