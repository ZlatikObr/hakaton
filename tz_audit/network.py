"""Fixed-origin HTTPS transport: no proxy env, guarded opt-in redirects, DNS pinned to public IP.
Only backend-owned clients instantiate this transport; models never receive it.
"""
import http.client
import ipaddress
import json
import socket
import ssl
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urljoin, quote
from .progress import RunCancelled

class NetworkError(ValueError):
    pass


class RateLimitError(NetworkError):
    def __init__(self, retry_after=None):
        super().__init__('Внешний сервис вернул HTTP 429; тело ответа скрыто.')
        self.retry_after = retry_after


def retry_after_seconds(value):
    """Read only the standard scheduling header; never retain response bodies."""
    if not isinstance(value, str) or len(value) > 100:
        return None
    if value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, (date - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        control = getattr(self, 'control', None)
        if control: control.check()
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = sock
            if control: control.check()
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

def checked_url(url, hosts):
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise NetworkError('Некорректный URL.') from None
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password
            or port not in (None, 443) or parsed.fragment or len(url) > 1800
            or any(ord(c) < 33 for c in url) or '\\' in url):
        raise NetworkError('Адрес не разрешён сетевой политикой.')
    return parsed

class HTTPS:
    def __init__(self, control=None):
        self.control = control

    def request(self, method, url, *, hosts, headers=None, body=None, max_bytes=1_500_000,
                allow_redirects=False, url_guard=None, timeout=45, total_timeout=90, retry_rate_limits=False):
        deadline = time.monotonic() + total_timeout
        retries = 0
        for hop in range(4):
            if url_guard: url_guard(url)
            while True:
                try:
                    result = self._request_once(method, url, hosts=hosts, headers=headers, body=body,
                        max_bytes=max_bytes, timeout=timeout, deadline=deadline,
                        redirects=allow_redirects and method == 'GET')
                    break
                except RateLimitError as exc:
                    # Only an explicit rejection can be retried. Timeouts and
                    # interrupted/unknown POST outcomes must never be replayed.
                    if not retry_rate_limits or method != 'POST' or retries >= 2:
                        raise
                    delay = max(1, exc.retry_after if exc.retry_after is not None else 30 * 2**retries)
                    if delay > 60 or time.monotonic() + delay + 1 >= deadline:
                        raise
                    retries += 1
                    if self.control:
                        self.control.emit(f'HTTP 429: повтор отклонённого запроса через {delay:.0f} с ({retries}/2)')
                    self._wait(delay)
            if len(result) == 2:
                return result
            if hop == 3:
                raise NetworkError('Превышен лимит перенаправлений источника.')
            url = urljoin(url, result[2])
            # Check each target before resolving DNS or sending anything.
            checked_url(url, hosts)
        raise NetworkError('Источник недоступен.')

    def _wait(self, seconds):
        if self.control:
            self.control.event.wait(seconds)
            self.control.check()
        else:
            time.sleep(seconds)

    def _request_once(self, method, url, *, hosts, headers, body, max_bytes, timeout, deadline, redirects):
        if self.control: self.control.check()
        parsed = checked_url(url, hosts)
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
            if not addresses or not all(ipaddress.ip_address(ip).is_global for ip in addresses):
                raise NetworkError('Непубличный IP запрещён.')
            # GET may try another IP; an ambiguous POST outcome is never replayed.
            ordered = sorted(addresses, key=lambda ip: (ipaddress.ip_address(ip).version, ip))
            last_error = 'connection'
            for address in ordered[:3 if method == 'GET' else 1]:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise NetworkError('Исчерпано время соединения с источником.')
                connection = PinnedHTTPS(parsed.hostname, address, timeout=min(timeout, remaining))
                connection.control = self.control
                def abort():
                    sock = getattr(connection, 'sock', None)
                    if sock is not None:
                        try: sock.shutdown(socket.SHUT_RDWR)
                        except OSError: pass
                        connection.close()
                if self.control: self.control.register_abort(abort)
                try:
                    if self.control: self.control.check()
                    path = quote(parsed.path or '/', safe='/%:@!$&\'()*+,;=-._~')
                    if parsed.query: path += '?' + quote(parsed.query, safe='=&%/:?+;,@!$\'()*-._~')
                    connection.request(method, path, body=body, headers={'User-Agent': 'TZ-Audit/0.2', 'Accept-Encoding': 'identity', **(headers or {})})
                    if self.control: self.control.check()
                    response = connection.getresponse()
                    if redirects and response.status in {301,302,303,307,308}:
                        location = response.getheader('Location', '')
                        if not location: raise NetworkError('Источник вернул пустое перенаправление.')
                        return None, None, location
                    if response.status == 429:
                        raise RateLimitError(retry_after_seconds(response.getheader('Retry-After', '')))
                    if response.status != 200:
                        raise NetworkError(f'Внешний сервис вернул HTTP {response.status}; тело ответа скрыто.')
                    if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                        raise NetworkError('Неожиданное сжатие ответа.')
                    chunks, size = [], 0
                    while True:
                        if self.control: self.control.check()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0: raise NetworkError('Исчерпано время чтения источника.')
                        if getattr(connection, 'sock', None): connection.sock.settimeout(min(timeout, remaining))
                        chunk = response.read1(min(65536, max_bytes + 1 - size)) if hasattr(response, 'read1') else response.read(max_bytes + 1)
                        if not chunk: break
                        chunks.append(chunk); size += len(chunk)
                        if size > max_bytes: raise NetworkError('Ответ внешнего сервиса превышает лимит.')
                        if not hasattr(response, 'read1'): break
                    return b''.join(chunks), response.getheader('Content-Type', '')
                except ssl.SSLCertVerificationError:
                    raise NetworkError('Сертификат HTTPS источника не прошёл проверку; небезопасное соединение запрещено.') from None
                except (TimeoutError, socket.timeout):
                    last_error = 'timeout'
                except (OSError, http.client.HTTPException, ssl.SSLError):
                    last_error = 'connection'
                finally:
                    if self.control: self.control.unregister_abort(abort)
                    connection.close()
                if self.control: self.control.check()
            raise NetworkError('Источник не ответил в срок.' if last_error == 'timeout' else 'Не удалось установить HTTPS-соединение с доступными IP источника.')
        except RunCancelled:
            raise
        except NetworkError:
            if self.control: self.control.check()
            raise
        except Exception:
            if self.control: self.control.check()
            raise NetworkError('Ошибка внешнего соединения. Сведения запроса скрыты.') from None

    def json(self, method, url, *, hosts, headers=None, data=None, timeout=45, total_timeout=90, retry_rate_limits=False):
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode()
        raw, _ = self.request(method, url, hosts=hosts, headers={'Content-Type': 'application/json', **(headers or {})}, body=body, timeout=timeout, total_timeout=total_timeout, retry_rate_limits=retry_rate_limits)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise NetworkError('Внешний сервис вернул некорректный JSON.') from None
