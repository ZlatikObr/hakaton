"""Bounded text extraction from public PDFs, in a separate, killable process."""
import json
import os
import subprocess
import sys


def extract_pdf(data, control=None):
    from .network import NetworkError
    worker = subprocess.Popen([sys.executable, '-I', os.path.abspath(__file__)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={})
    if control: control.register_abort(worker.kill)
    try:
        out, _ = worker.communicate(data, timeout=15)
        if control: control.check()
        if worker.returncode or len(out) > 1_200_000:
            raise NetworkError('PDF не прочитан в пределах лимитов. Сканам требуется OCR.')
        result = json.loads(out)
        return result['text'], result['truncated']
    except (subprocess.TimeoutExpired, ValueError, KeyError):
        raise NetworkError('PDF не прочитан в пределах лимитов. Сканам требуется OCR.') from None
    finally:
        if control: control.unregister_abort(worker.kill)
        if worker.poll() is None: worker.kill()
        worker.communicate()


if __name__ == '__main__':
    import io
    import resource
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    except (ValueError, OSError):
        pass  # macOS не дает понизить RLIMIT_AS; в Linux/Docker лимит памяти действует.
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    from pypdf import PdfReader
    raw = sys.stdin.buffer.read(2_000_001)
    if len(raw) > 2_000_000: raise ValueError()
    reader = PdfReader(io.BytesIO(raw), strict=False)
    chunks, length = [], 0
    truncated = len(reader.pages) > 40
    for i, page in enumerate(reader.pages[:40], 1):
        text = page.extract_text() or ''
        remaining = 200_000 - length
        chunks.append(f'\n[Страница {i}]\n' + text[:remaining])
        length += len(text)
        if length >= 200_000:
            truncated = True; break
    sys.stdout.write(json.dumps({'text': '\n'.join(chunks), 'truncated': truncated}, ensure_ascii=False))
