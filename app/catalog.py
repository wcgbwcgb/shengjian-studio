"""Standard API prices; gateway bills can differ from official rates."""
MODELS = {
    'claude-opus-5-5': {'id': 'claude-opus-5-5', 'name': 'Opus 5.5', 'tag': '精细判断',
                       'description': '适合复杂剪辑决策与精修', 'input_price': 4, 'output_price': 20,
                       'cache_read_price': .2, 'cache_write_price': 5, 'search_price': .01},
    'claude-sonnet-5-5': {'id': 'claude-sonnet-5-5', 'name': 'Sonnet 5.5', 'tag': '经济高效',
                         'description': '适合调研、文案和日常修改', 'input_price': 2, 'output_price': 10,
                         'cache_read_price': .2, 'cache_write_price': 2.5, 'search_price': .01},
    'gpt-6-luna': {'id': 'gpt-6-luna', 'name': 'GPT Luna', 'tag': '轻快高效',
                     'description': 'GPT-6 Luna，适合日常调研、脚本与修改；需服务支持该模型',
                     'input_price': .1, 'output_price': .5, 'cache_read_price': .01,
                     'cache_write_price': .125, 'search_price': .01}
}
STAGE_DEFAULTS = {'research': 'claude-sonnet-5-5', 'script': 'claude-sonnet-5-5', 'edit': 'claude-opus-5-5'}
for _model in MODELS.values():
    _model['provider'] = 'openai' if _model['id'].startswith('gpt-') else 'claude'


def validate(model, provider=None):
    if model not in MODELS:
        raise ValueError('请选择支持的模型：Opus 5.5、Sonnet 5.5 或 GPT Luna')
    if provider and MODELS[model]['provider'] != provider:
        raise ValueError('模型与 AI 服务不兼容，请选择当前 Provider 支持的模型')
    return model


def resolve(stage, supplied=None):
    from . import config, store
    provider = config.provider()
    if supplied:
        return validate(supplied, provider)
    chosen = config.read().get('model_defaults', {}).get(stage) or store.settings().get('model_' + stage, STAGE_DEFAULTS[stage])
    if MODELS.get(chosen, {}).get('provider') != provider:
        chosen = 'gpt-6-luna' if provider == 'openai' else STAGE_DEFAULTS[stage]
    return validate(chosen, provider)


def compatible(provider):
    return [m for m in MODELS.values() if m['provider'] == provider]


def pricing(model):
    fields = ('input_price', 'output_price', 'cache_read_price', 'cache_write_price', 'search_price')
    return {key: MODELS[validate(model)][key] for key in fields}
