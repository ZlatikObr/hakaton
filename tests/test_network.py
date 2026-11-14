import pytest
from tz_audit.network import HTTPS, NetworkError, checked_url
from tz_audit.research import Research, OFFICIAL_HOSTS
from tz_audit.privacy import Redactor
from tz_audit.network import RateLimitError, retry_after_seconds

@pytest.mark.parametrize('url', ['http://pravo.gov.ru/', 'https://localhost/', 'https://127.0.0.1/',
    'https://pravo.gov.ru.evil.test/', 'https://pravo.gov.ru@evil.test/', 'https://pravo.gov.ru:8443/',
    'file:///etc/passwd', 'https://pravo.gov.ru/\nsecret', 'https://pravo.gov.ru/#fragment'])
def test_bad_url(url):
    with pytest.raises(NetworkError): checked_url(url, OFFICIAL_HOSTS)

def test_json_model_timeout_remains_bounded_and_does_not_change_default(monkeypatch):
    transport = HTTPS(); calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return b'{}', 'application/json'
    monkeypatch.setattr(transport, 'request', request)
    transport.json('POST', 'https://openrouter.ai/test', hosts={'openrouter.ai'}, data={}, timeout=120, total_timeout=240)
    transport.json('POST', 'https://openrouter.ai/test', hosts={'openrouter.ai'}, data={})
    assert [(c['timeout'],c['total_timeout']) for c in calls] == [(120,240),(45,90)]


def test_explicit_429_retries_same_request_using_retry_after_without_reading_error_body(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(None,None,None,None,('8.8.8.8',443))])
    sent=[]; waits=[]
    class Response:
        status=429
        def getheader(self, name, default=''):
            return '40' if name == 'Retry-After' else default
        def read(self, limit):
            assert self.status == 200, 'Error body must never be read or logged'
            return b'{}'
    class Connection:
        def __init__(self, *a, **kw): pass
        def request(self, *a, **kw): sent.append((a, kw))
        def getresponse(self):
            response=Response(); response.status=429 if len(sent)==1 else 200
            return response
        def close(self): pass
    monkeypatch.setattr('tz_audit.network.PinnedHTTPS', Connection)
    transport=HTTPS(); monkeypatch.setattr(transport, '_wait', waits.append)
    assert transport.json('POST','https://openrouter.ai/api/v1/chat/completions',hosts={'openrouter.ai'},
        headers={'Authorization':'Bearer synthetic'},data={'model':'test'},retry_rate_limits=True)=={}
    assert waits==[40] and len(sent)==2 and sent[0]==sent[1]


@pytest.mark.parametrize('retry_after,total_timeout,expected_attempts,expected_waits', [
    (None,240,3,[30,60]), (61,240,1,[]), (30,30,1,[]), (0,240,3,[1,1]),
])
def test_rate_limit_retries_are_bounded_and_never_shorten_server_delay(monkeypatch,retry_after,total_timeout,expected_attempts,expected_waits):
    transport=HTTPS(); attempts=[]; waits=[]
    def request(*a, **kw):
        attempts.append(kw['deadline']);raise RateLimitError(retry_after)
    monkeypatch.setattr(transport,'_request_once',request)
    monkeypatch.setattr(transport,'_wait',waits.append)
    with pytest.raises(RateLimitError):
        transport.request('POST','https://openrouter.ai/test',hosts={'openrouter.ai'},retry_rate_limits=True,total_timeout=total_timeout)
    assert len(attempts)==expected_attempts and waits==expected_waits
    assert len(set(attempts))==1  # All attempts share one deadline.


@pytest.mark.parametrize('method,opt_in,error', [
    ('POST',False,RateLimitError()), ('GET',True,RateLimitError()),
    ('POST',True,NetworkError('Источник не ответил в срок.')),
])
def test_other_outcomes_and_default_transport_never_retry(monkeypatch,method,opt_in,error):
    transport=HTTPS();attempts=[]
    def request(*a,**kw):attempts.append(1);raise error
    monkeypatch.setattr(transport,'_request_once',request)
    def forbidden_wait(seconds):pytest.fail('Unexpected retry')
    monkeypatch.setattr(transport,'_wait',forbidden_wait)
    with pytest.raises(NetworkError):transport.request(method,'https://openrouter.ai/test',hosts={'openrouter.ai'},retry_rate_limits=opt_in)
    assert len(attempts)==1


