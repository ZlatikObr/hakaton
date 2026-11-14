"""Прогноз времени проверки и срок хранения файлов.

Прогноз считается для каждого документа по объему обезличенного текста и
калибруется по фактической длительности прошлых проверок на этом сервере.
Хранятся только числа: объем текста и длительность, без содержимого.
"""
import json
import os
import shutil
import statistics
import threading
import time
from pathlib import Path

# До накопления статистики: ~3 мин на два прохода модели плюс ~8 с на 1000 знаков
# (документ на 40 000 знаков выходит около 6 минут, как в ручном тестировании).
BASE = {'openrouter': 180.0, 'offline': 2.0}
PER_CHAR = {'openrouter': 0.008, 'offline': 0.0001}
LIMITS = {'openrouter': (120, 900), 'offline': (2, 60)}
KEEP = 50


def document_chars(public):
    blocks = public['blocks'] if isinstance(public, dict) else public.blocks
    return sum(len(b['text'] if isinstance(b, dict) else b.text) for b in blocks)


class Timings:
    def __init__(self, root):
        self.path = Path(root) / '.timings.json'
        self.lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def record(self, mode, chars, seconds):
        if mode not in BASE or chars <= 0 or seconds <= 0:
            return
        with self.lock:
            data = self._load()
            runs = (data.get(mode) or [])[-(KEEP - 1):] + [[int(chars), round(float(seconds), 1)]]
            data[mode] = runs
            tmp = self.path.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(data, handle)
            os.replace(tmp, self.path)

    def estimate(self, mode, chars):
        base, per_char = BASE[mode], PER_CHAR[mode]
        runs = [(c, s) for c, s in self._load().get(mode, []) if c > 0 and s > 0]
        if len(runs) >= 3:
            # Медиана отношения факта к формуле: устойчиво к единичным долгим запускам.
            factor = statistics.median(s / (base + per_char * c) for c, s in runs)
            factor = min(4.0, max(0.25, factor))
            base, per_char = base * factor, per_char * factor
        low, high = LIMITS[mode]
        return int(round(min(high, max(low, base + per_char * chars))))


def newest_mtime(path):
    times = [path.stat().st_mtime] + [p.stat().st_mtime for p in path.iterdir() if p.is_file()]
    return max(times)


def purge_expired(root, hours, active=frozenset(), now=None):
    """Удаляет каталоги заданий, к которым не обращались дольше срока хранения."""
    if hours <= 0:
        return []
    cutoff = (now or time.time()) - hours * 3600
    removed = []
    for path in Path(root).iterdir():
        if not path.is_dir() or path.is_symlink() or len(path.name) != 32 or path.name in active:
            continue
        try:
            if newest_mtime(path) < cutoff:
                shutil.rmtree(path)
                removed.append(path.name)
        except OSError:
            continue
    return removed
