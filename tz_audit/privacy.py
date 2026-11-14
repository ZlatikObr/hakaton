import functools
"""Local redaction is a detector, not a proof of absence of PII.
External use additionally requires an exact-digest local release review.
"""
import hashlib
import json
import re
import unicodedata
import threading
from importlib.util import find_spec
from dataclasses import dataclass
from functools import lru_cache
from copy import copy
from .models import Block, Document

class PrivacyError(ValueError):
    pass

PATTERNS = [
    ('EMAIL', r'(?i)[\w.+-]+\s*@\s*[\w.-]+\.[a-zа-я]{2,}'),
    ('PHONE', r'(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s).–-]*\d{3}[\s.–-]*\d{2}[\s.–-]*\d{2}(?!\d)'),
    ('PHONE', r'(?<!\w)\+\d[\d ()-]{8,19}\d(?!\d)'),
    ('SNILS', r'\b\d{3}-\d{3}-\d{3}\s?\d{2}\b'),
    ('ID', r'(?<!\d)(?:\d{12}|\d{16}|\d{20})(?!\d)'),
    ('PASSPORT', r'(?i)\b(?:паспорт|passport)(?:\s+(?:серия|номер))?\s*[:№]?\s*\d[\d -]{5,25}'),
    ('PERSON', r'\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.'),
    ('PERSON', r'\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s*[А-ЯЁ][а-яё-]+'),
    ('PERSON', r'\b[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+(?:вич|вна|ична)\b'),
    ('PERSON', r'(?i)(?:фио|ф\.и\.о\.|контактное лицо|ответственный|исполнитель|contact person)\s*[:—-]\s*[^\n|;]+'),
    ('PERSON', r'(?i:\b(?:name|first name|last name|signed by|approved by))\s*[:—-]?\s*[A-Z][a-z]+(?:[- ][A-Z][a-z]+){0,3}\b'),
    ('PERSON', r'(?i)(?:имя|фамилия|отчество)\s*[:—-]\s*[^\n|;]+'),
    ('PERSON', r'\b[A-Z][a-z]+(?:[- ][A-Z][a-z]+){1,3}\b'),
    ('ADDRESS', r'(?im)\b\d{6},?\s*г\.[^\n|;]+'),
    ('ADDRESS', r'(?im)(?:адрес[^\n:|]{0,30}:|\b(?:ул\.|улица|пер\.|переулок|проспект|пр-т|место жительства))[^\n|;]+'),
    ('BIRTH', r'(?i)(?:дата рождения|родил[а-я]+|birth date|д\.р\.)[^\n|;]{0,60}'),
    ('ACCOUNT', r'(?i)(?:инн|огрнип|сч[её]т|карта|полис|водительское удостоверение)\s*[:№]?\s*[\d -]{6,30}'),
    ('URL', r'(?i)\b(?:https?://|www\.)[^\s<>]+'),
    ('HANDLE', r'(?<!\w)@[\w.]{3,}'),
]
TECHNICAL_NOUN = re.compile(r'(?:товар(?:а|у|ом|е|ы|ов|ам|ами|ах)?|поставщик(?:а|у|ом|е|и|ов|ам|ами|ах)?|заказчик(?:а|у|ом|е|и|ов|ам|ами|ах)?|подрядчик(?:а|у|ом|е|и|ов|ам|ами|ах)?|акт(?:а|у|ом|е|ы|ов|ам|ами|ах)?|регламент(?:а|у|ом|е)?|служб(?:а|ы|е|у|ой)|сторон(?:а|ы|е|у|ой|ами)|диапазон|комплект|референс)', re.I)

def known_pattern(value):
    return (r'(?<!\w)' if value[0].isalnum() else '') + re.escape(value) + (r'(?!\w)' if value[-1].isalnum() else '')

PUBLIC_TECHNICAL_TERMS = frozenset(v.casefold() for v in ['Полноцветное','Цветной','Цветная','Лазерная','Wi-Fi','Ultra Fine','Manual Transmission','Astra Linux','Intel Core','Microsoft Windows','Windows Enterprise','Windows Server','Apple AirPrint','Российской Федерации','Российская Федерация','Россия','России','РФ'])

