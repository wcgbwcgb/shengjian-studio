import math
from .store import uid
from . import assets as asset_store


def number(value):
    if isinstance(value, bool):
        raise ValueError('时间必须是数字')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('时间必须是有限数值')
    return value


def validate(timeline, assets, allow_music_cut=False):
    assets = [asset_store.rules(a) for a in assets]
    by_id = {a['id']: a for a in assets}
    segments = timeline.get('segments', [])
    if not segments or len(segments) > 200:
        raise ValueError('时间线应包含 1–200 个片段')
    if timeline.get('aspect', '9:16') not in ('9:16', '16:9', '1:1'):
        raise ValueError('画幅仅支持 9:16、16:9、1:1')
    output, cursor, ids = [], 0.0, set()
    for original in segments:
        seg = dict(original)
        asset = by_id.get(seg.get('source_asset_id'))
        if not asset or not asset.get('analysis'):
            raise ValueError('时间线引用了不存在或未分析的素材')
        start, end = number(seg['source_start_sec']), number(seg['source_end_sec'])
        if start < 0 or end <= start or end > asset['analysis']['duration'] + 0.025:
            raise ValueError(f"素材 {asset['name']} 的起止时间越界")
        seg.setdefault('id', uid())
        if seg['id'] in ids:
            raise ValueError('片段标识重复，请为每次出现的片段使用不同标识')
        ids.add(seg['id'])
        seg.update(source_start_sec=start, source_end_sec=end, output_start_sec=round(cursor, 6), output_end_sec=round(cursor + end - start, 6))
        seg.setdefault('segment_type', 'music' if asset['type'] == 'Music' else 'speech')
        seg.setdefault('reason', '用户保留')
        seg['volume'] = number(seg.get('volume', 1))
        if not 0 <= seg['volume'] <= 2:
            raise ValueError('音量系数应在 0–2 之间')
        if asset['type'] == 'Music' and seg['volume'] != 1:
            raise ValueError('音乐比较素材保持原始增益；首版不单独归一化音乐')
        output.append(seg)
        cursor += end - start
    if cursor > 3600:
        raise ValueError('首版单次输出不超过 60 分钟')
    if not allow_music_cut:
        used_ids = {s['source_asset_id'] for s in output}
        for asset in assets:
            # All uploaded standalone music is protected. Mixed regions are user marked.
            for region in asset.get('protected', []):
                a, b = number(region['start']), number(region['end'])
                spans = sorted((s['source_start_sec'], s['source_end_sec']) for s in output if s['source_asset_id'] == asset['id'])
                reached = a
                for start, end in spans:
                    if start <= reached + 0.025 and end > reached:
                        reached = end
                if reached < b - 0.025:
                    raise ValueError(f"受保护音乐 {asset['name']} {a:.2f}–{b:.2f}s 未完整保留")
    captions = []
    for caption in timeline.get('captions', []):
        start, end = number(caption['start']), number(caption['end'])
        if start < 0 or end <= start or end > cursor + 0.025:
            raise ValueError('字幕时间越界')
        captions.append({'start': start, 'end': end, 'text': str(caption['text'])[:1000]})
    return dict(timeline, segments=output, captions=captions, duration=round(cursor, 6))


def map_captions(segments, assets):
    by_id, captions = {a['id']: a for a in assets}, []
    for seg in segments:
        for line in by_id[seg['source_asset_id']].get('analysis', {}).get('transcript', []):
            a = max(float(line['start']), seg['source_start_sec'])
            b = min(float(line['end']), seg['source_end_sec'])
            if b > a:
                offset = seg['output_start_sec'] - seg['source_start_sec']
                captions.append({'start': a + offset, 'end': b + offset, 'text': line['text']})
    return captions


def full_timeline(assets, aspect='9:16'):
    result = validate({'aspect': aspect, 'segments': [
        {'id': uid(), 'source_asset_id': a['id'], 'source_start_sec': 0,
         'source_end_sec': a['analysis']['duration'], 'reason': '本地初版完整保留素材，尚未进行 AI 剪辑'} for a in assets]}, assets)
    result['captions'] = map_captions(result['segments'], assets)
    return result


def source_at(timeline, second):
    second = number(second)
    return [dict(segment_id=s['id'], source_asset_id=s['source_asset_id'],
                 source_sec=s['source_start_sec'] + second - s['output_start_sec'])
            for s in timeline['segments'] if s['output_start_sec'] <= second < s['output_end_sec']]


def srt(captions):
    def timestamp(sec):
        ms = round(sec * 1000)
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    return '\n\n'.join(f"{i}\n{timestamp(c['start'])} --> {timestamp(c['end'])}\n{c['text']}" for i, c in enumerate(captions, 1)) + '\n'
