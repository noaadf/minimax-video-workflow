"""Lint keyframe/video prompts against docs/prompt_grammar_v1.md before manifest wiring."""
import re

# 景别词汇（任一命中即视为有构图段）
_FRAMING = ('远景', '全景', '中景', '近景', '特写', '微距', '鸟瞰', '俯视', '平视', '低角度',
            'macro', 'close-up', 'wide shot', 'medium shot', 'aerial')
# 光线词汇（可选段，命中加分）
_LIGHT = ('光', '灯', '逆光', '照明', '晨光', '暮色', 'daylight', 'lamplight', 'light')
# 画面内文字风险词：出现即要求显式禁则兜底
_TEXT_RISK = ('字幕', '文字', '数字', '图表', '流程图', '标识', '标牌', 'label', 'lettering', 'text')
_BAN_PHRASES = ('不出现', '由口播', 'no text', 'no lettering', 'no overlays')
# 时间性描述：静态帧不应承载"全过程/慢慢/逐渐"
_TEMPORAL_STATIC = ('全过程', '慢慢', '逐渐', '全过程演示')
_SCHEMATIC = ('示意图', '流程图', '示意图演绎', 'diagram')


def lint(prompt, kind='keyframe'):
    """Return list of violations; empty list means the prompt passes grammar v1."""
    if not isinstance(prompt, str):
        return ['prompt must be text']
    text = prompt.strip()
    if kind == 'video':
        if len(text) < 60:
            return ['video prompt too short (<60 chars): camera move + process + frame binding required']
        missing = [w for w in ('reference frame',) if w not in text]
        if 'No cuts' not in text and 'no cuts' not in text:
            missing.append('no-cuts constraint')
        return ['video prompt missing: ' + ', '.join(missing)] if missing else []
    # Ban clauses legitimately name what must NOT appear; drop them before risk scans.
    descriptive = '；'.join(seg for seg in re.split(r'[；。;]', text)
                           if not any(b in seg for b in _BAN_PHRASES))
    violations = []
    if not 40 <= len(text) <= 200:
        violations.append('length %d outside 40-200' % len(text))
    if not any(w in descriptive for w in _FRAMING):
        violations.append('no framing term (远/全/中/近/特/微距/俯视...)')
    if any(w in descriptive for w in _SCHEMATIC):
        violations.append('schematic wording (%s) routes to the banned graphic lane' %
                          [w for w in _SCHEMATIC if w in descriptive][0])
    if any(w in descriptive for w in _TEMPORAL_STATIC):
        violations.append('temporal wording in a static keyframe')
    # 主体描述里有文字风险词时，必须显式说明文字由口播/字幕承载或禁止出现
    if any(w in descriptive for w in _TEXT_RISK) and not any(w in text for w in _BAN_PHRASES):
        violations.append('in-frame text risk without an explicit ban/字幕承载 clause')
    return violations