# Названия характеристик товара в таблицах ТЗ («Цвет чернил: синий»), которые NER принимает за имена.
ATTRIBUTE = re.compile(r'(?:цвет|материал|размер|вес|объ[её]м|длин|ширин|высот|диаметр|толщин|плотност|состав|форм[аы]|нанесени|упаковк|тираж|механизм|корпус|покрыти|емкост|ёмкост|мощност|напряжени|частот|разъ[её]м|комплектаци|назначени|фасовк|гарантия|срок|марк|модел|артикул|бренд|сери[яи])\w*', re.I)
BRAND_CONTEXT_AFTER = re.compile(r'^\s*(?:или|либо|or)\s+(?:аналог|эквивалент)', re.I)
BRAND_CONTEXT_BEFORE = re.compile(r'(?:бренд|модель|артикул|марка|производитель|серия)\s*[:–-]?\s*$', re.I)


def product_term(value, before, after):
    """Подпись характеристики или латинское название бренда/модели — не персональные данные."""
    words = value.strip(' :;•').split()
    if words and all(ATTRIBUTE.fullmatch(w.strip('.,')) for w in words):
        return True
    if len(words) == 1 and re.match(r'^\s*[а-яё]*\s*:', after) and ATTRIBUTE.match(words[0]):
        return True
    latin = bool(words) and all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.&\'-]*', w) for w in words)
    return latin and (bool(BRAND_CONTEXT_AFTER.match(after)) or bool(BRAND_CONTEXT_BEFORE.search(before)))


NON_NAME_ENDING = re.compile(r'.*(?:ние|ния|ций|ция|ость|ный|ная|ное|ные|ый|ий|ая|ое|ие|ые|изм|тор|ктор|лен|ент)$', re.I)


@functools.lru_cache(maxsize=1)
def _morph():
    from pymorphy3 import MorphAnalyzer
    return MorphAnalyzer()


@functools.lru_cache(maxsize=4096)
def common_word(word):
    """Одно русское слово, которое словарь не считает именем, фамилией или отчеством."""
    if not re.fullmatch(r'[А-Яа-яЁё-]{3,}', word):
        return False
    morph = _morph()
    parses = morph.parse(word)
    if any(t in p.tag for p in parses for t in ('Name', 'Surn', 'Patr')):
        return False
    return morph.word_is_known(word.lower()) or bool(NON_NAME_ENDING.fullmatch(word))


def technical_term(value):
    return value.strip(' :;•').casefold() in PUBLIC_TECHNICAL_TERMS

TOKEN = re.compile(r'⟦PRIVATE_\d{4,6}⟧')

SPACE_FOLD = str.maketrans({'\u00a0': ' ', '\u202f': ' ', '\u2007': ' '})

def normalize(text):
    return unicodedata.normalize('NFC', ''.join(c for c in text if unicodedata.category(c) != 'Cf')).translate(SPACE_FOLD)

@lru_cache(maxsize=1)
def ner_engine():
    try:
        from natasha import Segmenter, NewsEmbedding, NewsNERTagger
        return Segmenter(), NewsNERTagger(NewsEmbedding())
    except Exception:
        raise PrivacyError('Локальный NER недоступен. Внешние запросы запрещены.') from None

NER_LOCK = threading.RLock()
PROFILES = {
    'ensemble': ('Natasha + spaCy', 'Два локальных NER и правила. Более широкое покрытие; возможны лишние скрытия.'),
    'natasha': ('Natasha', 'Русские имена и адресные сущности; правила для контактов и реквизитов.'),
    'spacy': ('spaCy Russian MD', 'Альтернативный локальный NER; те же правила для контактов и реквизитов.'),
    'rules': ('Только правила', 'Быстро, но имена без контекста могут остаться. Особенно тщательно проверьте текст.'),
}


