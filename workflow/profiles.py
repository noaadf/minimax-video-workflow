"""Reusable editorial/voice/music combinations with publication attribution."""
import copy
import hashlib
import json
from pathlib import Path


def select(library_path, identity, template, explicit=None):
    library = json.loads(Path(library_path).expanduser().read_text(encoding="utf-8"))
    profiles = library['profiles']
    choices = [p for p in profiles if (p['id'] == explicit if explicit else template in p['templates'])]
    if not choices: raise ValueError('no compatible audiovisual profile')
    return copy.deepcopy(choices[int(hashlib.sha256(identity.encode()).hexdigest(),16) % len(choices)])


def apply(brief, profile):
    brief.update(voice=profile['voice'], music=profile['music'],
                 audiovisual_profile={k:v for k,v in profile.items() if k not in ('music','voice')})
    return brief


def publication_text(plan):
    lines = [plan['title'],plan['comment_prompt'],'AI演绎 · 非现场实拍']
    lines.extend(dict.fromkeys(f['source_url'] for f in plan['facts']))
    music = plan.get('music',{})
    if music.get('credit'):
        lines += ['', '配乐：'+music['credit'], music.get('source_url',''), music.get('license_url','')]
        if music.get('changes'): lines.append('配乐处理：'+music['changes'])
    return '\n'.join(lines)+'\n'