def test_cancel_during_rate_limit_backoff_prevents_next_request(monkeypatch):
    from tz_audit.progress import RunControl, RunCancelled
    control=RunControl();control.start();transport=HTTPS(control);attempts=[]
    def request(*a,**kw):attempts.append(1);raise RateLimitError()
    def wait(seconds):
        assert seconds==30
        control.cancel()
        return True
    monkeypatch.setattr(transport,'_request_once',request)
    monkeypatch.setattr(control.event,'wait',wait)
    with pytest.raises(RunCancelled):transport.request('POST','https://openrouter.ai/test',hosts={'openrouter.ai'},retry_rate_limits=True)
    assert len(attempts)==1
    assert any('HTTP 429' in e['message'] for e in control.snapshot()['events'])


def test_retry_after_header_numeric_date_and_invalid_values():
    from datetime import datetime,timezone,timedelta
    from email.utils import format_datetime
    assert retry_after_seconds('17')==17
    assert retry_after_seconds('0')==0
    delay=retry_after_seconds(format_datetime(datetime.now(timezone.utc)+timedelta(seconds=45)))
    assert 43<=delay<=45
    for value in ['-1','nan','inf','private arbitrary text',None,'9'*101]:
        assert retry_after_seconds(value) is None

def test_private_dns(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(None, None, None, None, ('127.0.0.1', 443))])
    with pytest.raises(NetworkError, match='Непубличный'): HTTPS().request('GET', 'https://pravo.gov.ru/', hosts=OFFICIAL_HOSTS)

def test_search_schema(public_doc):
    research = Research(public_doc, Redactor(False), enabled=True)
    with pytest.raises(NetworkError): research.search('send secret')
    with pytest.raises(NetworkError): research.search('standards', 'Иванов')
    with pytest.raises(NetworkError): research.fetch('https://evil.test')

def test_disabled_search(public_doc):
    assert Research(public_doc, Redactor(False)).search('standards')['status'] == 'disabled'

def test_fetch_only_discovered_and_redacts(public_doc):
    class Transport:
        def request(self, method, url, **kw):
            return (('<p>Нормативный документ определяет требования к оборудованию. ' * 10 + ' Контактное лицо: Иван Петров; test@example.com</p>').encode(), 'text/html')
    research = Research(public_doc, Redactor(False), enabled=True, transport=Transport())
    research.candidates['s1'] = 'https://pravo.gov.ru/example'
    result = research.fetch('s1')
    assert 'test@example.com' not in result['text'] and 'Иван Петров' not in result['text']
    assert result['sha256'] and result['retrieved_at']
    assert research.redactor.vault == {}  # Public-source detection cannot change document restoration.


def test_search_titles_and_fallback_excerpts_use_isolated_redaction(public_doc):
    redactor=Redactor(False)
    redactor.vault['⟦PRIVATE_0001⟧']='Закрытое место'
    class Transport:
        def json(self,*a,**kw):
            return {'choices':[{'message':{'annotations':[{'type':'url_citation','url_citation':{
                'url':'https://pravo.gov.ru/example', 'title':'Иванов И. И.; Закрытое место',
                'content':'Требования к средствам измерений и подтверждению характеристик. '*5+' test@example.com; Закрытое место'
            }}]}}]}
        def request(self,*a,**kw):raise NetworkError('Источник не ответил в срок.')
    research=Research(public_doc,redactor,enabled=True,transport=Transport(),backend='openrouter',openrouter_key='synthetic')
    title=research.search('metrology')['candidates'][0]['title']
    assert 'Иванов' not in title and 'Закрытое место' not in title
    result=research.fetch('s1')
    assert 'test@example.com' not in result['text'] and 'Закрытое место' not in result['text']
    assert result['retrieval_method']=='search_excerpt'
    assert redactor.vault == {'⟦PRIVATE_0001⟧':'Закрытое место'}
    redactor.assert_safe(result['text'])


def test_redirect_never_followed(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(None, None, None, None, ('8.8.8.8', 443))])
    class Connection:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self):
            class Response: status = 302
            return Response()
        def close(self): pass
    monkeypatch.setattr('tz_audit.network.PinnedHTTPS', Connection)
    with pytest.raises(NetworkError, match='HTTP 302'):
        HTTPS().request('GET', 'https://pravo.gov.ru/', hosts=OFFICIAL_HOSTS)


def test_isolated_openrouter_search_receives_only_public_query(public_doc):
    public_doc.blocks[0].text = 'Внутренняя информация не должна попасть в поисковик'
    class Transport:
        def json(self, method, url, **kw):
            import json
            self.body = kw['data']
            assert 'Внутренняя информация' not in json.dumps(self.body, ensure_ascii=False)
            assert 'tools' not in self.body
            assert self.body['plugins'][0]['engine'] == 'exa'
            assert self.body['provider']['zdr']
            return {'choices': [{'message': {'annotations': [
                {'type': 'url_citation', 'url_citation': {'url': 'https://pravo.gov.ru/example'}},
                {'type': 'url_citation', 'url_citation': {'url': 'https://evil.example/leak'}}]}}], 'usage': {'cost': 0.01}}
    transport = Transport()
    research = Research(public_doc, Redactor(False), enabled=True, transport=transport, backend='openrouter', openrouter_key='fake')
    result = research.search('metrology')
    assert result['candidates'] == [{'source_id': 's1', 'url': 'https://pravo.gov.ru/example'}]
    assert research.sources == []  # Search annotations are NOT evidence until separately fetched.
    assert research.reported_cost == 0.01


