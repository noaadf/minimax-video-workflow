import json
from pathlib import Path
import pytest
from workflow.mpt_composition_adapter import validate, ass_text, sha, compose, PINNED_COMMIT, parse_captions

REAL=Path('research/mpt_native_baseline_20261003/adapter_actual_v2/composition_manifest.json')


def unit_manifest(tmp,monkeypatch):
    # Pure subtitle/replay tests isolate only the expensive production evidence gate.
    monkeypatch.setattr('workflow.mpt_composition_adapter.production_inputs',lambda m:None)
    p=tmp/'captions.ass';p.write_text('Dialogue: 0,0:00:00.00,0:00:04.00,Main,,0,0,0,,完整旁白\nDialogue: 1,0:00:00.00,0:00:05.00,Label,,0,0,0,,AI生成画面，仅作示意\n')
    caps,labels=parse_captions(p,5)
    return {'duration_seconds':5,'caption_source':{'path':str(p),'sha256':sha(p)},'captions':caps,'disclosures':labels}


def save(tmp,m):
    p=tmp/'manifest.json';p.write_text(json.dumps(m));return p,sha(p)


def test_real_existing_tailings_evidence():
    if not REAL.exists():pytest.skip('Local production evidence not included in source checkout')
    m=validate(REAL,sha(REAL))
    assert len(m['shots'])==8 and m['fit_mode']=='contain'
    assert len(m['captions'])==33


def test_ass_layout(tmp_path,monkeypatch):
    m=unit_manifest(tmp_path,monkeypatch);p,h=save(tmp_path,m);assert validate(p,h)==m
    assert '完整旁白' in ass_text(m) and 'Style: ShotDisclosure' in ass_text(m)


@pytest.mark.parametrize('kind',['sha','caption','disclosure','duration','caption_source'])
def test_tamper_rejected(tmp_path,monkeypatch,kind):
    m=unit_manifest(tmp_path,monkeypatch)
    if kind=='caption':m['captions'][0]['text']='改词'
    if kind=='disclosure':m['disclosures']=[]
    if kind=='duration':m['duration_seconds']=6
    if kind=='caption_source':m['caption_source']['sha256']='bad'
    p,h=save(tmp_path,m)
    with pytest.raises(ValueError):validate(p,'bad' if kind=='sha' else h)


def test_completed_replay_never_renders(tmp_path,monkeypatch):
    m=unit_manifest(tmp_path,monkeypatch);p,h=save(tmp_path,m);out=tmp_path/'out';out.mkdir();v=out/'final.mp4';v.write_bytes(b'completed')
    r={'manifest_sha256':h,'output_path':str(v),'output_sha256':sha(v),'delivery_approved':False};(out/'receipt.json').write_text(json.dumps(r))
    monkeypatch.setattr('subprocess.check_output',lambda cmd,**kw:PINNED_COMMIT if 'rev-parse' in cmd else '')
    monkeypatch.setattr('subprocess.run',lambda *a,**k:pytest.fail('Replay rendered'))
    assert compose(p,h,out,tmp_path,'python')==r
    v.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='Existing output changed'):compose(p,h,out,tmp_path,'python')


def test_real_wrong_asset_rejected(tmp_path):
    if not REAL.exists():pytest.skip('Local production evidence unavailable')
    m=json.loads(REAL.read_text());m['shots'][0]['video']['sha256']='bad';p,h=save(tmp_path,m)
    with pytest.raises(ValueError,match='Shot binding'):validate(p,h)
