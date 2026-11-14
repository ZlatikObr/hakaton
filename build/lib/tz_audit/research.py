import hashlib
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, parse_qs, unquote
from contextlib import contextmanager
from .models import Source
from .network import HTTPS, checked_url, NetworkError

OFFICIAL_HOSTS = frozenset({'pravo.gov.ru', 'publication.pravo.gov.ru', 'www.consultant.ru',
    'docs.eaeunion.org', 'eec.eaeunion.org', 'www.gost.ru', 'protect.gost.ru',
    'fgis.gost.ru', 'zakupki.gov.ru', 'rosstat.gov.ru', 'www.rst.gov.ru'})
# Model controls topic identifiers only. Query text and host filters are server owned.
TOPICS = {
    'procurement_44': '44-ФЗ статья 33 описание объекта закупки',
    'procurement_223': '223-ФЗ описание предмета закупки эквивалент',
    'national_regime': 'национальный режим закупки постановление 1875',
    'classification': 'ОКПД 2 классификатор продукция услуги',
    'warranty': 'ГК РФ гарантия качества работ приемка результата работ',
    'metrology': '102-ФЗ поверка средства измерений утвержденного типа',
    'standards': 'стандарты действующий статус замена ГОСТ',
    'safety': 'технические регламенты ЕАЭС безопасность оборудования',
}
STANDARD_RE = re.compile(r'\b(?:ГОСТ(?:\s+Р)?(?:\s+ИСО)?|СП|ТР\s+ТС)\s+\d{1,6}(?:[./-]\d{1,6}){0,3}\b')

class ExtractHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks, self.links = [], []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden += 1
        if tag == 'a':
            self.links += [value for key, value in attrs if key == 'href' and value]
        if tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'tr'}:
            self.chunks.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)
    def plain(self):
        return re.sub(r'\s+', ' ', ' '.join(self.chunks)).strip()

