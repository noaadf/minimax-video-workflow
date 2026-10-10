"""Editorial contract and source-bounded planning. No automatic publishing."""
import json
import os
import re
import urllib.request
from pathlib import Path

TEMPLATES = {
    'engineering_tension': ['险境钩子', '具体障碍', '技术破局', '精度细节', '建设者', '通达意义'],
    'science_reveal': ['反常识问题', '现象特写', '原理可视化', '实验验证', '应用尺度', '开放问题'],
    'product_transformation': ['形态冲击', '使用痛点', '结构细节', '能力演示', '真实限制', '场景选择'],
    'global_connection': ['地图问题', '跨越距离', '关键技术', '协作现场', '实际价值', '未来视角'],
}


from .core.io import digest, sha256, write_json  # re-export


def _validate_header(p):
    if p.get('schema_version') != 1 or not re.fullmatch('[a-zA-Z0-9_-]{1,80}', p.get('id', '')):
        raise ValueError('schema_version=1 and a safe id are required')
    if p.get('template') not in TEMPLATES:
        raise ValueError('unknown template')
    length=p.get('duration_seconds')
    if type(length) not in (int,float) or not 15 <= length <= 120 or p.get('aspect_ratio') != '9:16':
        raise ValueError('production requires 15–120 seconds and 9:16')
    return length


def _validate_facts(p):
    facts = p.get('facts', [])
    if not facts or len({f['id'] for f in facts}) != len(facts):
        raise ValueError('nonempty, unique source facts required')
    ids = {f['id'] for f in facts}
    def _fact_cited(f):
        # Policy lives in fact_gate (single source of truth for provenance gating).
        from .fact_gate import requires_archive_binding
        if not requires_archive_binding(f):
            return bool(f.get('text'))
        return f.get('source_url', '').startswith('https://') and bool(f.get('text'))
    if any(not _fact_cited(f) for f in facts):
        raise ValueError('every fact needs source text and HTTPS attribution')
    return ids


def _validate_shot_collection(p, editorial):
    shots = p.get('shots', [])
    if not shots or len({s['id'] for s in shots}) != len(shots):
        raise ValueError('nonempty uniquely identified shots required')
    if not editorial and len(shots) < 6:
        raise ValueError('at least six uniquely identified shots required')
    if not editorial and len({s['camera'] for s in shots}) < 4:
        raise ValueError('at least four camera viewpoints required')
    return shots


def _validate_shot_timeline(shots, length, narrative_timed):
    if any(s['id'] in ('voice','music','compose','speech','quality','export','story_preflight') for s in shots):
        raise ValueError('shot id collides with a reserved stage name')
    if not narrative_timed and shots[0]['duration_seconds'] > 3:
        raise ValueError('first hook shot must be <=3 seconds')
    if abs(sum(s['duration_seconds'] for s in shots) - length) > .001:
        raise ValueError('shot durations must sum to duration_seconds')


def _validate_shot_details(shots, ids, narrative_timed):
    for s in shots:
        maximum = 15 if narrative_timed else 5
        if not re.fullmatch('[a-zA-Z0-9_-]+', s['id']) or not 1 <= s['duration_seconds'] <= maximum:
            raise ValueError(f'shots must be 1–{maximum} seconds with safe identifiers')
        if narrative_timed and (not isinstance(s.get('duration_reason'), str) or not s['duration_reason'].strip()):
            raise ValueError('narrative_timed_v1 requires a nonempty duration_reason for every shot')
        if not s.get('prompt') or not set(s.get('fact_ids', [])) <= ids:
            raise ValueError('shot prompt or fact references invalid')


def _validate_narration(p, length, ids):
    end = 0
    for n in p.get('narration', []):
        if not (end <= n['start'] < n['end'] <= length-.9) or not n.get('text'):
            raise ValueError('narration must be ordered, nonoverlapping and leave >=0.9s musical tail')
        if not set(n.get('fact_ids', [])) <= ids:
            raise ValueError('narration references unknown facts')
        if len(n['text']) / (n['end'] - n['start']) > 8:
            raise ValueError('narration exceeds the supported speech density')
        end = n['end']
    if not p.get('narration'):
        raise ValueError('separate narration required')


def _validate_finishing_assets(p, length):
    for effect in p.get('sound_effects', []):
        if not effect.get('path') or not 0 <= effect.get('start', 0) < length:
            raise ValueError('invalid sound effect path/start')
        if not 0 < effect.get('duration_seconds', length) <= length - effect.get('start', 0):
            raise ValueError('sound effect extends beyond video')
    music = p.get('music', {})
    if not music.get('path') or not music.get('license_note'):
        raise ValueError('music asset with provenance/license note required')
    if not p.get('cover_title') or len(p['cover_title']) > 20:
        raise ValueError('cover_title must be 1–20 characters')


