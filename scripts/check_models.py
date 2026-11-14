"""Public catalog only; does not read documents, environment API keys or job storage."""
import json
import sys
from tz_audit.network import HTTPS

if __name__ == '__main__':
    requested = sys.argv[1:] or ['deepseek/deepseek-v4.1-flash-1017', 'moonshotai/kimi-k2.5', 'moonshotai/kimi-k3.1-pro', 'deepseek/deepseek-v3.2']
    response = HTTPS().json('GET', 'https://openrouter.ai/api/v1/models', hosts={'openrouter.ai'})
    found = {row['id']: row for row in response['data']}
    for name in requested:
        row = found.get(name)
        if not row:
            print(json.dumps({'model': name, 'available': False})); continue
        prices = row.get('pricing', {})
        print(json.dumps({'model': name, 'available': True, 'context_length': row.get('context_length'),
            'input_usd_per_million': float(prices.get('prompt', 0))*1e6,
            'output_usd_per_million': float(prices.get('completion', 0))*1e6,
            'tool_support': 'tools' in row.get('supported_parameters', []),
            'note': 'Catalog availability does not guarantee a ZDR route. Verify with a live test.'}, ensure_ascii=False))