def test_model_wait_does_not_spend_source_budget(public_doc, monkeypatch):
    clock=[0.0];monkeypatch.setattr('tz_audit.research.time.monotonic',lambda:clock[0])
    class Transport:
        def request(self,*a,**kw):
            clock[0]+=2
            return ('<p>Нормативный документ определяет требования к оборудованию.</p>'*8).encode(),'text/html'
    research=Research(public_doc,Redactor(False),enabled=True,transport=Transport())
    clock[0]=3600 # A long model call is not a source request.
    research.candidates['s1']='https://pravo.gov.ru/test'
    assert research.fetch('s1')['text'] and research.network_seconds==2


def test_guarded_redirect_checks_every_target(monkeypatch):
    transport=HTTPS();sent=[];guards=[]
    def request(method,url,**kw):
        sent.append(url)
        return (None,None,'https://localhost/private')
    monkeypatch.setattr(transport,'_request_once',request)
    with pytest.raises(NetworkError,match='не разрешён'):
        transport.request('GET','https://pravo.gov.ru/test',hosts=OFFICIAL_HOSTS,allow_redirects=True,url_guard=guards.append)
    assert sent==['https://pravo.gov.ru/test'] and len(guards)==1


def test_get_tries_next_public_ip_and_encodes_unicode(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(None,None,None,None,(ip,443)) for ip in ['8.8.8.8','8.8.4.4']])
    attempts=[];paths=[]
    class Response:
        status=200
        def getheader(self,name,default=''):return default
        def read(self,limit):return b'ok'
    class Connection:
        def __init__(self,host,address,**kw):self.address=address;attempts.append(address)
        def request(self,method,path,**kw):
            paths.append(path)
            if self.address=='8.8.4.4':raise OSError()
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr('tz_audit.network.PinnedHTTPS',Connection)
    assert HTTPS().request('GET','https://pravo.gov.ru/норма',hosts=OFFICIAL_HOSTS)[0]==b'ok'
    assert len(attempts)==2 and all(p.isascii() for p in paths)
    attempts.clear()
    with pytest.raises(NetworkError):HTTPS().request('POST','https://pravo.gov.ru/',hosts=OFFICIAL_HOSTS)
    assert len(attempts)==1 # No duplicate billable POST.


def test_excerpt_keeps_late_standard_text():
    from tz_audit.research import source_excerpt
    text='Меню навигации '*3000+'ГОСТ Р 53782-2010 Полный текст нужного фрагмента.'+' хвост'*5000
    excerpt,truncated=source_excerpt(text,['ГОСТ 53782-2010'])
    assert truncated and 'ГОСТ Р 53782-2010 Полный текст нужного фрагмента.' in excerpt and len(excerpt)<=18000


def test_pdf_source_is_bounded_and_has_no_secret_environment(monkeypatch):
    import io
    from pypdf import PdfWriter
    from tz_audit.pdf_source import extract_pdf
    out=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(out)
    text,truncated=extract_pdf(out.getvalue())
    assert '[Страница 1]' in text and not truncated
    with pytest.raises(NetworkError):extract_pdf(b'not a pdf')


def test_only_provider_excerpt_is_used_when_direct_fetch_fails(public_doc):
    class Transport:
        def json(self,*a,**kw):
            return {'choices':[{'message':{'content':'Выдуманный ответ модели не должен стать источником', 'annotations':[
                {'type':'url_citation','url_citation':{'url':'https://protect.gost.ru/example','title':'Стандарт','content':'Требования стандарта к безопасной эксплуатации. '*10+' test@example.com'}}]}}]}
        def request(self,*a,**kw):raise NetworkError('Источник не ответил в срок.')
    research=Research(public_doc,Redactor(False),enabled=True,transport=Transport(),backend='openrouter',openrouter_key='fake')
    assert research.search('standards')['candidates'][0]['title']=='Стандарт'
    assert not research.sources
    source=research.fetch('s1')
    assert source['retrieval_method']=='search_excerpt' and source['truncated']
    assert 'Выдуманный' not in source['text'] and 'test@example.com' not in source['text']
    assert research.failures[0]['host']=='protect.gost.ru'