def _validate_storyboard_captions(p):
    if p.get('caption_policy') != 'speech_aligned_v1':
        raise ValueError('storyboard_v1 requires speech_aligned_v1 captions')
    from .speech_timing import semantic_chunks
    for n in p.get('narration', []):
        chunks = semantic_chunks(n['text'], n.get('caption_chunks'))
        lines = n.get('caption_lines')
        if (not isinstance(lines, list) or len(lines) != len(chunks) or
                any(_invalid_caption_row(row, chunk) for row, chunk in zip(lines, chunks))):
            raise ValueError('storyboard narration needs semantic caption_lines, <=11 chars per line')


def _invalid_caption_row(row, chunk):
    return (not isinstance(row, list) or not 1 <= len(row) <= 2 or
            any(not isinstance(line, str) or not line or len(line) > 11 for line in row) or
            ''.join(row) != chunk)


def validate(p):
    # Keep this order: invalid manifests may violate several contracts at once.
    length = _validate_header(p)
    ids = _validate_facts(p)
    if p.get('review_mode', 'api') not in ('api','manual'):
        raise ValueError('review_mode must be api or manual')
    if not p.get('continuity_bible') or not p.get('comment_prompt'):
        raise ValueError('continuity bible and comment prompt required')
    storyboard = p.get('pacing_policy') == 'storyboard_v1'
    editorial = p.get('pacing_policy') in ('editorial_v2', 'storyboard_v1')
    narrative_timed = p.get('pacing_policy') in ('narrative_timed_v1', 'editorial_v2', 'storyboard_v1')
    if 'pacing_policy' in p and not narrative_timed:
        raise ValueError('unknown pacing_policy')
    shots = _validate_shot_collection(p, editorial)
    if storyboard:
        from .storyboard import validate as validate_story
        validate_story(p)
        _validate_storyboard_captions(p)
    elif editorial:
        from .editorial import validate_beats
        validate_beats(p)
    _validate_shot_timeline(shots, length, narrative_timed)
    _validate_shot_details(shots, ids, narrative_timed)
    _validate_narration(p, length, ids)
    _validate_finishing_assets(p, length)
    return p