def privacy_profiles():
    natasha = find_spec('natasha') is not None
    spacy = find_spec('spacy') is not None and find_spec('ru_core_news_md') is not None
    return [{'id': key, 'label': label, 'description': description,
             'available': {'ensemble': natasha and spacy, 'natasha': natasha, 'spacy': spacy, 'rules': True}[key]}
            for key, (label, description) in PROFILES.items()]


@lru_cache(maxsize=1)
def spacy_engine():
    try:
        import spacy
        return spacy.load('ru_core_news_md', disable=['parser', 'lemmatizer', 'attribute_ruler', 'morphologizer'])
    except Exception:
        raise PrivacyError('Выбранный локальный spaCy NER недоступен. Выберите установленный детектор.') from None


def ner_spans(text, backend):
    spans = []
    with NER_LOCK:
        if backend in {'natasha', 'ensemble'}:
            from natasha import Doc
            segmenter, tagger = ner_engine()
            doc = Doc(text)
            doc.segment(segmenter)
            doc.tag_ner(tagger)
            spans.extend((s.start, s.stop, s.type) for s in doc.spans)
        if backend in {'spacy', 'ensemble'}:
            doc = spacy_engine()(text)
            spans.extend((s.start_char, s.end_char, s.label_) for s in doc.ents)
    return spans


