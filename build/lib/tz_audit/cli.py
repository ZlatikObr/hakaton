import argparse
import json
import os
import sys
from pathlib import Path
from .service import Service
from .privacy import release_digest


def main():
    parser = argparse.ArgumentParser(description='Локальная проверка технических заданий')
    parser.add_argument('--data-dir', default=os.getenv('TZ_DATA_DIR', 'runs'))
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Локально разобрать и обезличить DOCX')
    prepare.add_argument('input', type=Path)
    prepare.add_argument('--privacy', choices=['ensemble','natasha','spacy','rules'], default='natasha')
    preview = commands.add_parser('preview', help='Показать обезличенный пакет и digest')
    preview.add_argument('job_id')
    redact = commands.add_parser('redact', help='Дополнительно скрыть локально указанный фрагмент')
    redact.add_argument('job_id')
    redact.add_argument('block_id')
    redact.add_argument('--text-file', type=Path, required=True, help='Локальный UTF-8 файл с точной цитатой')
    analyze = commands.add_parser('analyze', help='Сформировать JSON и DOCX отчёты')
    analyze.add_argument('job_id')
    analyze.add_argument('--mode', choices=['offline', 'openrouter'], default='offline')
    analyze.add_argument('--approve-digest', help='Digest локально проверенного public.json')
    demo = commands.add_parser('demo', help='Подготовить DOCX и выполнить локальную проверку')
    demo.add_argument('input', type=Path)
    demo.add_argument('--privacy', choices=['ensemble','natasha','spacy','rules'], default='natasha')
    args = parser.parse_args()
    try:
        service = Service(args.data_dir)
        if args.command in {'prepare', 'demo'}:
            if args.input.suffix.lower() != '.docx':
                raise ValueError('Поддерживается только DOCX.')
            from .document import MAX_FILE
            with args.input.open('rb') as file:
                data = file.read(MAX_FILE + 1)
            manifest = service.prepare(data, args.privacy)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            if args.command == 'demo':
                service.analyze(manifest['job_id'])
                print('Отчёты:', service.path(manifest['job_id']))
        elif args.command == 'preview':
            result = service.preview(args.job_id)
            _, _, public, _ = service.load(args.job_id)
            result['release_digest'] = release_digest(public)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == 'redact':
            print(json.dumps(service.redact_more(args.job_id, args.block_id, args.text_file.read_text().rstrip('\n')), ensure_ascii=False, indent=2))
        elif args.command == 'analyze':
            service.analyze(args.job_id, args.mode, args.approve_digest)
            print('Отчёты:', service.path(args.job_id))
    except Exception as exc:
        from .document import DocumentError
        from .privacy import PrivacyError
        from .network import NetworkError
        from .service import JobError
        if isinstance(exc, (DocumentError, PrivacyError, NetworkError, JobError)):
            print('Ошибка:', str(exc), file=sys.stderr)
        else:
            print('Ошибка обработки. Проверьте файл и настройки; входные данные и секреты не выводятся.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