def freeze_assets(p, base):
    """Resolve and hash inputs so a changed asset cannot silently reuse old tasks."""
    import copy
    p = copy.deepcopy(p)
    assets = {}
    def resolve(owner, key):
        if owner.get(key):
            path = Path(owner[key]).expanduser()
            path = (base / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_file():
                raise ValueError(f'missing asset: {path}')
            owner[key] = str(path)
            assets[str(path)] = sha256(path)
    if p.get('prebuilt_music'):
        if p['prebuilt_music'].get('config_sha256') != digest(p['music']):
            raise ValueError('cached music configuration differs from plan')
        resolve(p['prebuilt_music'], 'path')
    resolve(p['music'], 'path')
    if p.get('locked_audio'):
        resolve(p['locked_audio'], 'path')
        if sha256(p['locked_audio']['path']) != p['locked_audio'].get('sha256'):
            raise ValueError('locked approved audio hash differs')
    if p.get('approved_story_preflight'):
        resolve(p['approved_story_preflight'], 'path')
        if sha256(p['approved_story_preflight']['path']) != p['approved_story_preflight'].get('sha256'):
            raise ValueError('approved story preflight hash differs')
    if p.get('prebuilt_narration'):
        resolve(p['prebuilt_narration'], 'path')
        if p['prebuilt_narration']['effective_narration'] != p['narration']:
            raise ValueError('cached narration text differs from effective plan')
    for s in p['shots']:
        resolve(s, 'source_path')
        resolve(s, 'previsual_path')
        resolve(s, 'foley_source_path')
        refs = []
        for ref in s.get('reference_images', []):
            box = {'path': ref}
            resolve(box, 'path')
            refs.append(box['path'])
        s['reference_images'] = refs
    for n in p['narration']:
        resolve(n, 'audio_path')
        resolve(n, 'word_timing_path')
        if n.get('word_timing_path'):
            cue = json.loads(Path(n['word_timing_path']).read_text(encoding="utf-8"))
            resolve(cue, 'source_path')
            if assets[cue['source_path']] != cue.get('source_sha256'):
                raise ValueError('word timing audio hash differs')
    for effect in p.get('sound_effects', []):
        resolve(effect, 'path')
    p['input_sha256'] = assets
    return p


def generate(brief, destination):
    """Calls the configured text API; source selection remains an explicit input contract."""
    brief = {**brief, 'pacing_policy': brief.get('pacing_policy', 'storyboard_v1')}
    if brief['pacing_policy'] == 'storyboard_v1':
        brief['caption_policy'] = 'speech_aligned_v1'
    template = brief.get('template', 'engineering_tension')
    if template not in TEMPLATES:
        raise ValueError('unknown template')
    prompt = '''Return ONLY a JSON video manifest in Chinese. Original content-paced patriotic engineering/science short.
Keep the central factual backbone consistent with supplied facts; do not invent records, dates, accidents, measurements or product claims.
Creative freedom is encouraged: original metaphors, emotional emphasis, scene transitions, clearly subjective opinions, rhetorical questions and implications need not quote the source. Avoid turning a metaphor or hypothetical scene into a claim that a real event happened. Creative narration may have empty fact_ids; do not force every sentence into factual paraphrase.
Use concrete expressive actions, multi-angle spatially consistent visuals; scenes are AI illustration.
Visual style: natural live-action documentary photography, neutral white balance, restrained saturation, realistic skin tones and material colors, soft available light and moderate contrast. Do not default to golden-hour orange lighting, teal-orange grading, vivid advertising colors, HDR glow or crushed shadows. Preserve plausible scene lighting and color continuity across keyframes. Emotional narration must not imply exaggerated color grading. Apply this to every visual prompt and continuity bible; these remain disclosed AI illustrations, not authentic footage.
Narration separate. Follow audiovisual_profile for narration arc, tone, pace and pause design when supplied.
Otherwise use a warm measured voice: tension -> explanation -> achievement -> resonant ending.
No copy of creator wording, logos, footage or distinctive identity. Structural inspiration only.
Technical terms only if source supported. Facts are supplied verbatim; don't alter their ids/text/URLs.
At least 6 shots, each 1-5 sec, total exactly duration_seconds (choose 15–120 seconds to suit the content), >=4 distinct camera views. First shot <=3sec.
No speech or music requested in visual prompts; natural foley is fine. Stable structural geometry.
CRITICAL: image/video prompts must never request any letters, Chinese characters, titles, numbers, labels, captions, charts with text, signs, logos or watermarks. All readable text belongs exclusively to narration and deterministic post-production subtitles. Use unmarked physical scenes; keep source dates and numerical facts in narration, not generated information cards. Do not request a specific number of tiny items as an on-screen statistical claim. Scenes illustrate the story, not documentary evidence.
Choose narration length to fit the selected duration without padding; aim 4-5 chars/sec, hard ceiling 8, ordered nonoverlap, final end<=duration_seconds-0.9s.
Use 5-6 complete spoken sentences spanning shot cuts; don't force one short speech slot per shot.
Allow 3-6 seconds per sentence. Preserve a real opening question or tension and meaningful closing callback.
For an explicitly celebratory or laudatory profile, reserve 5-7 seconds for a concise, deliberate closing
sentence: use narration-segment voice:{rate:"-14%"} and preserve_delivery:true, with a meaningful pause
between clauses and at least 1.5 seconds of music after the last word. Allocate time by removing earlier
verbiage, never by accelerating this final line. Keep product explainers and neutral science at natural pace.
Comment question should invite experience or reasoned choice, no rage bait or fabricated controversy.
Design narration itself to create a concrete discussion opportunity: an accessible question/tension
in the opening, source-backed explanation in the middle, and a meaningful implication at the end.
Do not simply append 你怎么看 or an empty slogan. Preserve an emotionally resonant spoken ending;
the explicit reply invitation may live in the publication caption/pinned-comment draft.
Schema: {schema_version:1,id:string,title:string,cover_title:<=20chars,template:string,
 duration_seconds:number,aspect_ratio:"9:16",facts:[{id,text,source_url}],
 continuity_bible:string,comment_prompt:string,
 shots:[{id,camera,duration_seconds,prompt,seed,fact_ids:[]}],
 narration:[{start,end,text,fact_ids:[],voice?:{rate:string},preserve_delivery?:boolean}],music:{path,license_note},
 voice:{name:"zh-CN-YunjianNeural",pitch:"-18Hz",rate:"+0%"}}.
If the brief supplies duration_policy, it overrides legacy fixed-duration requirements and word-count quotas. Keep the id/template/music/facts from brief. English visual prompts. No Markdown.
Preserve supplied voice and audiovisual_profile. Their voice selection is a generic TTS voice, not a creator clone.
If style_reference exists, use only its general structure and adaptation guidance.
It is preliminary craft evidence, never a factual source for this new topic or proof of virality.
Do not copy the reference video's wording, footage, people, branding or topic-specific measurements.
Template beat structure: ''' + json.dumps(TEMPLATES[template], ensure_ascii=False)
    if brief.get('pacing_policy') in ('editorial_v2', 'storyboard_v1'):
        prompt = prompt.replace(
            'At least 6 shots, each 1-5 sec, total exactly duration_seconds (choose 15–120 seconds to suit the content), >=4 distinct camera views. First shot <=3sec.',
            'Use pacing_policy=editorial_v2. Choose shot count and viewpoints only to tell the story; no quota. Each shot is 1–15 seconds with a purpose and duration_reason. Total exactly duration_seconds (15–120 seconds). Add editorial:{central_question:string,beats:[{id,purpose,contribution}]}. Each beat adds information, an action or a result. Add beat_ids:[string] and purpose:string to each shot. Cover every beat. Durations are provisional until actual narration and roughcut viewing; do not pad empty time.')
        prompt = prompt.replace("Use 5-6 complete spoken sentences spanning shot cuts; don't force one short speech slot per shot.",
                                'Use only the complete spoken sentences the story needs; narration may span shot cuts.')
        prompt = prompt.replace('Allow 3-6 seconds per sentence.',
                                'Allow time for each sentence at natural delivery.')
    if brief['pacing_policy'] == 'storyboard_v1':
        prompt = prompt.replace('Use pacing_policy=editorial_v2.', 'Use pacing_policy=storyboard_v1.')
        prompt += '''
STORY-FIRST CONTRACT: plan a specific protagonist pursuing one goal with an obstacle, visible actions and consequences. A camera change is never a story event. Do not repeat a clip or add a quota of angles. Resolve the goal honestly; do not invent documentary events or factual claims.
Add story_contract:{central_question,story_one_sentence,protagonist,goal,obstacle,resolution,beats:[{id,caused_by,action,result}]}. First caused_by is null; each later beat names the preceding id. Choose only necessary beats, no count quota.
Every shot requires storyboard:{beat_id,scene_id,purpose,attention_target,action,result,cut_reason,state_before:{...},state_after:{...},transition:{kind}}. Shots realize all beats in chronological order, with adjacent shots allowed in one beat. State is a complete structured object for that scene, with identical keys before/after: track characters, prop count/positions, cargo, straps, vehicle movement, etc. Save each scene's last state across independent cutaways; returning restores it. New scenes use establish or cutaway. Existing scenes use continuous or return. Only an explicit ellipsis with explanation and allowed_changes:[exact changed state keys] permits an offscreen state jump. Unchanged state needs new_information:string. No repeated action/result/state with a different camera.
English visual prompts describe one feasible action, start pose, end pose, framing, lighting and what must stay fixed. Include no speech or generated text. Do not fake missing actions with static holds or looped footage. Keyframe paths are added after planning; never invent files. Generation waits for real start AND end reference images when state changes; unchanged explanatory shots need one start reference. Start/end poses must match the storyboard states.
Use caption_policy=speech_aligned_v1. For each narration add semantic caption_chunks:[exact text chunks], caption_lines:[[line1,optional line2],...]. Concatenated chunks equal the original text, concatenated lines equal their chunk, max 11 characters per line and 2 lines per chunk. Keep natural delivery; actual word boundaries and animatic precede video generation.
'''
    from .llm import ask_json
    last_error=None
    for attempt in range(3):
        instruction=prompt
        if last_error:instruction+='\nThe previous plan failed validation: '+last_error+'\nCorrect the structure, duration arithmetic and pacing. Return the complete corrected plan.'
        p,receipt=ask_json(instruction,{'attempt':attempt+1,'brief':brief},max_tokens=16000)
        # Source-controlled fields come from the archived brief, not from model retyping.
        for field in ('id','facts','music','template'):
            p[field]=template if field=='template' else brief[field]
        if brief.get('style_reference'):
            p['style_reference']=brief['style_reference']
        for field in ('voice','audiovisual_profile','quality_profile','pacing_policy','caption_policy'):
            if brief.get(field):p[field]=brief[field]
        if brief.get('duration_policy',{}).get('mode')=='content_driven':
            shots=p.get('shots',[])
            if shots and all(type(s.get('duration_seconds')) in (int,float) for s in shots):
                # Timing arithmetic is deterministic; preserve every shot and speech window.
                p['duration_seconds']=sum(s['duration_seconds'] for s in shots)
        from .color_policy import DEFAULT as natural_color
        p['color_policy']=dict(natural_color)
        try:
            validate(p)
        except (ValueError,KeyError,TypeError) as e:
            last_error=str(e)
            continue
        from .script_attribution import record
        record(p, receipt, "original_writer")
        from .engagement import enrich
        enrich(p)
        write_json(destination,p)
        write_json(str(destination)+'.receipt.json',{**receipt,'validation_attempts':attempt+1})
        return p
    raise ValueError('planner failed three validation attempts: '+str(last_error))