@dataclass
class Redactor:
    use_ner: bool = True
    backend: str = 'natasha'

    def __post_init__(self):
        if not self.use_ner:
            self.backend = 'rules'
        if self.backend not in PROFILES:
            raise PrivacyError('Неизвестный локальный детектор ПД.')
        if not next(p['available'] for p in privacy_profiles() if p['id'] == self.backend):
            raise PrivacyError('Выбранный детектор не установлен. Выберите доступный вариант.')
        self.vault = {}
        self.counts = {}
        self.public_urls = set()
        self._span_cache = {}

    def spans(self, text):
        if text in self._span_cache:
            return list(self._span_cache[text])
        spans = []
        for kind, expression in PATTERNS:
            spans.extend((m.start(), m.end(), kind) for m in re.finditer(expression, text)
                         if kind != 'PERSON' or not technical_term(m.group()))
        # libphonenumber validates international numbering plans; regex also catches malformed contacts.
        import phonenumbers
        for match in phonenumbers.PhoneNumberMatcher(text, 'RU'):
            spans.append((match.start, match.end, 'PHONE'))
        if self.backend != 'rules':
            # Preserve table/paragraph boundaries; treat PER/PERSON and LOC/GPE conservatively.
            for chunk in re.finditer(r'[^|\n]+', text):
                for start, stop, kind in ner_spans(chunk.group(), self.backend):
                    value = chunk.group()[start:stop]
                    if kind not in {'PER', 'PERSON', 'LOC', 'GPE'} or not re.search(r'[А-Яа-яЁёA-Za-z]{3}', value):
                        continue
                    words = value.strip(' :;•').split()
                    if technical_term(value) or product_term(value, chunk.group()[:start], chunk.group()[stop:]) or (kind in {'PER', 'PERSON'} and len(words) == 1 and common_word(words[0])):
                        continue
                    if value.strip(' :;•') == 'Выберите' or all(TECHNICAL_NOUN.fullmatch(word) for word in value.strip(' :;•').split()):
                        continue
                    if value.strip(' :;•') in {'Диапазон', 'Комплект', 'Товар', 'Подрядчик', 'Поставщик', 'Заказчик', 'Референс',
                        'Российской Федерации', 'Российская Федерация', 'Россия', 'России', 'РФ', 'Восемнадцати', 'Двадцати'}:
                        continue
                    if re.fullmatch(r'(?:метод[ауе]? Роквелла|датчик[ауе]? Холла)', value, re.I):
                        continue
                    if value in {'Роквелла', 'Холла'} and re.search(r'(?:метод[ауе]?|датчик)\s*$', chunk.group()[:start], re.I):
                        continue
                    spans.append((chunk.start()+start, chunk.start()+stop, kind))
        # Detection depends only on text; vault matching below is always performed afresh.
        if len(self._span_cache) >= 1500:
            self._span_cache.clear()
        self._span_cache[text] = tuple(spans)
        return spans

    def redact(self, text):
        text = normalize(text)
        spans = self.spans(text)
        for value in list(self.vault.values()):
            if len(value) >= 4:
                spans.extend((m.start(), m.end(), 'KNOWN') for m in re.finditer(known_pattern(value), text, re.I))
        # Merge overlapping spans without exposing partial names/addresses.
        merged = []
        for start, end, kind in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end, kind])
        for start, end, kind in reversed(merged):
            value = text[start:end]
            token = next((k for k, v in self.vault.items() if v == value), None)
            if token is None:
                token = f'⟦PRIVATE_{len(self.vault)+1:04}⟧'
                self.vault[token] = value
                self.counts[kind] = self.counts.get(kind, 0) + 1
            text = text[:start] + token + text[end:]
        return text

    def assert_safe(self, text):
        # Tokens are allowed for model context but never for web queries.
        # Inspect actual string values, never JSON syntax/escaped newlines.
        try:
            structured = json.loads(text)
        except (ValueError, TypeError):
            structured = None
        if isinstance(structured, (dict, list)):
            def visit(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        self.assert_safe(key)
                        visit(item)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)
                elif isinstance(value, str):
                    self.assert_safe(value)
            visit(structured)
            return
        candidate = TOKEN.sub('[скрыто]', normalize(text))
        for url in sorted(self.public_urls, key=len, reverse=True):
            candidate = candidate.replace(url, '[открытый источник]')
        if self.spans(candidate):
            raise PrivacyError('Внешний запрос заблокирован локальным детектором ПД.')
        for private in self.vault.values():
            if len(private) >= 4 and re.search(known_pattern(normalize(private)), candidate, re.I):
                raise PrivacyError('Попытка передать значение из локального хранилища ПД.')

    def redact_external(self, text):
        """Redact public-source text without changing the released document vault.

        Source tokens are non-reversible markers: they cannot collide with the
        document tokens used to restore anchors and edits locally.
        """
        local = copy(self)
        local.vault, local.counts = dict(self.vault), dict(self.counts)
        local.public_urls, local._span_cache = set(self.public_urls), dict(self._span_cache)
        for _ in range(4):
            text = TOKEN.sub('[скрыто]', local.redact(text))
            try:
                local.assert_safe(text)
                self.assert_safe(text)
                return text
            except PrivacyError:
                continue
        raise PrivacyError('Не удалось безопасно обезличить текст внешнего источника.')

    def allow_public_url(self, url):
        # Caller must first apply exact host allowlist. Inspect decoded URL payload.
        from urllib.parse import urlsplit, unquote
        parts = urlsplit(url)
        self.assert_safe(unquote(parts.path + ' ' + parts.query))
        self.public_urls.add(url)

    def restore(self, text):
        return TOKEN.sub(lambda m: self.vault.get(m.group(), m.group()), text)

    def document(self, doc: Document):
        # Location labels can contain names, so model sees only IDs and safe structural kind.
        texts = [self.redact(b.text) for b in doc.blocks]
        # Propagate every detected value across the entire document, including earlier blocks.
        for _ in range(3):
            updated = [self.redact(t) for t in texts]
            if updated == texts:
                break
            texts = updated
        blocks = []
        for b, text in zip(doc.blocks, texts):
            self.assert_safe(text)
            blocks.append(Block(id=b.id, text=text, location=b.id, kind=b.kind))
        return Document(sha256=doc.sha256, blocks=blocks, warnings=[])


def public_payload(doc):
    return {'blocks': [{'id': b.id, 'kind': b.kind, 'text': b.text} for b in doc.blocks]}

def release_digest(doc):
    return hashlib.sha256(json.dumps(public_payload(doc), ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def check_release(doc, approved_digest):
    if not approved_digest or approved_digest != release_digest(doc):
        raise PrivacyError('Перед внешним анализом просмотрите public.json и подтвердите его точный release_digest.')
