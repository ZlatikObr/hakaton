"""Explicit inference routes; no model/provider selection by document content."""

KIMI_PRO = 'moonshotai/kimi-k3.1-pro'
DEFAULT_MODEL = KIMI_PRO
SEARCH_MODEL = 'deepseek/deepseek-v4.1-flash-1017'
MODELS = [KIMI_PRO, SEARCH_MODEL, 'moonshotai/kimi-k2.5', 'deepseek/deepseek-v3.2']


def provider_policy(model):
    policy = {'data_collection': 'deny', 'zdr': True, 'require_parameters': True, 'allow_fallbacks': False}
    if model == KIMI_PRO:
        policy.update(only=['fireworks'], order=['fireworks'])
    return policy


def reasoning_policy(model):
    # Use the same non-thinking comparison profile as DeepSeek. No reasoning
    # trace is silently dropped from an enabled thinking conversation.
    return {'reasoning': {'enabled': False}} if model == KIMI_PRO or model.startswith('deepseek/deepseek-v4') else {}


def route_info(model):
    if model == KIMI_PRO:
        return {'provider': 'Fireworks', 'endpoint': 'fireworks', 'quantization': 'unknown',
                'label': 'Fireworks · фиксированный провайдер', 'reasoning': False}
    return {'provider': None, 'endpoint': None, 'label': 'Маршрутизация OpenRouter с ограничениями приватности'}