class Research:
    def __init__(self, document, redactor, *, enabled=False, brave_key='', transport=None, backend='duckduckgo', openrouter_key='', search_model='deepseek/deepseek-v4.1-flash-1017'):
        self.redactor = redactor
        self.enabled = enabled
        self.brave_key = brave_key
        self.transport = transport or HTTPS()
        # References are selected from released text, never from tool-supplied strings.
        document_text = '\n'.join(b.text for b in document.blocks)
        standards = sorted(set(STANDARD_RE.findall(document_text)))[:60]
        self.references = {f'n{i+1}': ref for i, ref in enumerate(standards)}
        codes = sorted(set(re.findall(r'\b\d{2}\.\d{2}\.\d{2}\.\d{3}\b', document_text)))[:30]
        self.references.update({f'c{i+1}': 'ОКПД 2 ' + code for i, code in enumerate(codes)})
        # Only fixed public subject names may enrich the isolated search query.
        subjects = [(r'\bлифт', 'лифты монтаж'), (r'\bМФУ\b|многофункциональн', 'многофункциональные устройства печати'), (r'\bмикроскоп', 'микроскопы')]
        self.subjects = [label for pattern, label in subjects if re.search(pattern, document_text, re.I)]
        self.candidates = {}
        self.search_excerpts = {}
        self.search_titles = {}
        self.sources = []
        self.calls = 0
        self.backend = backend
        self.openrouter_key = openrouter_key
        self.search_model = search_model
        self.reported_cost = 0.0
        self.network_seconds = 0.0
        self.fetch_attempts = 0
        self.failures = []

    @contextmanager
    def network_budget(self):
        if self.network_seconds >= 180:
            raise NetworkError('Исчерпан бюджет сетевых операций; непроверенные нормы требуют отдельной проверки.')
        started = time.monotonic()
        try:
            yield
        finally:
            self.network_seconds += time.monotonic() - started

    def search(self, topic, reference_id=None):
        if topic not in TOPICS or (reference_id is not None and reference_id not in self.references):
            raise NetworkError('Неизвестная тема или нормативный идентификатор.')
        if not self.enabled:
            return {'status': 'disabled', 'message': 'Интернет выключен. Не утверждай актуальность норм без источников.'}
        if self.network_seconds >= 180:
            return {'status': 'budget_exhausted', 'message': 'Время поиска исчерпано. Заверши анализ документа; неподтверждённые нормы отметь для ручной проверки.'}
        if self.calls >= 6:
            raise NetworkError('Лимит поиска исчерпан.')
        self.calls += 1
        query = TOPICS[topic]
        if reference_id:
            query = self.references[reference_id] + (' обозначение статус стандарта' if topic == 'standards' else ' ' + TOPICS[topic])
        if topic in {'classification', 'standards', 'safety'} and not reference_id:
            query += ' ' + ' '.join(self.subjects[:2])
        self.redactor.assert_safe(query)
        if self.backend != 'openrouter':
            query += ' (' + ' OR '.join('site:' + h for h in sorted(OFFICIAL_HOSTS)) + ')'
        if self.backend == 'openrouter':
            if not self.openrouter_key:
                raise NetworkError('Для поиска через OpenRouter нужен OPENROUTER_API_KEY.')
            # Isolated one-shot search worker: no document/history/vault is ever included.
            with self.network_budget():
                response = self.transport.json('POST', 'https://openrouter.ai/api/v1/chat/completions',
                hosts={'openrouter.ai'}, headers={'Authorization': 'Bearer ' + self.openrouter_key}, data={
                    'model': self.search_model,
                    'messages': [{'role': 'user', 'content': 'Найди открытые нормативные источники по запросу. Дай ссылки. Запрос: ' + query}],
                    'max_tokens': 2000,
                    **({'reasoning': {'enabled': False}} if self.search_model.startswith('deepseek/deepseek-v4') else {}),
                    'provider': {'zdr': True, 'data_collection': 'deny', 'allow_fallbacks': False},
                    'plugins': [{'id': 'web', 'engine': 'exa', 'max_results': 5, 'include_domains': sorted(OFFICIAL_HOSTS)}],
                })
            try:
                annotations = response['choices'][0]['message'].get('annotations', [])
                links = [a['url_citation']['url'] for a in annotations if a.get('type') == 'url_citation']
                # Provider supplied extractive highlights only. Never use assistant prose as evidence.
                for annotation in annotations:
                    value = annotation.get('url_citation', {})
                    url, content = value.get('url'), value.get('content')
                    if annotation.get('type') != 'url_citation' or not isinstance(url, str): continue
                    try:
                        checked_url(url, OFFICIAL_HOSTS)
                        self.redactor.allow_public_url(url)
                    except ValueError: continue
                    if isinstance(value.get('title'), str):
                        self.search_titles[url] = self.redactor.redact_external(value['title'][:300])
                    if isinstance(content, str) and len(content.strip()) >= 100:
                        self.search_excerpts[url] = content[:18000]
                cost = (response.get('usage') or {}).get('cost', 0)
                if isinstance(cost, (float, int)) and cost >= 0:
                    self.reported_cost += cost
            except (KeyError, TypeError, IndexError):
                raise NetworkError('Некорректный ответ поискового сервиса.') from None
        elif self.backend == 'brave':
            if not self.brave_key:
                raise NetworkError('Для Brave Search нужен BRAVE_API_KEY.')
            with self.network_budget():
                result = self.transport.json('GET', 'https://api.search.brave.com/res/v1/web/search?' + urlencode({'q': query, 'count': 5}),
                    hosts={'api.search.brave.com'}, headers={'X-Subscription-Token': self.brave_key})
            links = [x.get('url', '') for x in result.get('web', {}).get('results', [])]
        elif self.backend == 'duckduckgo':
            with self.network_budget():
                raw, _ = self.transport.request('GET', 'https://html.duckduckgo.com/html/?' + urlencode({'q': query}), hosts={'html.duckduckgo.com'})
            html = ExtractHTML()
            html.feed(raw.decode('utf-8', errors='replace'))
            links = []
            for link in html.links:
                if 'uddg=' in link:
                    link = parse_qs(urlsplit(link).query).get('uddg', [''])[0]
                links.append(link)
        else:
            raise NetworkError('Неизвестный поисковый backend.')
        result = []
        for url in links:
            if not isinstance(url, str):
                continue
            try:
                checked_url(url, OFFICIAL_HOSTS)
                self.redactor.allow_public_url(url)
            except ValueError:
                continue
            if url in self.candidates.values():
                continue
            sid = f's{len(self.candidates)+1}'
            self.candidates[sid] = url
            result.append({'source_id': sid, 'url': url, **({'title': self.search_titles[url]} if url in self.search_titles else {})})
            if len(result) == 5:
                break
        return {'status': 'ok' if result else 'no_sources', 'candidates': result}

    def fetch(self, source_id):
        if not self.enabled or source_id not in self.candidates:
            raise NetworkError('Разрешены только источники, полученные поиском этого задания.')
        old = next((s for s in self.sources if s.id == source_id), None)
        if old:
            return old.model_dump()
        if len(self.sources) >= 6:
            raise NetworkError('Лимит источников исчерпан.')
        if self.fetch_attempts >= 8:
            raise NetworkError('Лимит попыток чтения источников исчерпан.')
        self.fetch_attempts += 1
        url = self.candidates[source_id]
        self.redactor.allow_public_url(url)
        try:
            retrieval_method = 'direct'
            with self.network_budget():
                raw, content_type = self.transport.request('GET', url, hosts=OFFICIAL_HOSTS, max_bytes=2_000_000,
                    allow_redirects=True, url_guard=self.redactor.allow_public_url, timeout=10, total_timeout=30)
            truncated = False
            if 'application/pdf' in content_type.lower() or raw.startswith(b'%PDF-'):
                from .pdf_source import extract_pdf
                plain, truncated = extract_pdf(raw, getattr(self.transport, 'control', None))
            elif any(t in content_type.lower() for t in ['text/html', 'text/plain', 'application/xhtml+xml']):
                charset = 'windows-1251' if '1251' in content_type or re.search(br'charset\s*=\s*["\x27]?windows-1251', raw[:10000], re.I) else 'utf-8'
                html = ExtractHTML(); html.feed(raw.decode(charset, errors='replace'))
                plain = html.plain()
            else:
                raise NetworkError('Формат источника не поддерживается: нужны HTML, текст или PDF.')
            text, shortened = source_excerpt(plain, self.references.values())
            truncated = truncated or shortened
            text = self.redactor.redact_external(text)
        except NetworkError as exc:
            self.failures.append({'source_id': source_id, 'host': urlsplit(url).hostname, 'reason': str(exc)})
            excerpt = self.search_excerpts.get(url)
            if not excerpt: raise
            text = self.redactor.redact_external(excerpt)
            truncated, content_type, retrieval_method = True, 'text/plain', 'search_excerpt'
            control = getattr(self.transport, 'control', None)
            if control: control.emit('Прямое чтение недоступно; проверяем выдержку поискового индекса с явной пометкой ограничения')
        self.redactor.assert_safe(text)
        if len(text) < 100:
            raise NetworkError('Не удалось получить содержательный текст источника.')
        source = Source(id=source_id, url=url, title=urlsplit(url).hostname, text=text,
            retrieved_at=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(text.encode()).hexdigest(), truncated=truncated, content_type=content_type, retrieval_method=retrieval_method)
        self.sources.append(source)
        return source.model_dump()


def source_excerpt(text, references, limit=18000):
    if len(text) <= limit:
        return text, False
    # Preserve exact text inside excerpts; never synthesize evidence from search snippets.
    terms = [re.escape(re.sub(r'^.*?\s(?=\d)', '', r)) for r in references]
    terms += [r'ОКПД', r'гаранти', r'лифт', r'приемк', r'приёмк']
    spans = [(0, 2000)]
    for match in re.finditer('|'.join(terms), text[:300000], re.I):
        left, right = max(0, match.start()-800), min(len(text), match.end()+1800)
        if left <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(right, spans[-1][1]))
        else:
            spans.append((left, right))
        if sum(b-a for a,b in spans) >= limit: break
    return '\n[... пропуск части источника ...]\n'.join(text[a:b] for a,b in spans)[:limit], True
