"""Локальный справочник ОКПД 2 и мер национального режима (ПП РФ № 1875).

Работает без сети: данные лежат в tz_audit/data. Происхождение и редакции — в data/SOURCE.md.
"""
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from .models import Source

DATA = Path(__file__).with_name('data')
CODE_RE = re.compile(r'(?<![\d.])(\d{2}\.\d{2}\.\d{1,2}(?:\.\d{3})?)(?![\d])')
APPENDIX = {1: 'запрет допуска иностранных товаров (приложение 1)',
            2: 'ограничение допуска иностранных товаров (приложение 2)',
            3: 'преимущество российским товарам и минимальная доля закупок (приложение 3)'}
OKPD_URL = 'https://rosstat.gov.ru/classification'
PP1875_URL = 'https://docs.cntd.ru/document/1310667825'
STOP = {'и', 'в', 'на', 'для', 'с', 'из', 'по', 'или', 'не', 'прочие', 'прочий', 'других', 'другие', 'включенные', 'группировки'}


def _words(text):
    return [w for w in re.findall(r'[а-яa-z0-9]+', (text or '').lower().replace('ё', 'е')) if w not in STOP]


def _stem(word):
    # Грубая основа для русского: отрезаем окончание, сравниваем по началу слова.
    return word[:max(4, len(word) - 2)] if len(word) > 4 else word


class Reference:
    def __init__(self, data_dir=DATA):
        meta = json.loads((data_dir / 'pp1875_meta.json').read_text())
        raw = gzip.decompress((data_dir / 'pp1875.json.gz').read_bytes())
        if hashlib.sha256(raw).hexdigest() != meta['data_hash_full']:
            raise ValueError('Справочник ОКПД 2 поврежден: контрольная сумма не совпадает.')
        self.records = {r['c']: r for r in json.loads(raw)}
        self.positions = {(p['a'], p['p']): p for p in json.loads(gzip.decompress((data_dir / 'pp1875_ref.json.gz').read_bytes()))}
        self.okpd_version = '01.10.2026'
        self.pp_version = meta.get('version', '')
        self.index = [(r['c'], tuple(set(_words(r['n'])))) for r in self.records.values()]

    def measures(self, code):
        record = self.records.get(code)
        if not record:
            return []
        result = []
        for appendix in (1, 2, 3):
            position = record.get(f'a{appendix}') or 0
            if not position:
                continue
            item = self.positions.get((appendix, position), {})
            text = f'{APPENDIX[appendix]}, позиция {position}'
            if item.get('n'):
                text += f': {item["n"]}'
            quota = record.get('q') or item.get('q')
            if appendix == 3 and quota:
                text += f'; минимальная доля российских товаров {quota}%'
            if item.get('x'):
                text += f' ({item["x"]})'
            result.append(text)
        return result

    def chain(self, code):
        names, seen = [], set()
        record = self.records.get(code)
        while record and record.get('p') and record['p'] not in seen:
            seen.add(record['p'])
            record = self.records.get(record['p'])
            if record:
                names.append(f'{record["c"]} {record["n"]}')
        return list(reversed(names))[:3]

    def describe(self, code):
        record = self.records.get(code)
        if not record:
            return {'code': code, 'found': False,
                    'note': f'Кода нет в ОКПД 2 (редакция {self.okpd_version}): возможна опечатка или устаревший код.'}
        return {'code': code, 'found': True, 'name': record['n'], 'lowest_level': bool(record.get('l')),
                'groups': self.chain(code), 'national_regime': self.measures(code) or ['мер ПП № 1875 для кода нет']}

    def text(self, code):
        info = self.describe(code)
        if not info['found']:
            return f'ОКПД 2: {info["note"]}'
        lines = [f'Код ОКПД 2 {code} — {info["name"]}.']
        if info['groups']:
            lines.append('Входит в группировки: ' + '; '.join(info['groups']) + '.')
        lines.append('ПП РФ № 1875 (редакция ' + self.pp_version + '): ' + '; '.join(info['national_regime']) + '.')
        return ' '.join(lines)

    def codes_in(self, document, limit=40):
        found = []
        for block in document.blocks:
            for code in CODE_RE.findall(block.text):
                if code not in found:
                    found.append(code)
        return found[:limit]

    def search(self, query, limit=8):
        stems = {_stem(w) for w in _words(query) if len(w) > 2}
        if not stems:
            return []
        scored = []
        for code, tokens in self.index:
            hit = sum(any(t.startswith(q) or (len(t) >= 4 and q.startswith(t)) for t in tokens) for q in stems)
            if hit:
                record = self.records[code]
                scored.append((hit, bool(record.get('l')), -len(tokens), code))
        scored.sort(reverse=True)
        best = scored[0][0] if scored else 0
        return [code for hit, _, _, code in scored if hit == best or hit >= 2][:limit]

    def source(self, code, source_id):
        text = self.text(code)
        return Source(id=source_id, url=PP1875_URL if 'позиция' in text else OKPD_URL,
            title=f'ОКПД 2 {code} и ПП № 1875 (локальный справочник)', text=text,
            retrieved_at=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(text.encode()).hexdigest(),
            retrieval_method='direct', content_type='text/plain')


@lru_cache(maxsize=1)
def reference():
    return Reference()


@lru_cache(maxsize=1)
def norms():
    """Дословные выдержки ключевых норм (КонсультантПлюс, см. data/SOURCE.md): id -> запись."""
    return {n['id']: n for n in json.loads((DATA / 'norms.json').read_text())}


def norm_source(norm_id, source_id):
    n = norms()[norm_id]
    text = f'{n["title"]} ({n["edition"]}).\n{n["text"]}'
    return Source(id=source_id, url=n['url'], title=n['title'] + ' (локальная выдержка)', text=text,
        retrieved_at=n['fetched'] + 'T00:00:00+00:00', sha256=hashlib.sha256(text.encode()).hexdigest(),
        retrieval_method='direct', content_type='text/plain')


def norm_line(norm_id, prefix):
    """Строка выдержки, начинающаяся с prefix: дословная цитата для ссылки."""
    return next(line for line in norms()[norm_id]['text'].split('\n') if line.startswith(prefix))[:2000]
