"""One new release story: original state-transition supplements, no production calls."""
import json, subprocess
from pathlib import Path
from PIL import Image, ImageDraw
from jsonschema import Draft202012Validator, FormatChecker
from .build_cycle_20261003 import font, text, node
from .core import content_id, fact_id, sha256, write_json, validate, enqueue_candidate, legacy_records
from .media import probe

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'research/material_pipeline'
C = R / 'monitor/runs/20261003T201010_124676'
SOURCE = C / 'supplementary/sources/120d1154c59d7ec6a753'
URL = 'https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.1-alpha.1'
TITLE = 'AI任务停了又开，消息为什么卡在队列里'
ENTITY = 'DeepSeek Harness开发团队dsh-v0.2.1-alpha.1预览版'
EVENT = dict(entity=ENTITY, action='发布任务状态与运行环境修复的预览版', date='2026-10-03', object='暂停恢复后的消息滞留、日志展开、插件依赖与桌面端口修复')
CLAIMS = [
    ('发布版本', '2026年10月3日发布alpha预览版', 'dsh-v0.2.1-alpha.1', 'DeepSeek Harness团队于10月3日发布v0.2.1-alpha.1预览版；不是稳定版。'),
    ('状态修复', '停止恢复再停止后新消息滞留问题修复', '修复目标任务停止、恢复并再次停止后，新消息滞留队列的问题。', '发布说明称，修复目标任务停止、恢复并再次停止后，新消息滞留队列的问题。'),
    ('诊断修复', '部分命令输出和文件差异记录可展开', '修复部分 Bash、PowerShell 和文件修改记录无法展开输出或差异的问题。', '发布说明称，修复部分Bash、PowerShell和文件修改记录无法展开输出或差异的问题。'),
    ('依赖修复', '启用找不到依赖及卸载后残留映射问题修复', '修复新组合包启用后找不到依赖，以及停用或卸载后残留依赖映射的问题；替换已安装包的版本仍需重启。', '发布说明称，修复组合包启用后找不到依赖以及停用或卸载后残留依赖映射的问题；更换已安装包版本仍需重启。'),
    ('启动修复', '桌面端默认使用系统分配端口', '桌面端默认使用系统分配的端口，避免 Windows 保留端口范围导致应用无法启动。', '发布说明称，桌面端默认使用系统分配的端口，避免Windows保留端口范围造成启动失败。'),
]
PANELS = [
    ('AI任务停了又开，消息卡住了？', '今天发布：v0.2.1-alpha.1预览版', [1,2], 'AI任务停了又开，消息为什么卡住？DeepSeek今天发布预览版，修复了状态切换中的问题。'),
    ('问题出在这一组状态切换', '停止 → 恢复 → 再停止 → 新消息', [2], '说明称，停止、恢复再停止后，新消息滞留队列的问题已修复。画面是状态示意。'),
    ('追查时，还得看得见记录', '命令记录展开 / 插件依赖清理', [3,4], '排查还要看日志。更新修复部分命令输出无法展开，并处理插件依赖残留。'),
    ('能打开应用，才有后续任务', '系统分配端口 · Windows启动问题', [5], '桌面启动改用系统分配端口，避开Windows保留端口。修复效果，仍待实测。'),
]

def frame(i, t):
    im = Image.new('RGB', (720,1280), '#0c1726'); d = ImageDraw.Draw(im)
    text(d, f'0{i+1} / 04 · 官方发布 2026-10-03', 50, 55, 23, '#88dac7')
    text(d, PANELS[i][0], 50, 145, 41); text(d, PANELS[i][1], 50, 292, 25, '#a5b4c8')
    if i == 0:
        for j,label in enumerate(['已有任务','停止 / 恢复','新消息等待']):
            node(d, (95,435+j*185,625,545+j*185), label, '#88dac7', 32)
        d.line((360,545,360,805),fill='#577969',width=4)
        y = 560 + min(t/8,1)*200; d.ellipse((350,y-10,370,y+10),fill='#edcb8c')
        text(d, '问题与修复均来自发布说明', 65,1045,27,'#a5b4c8')
    elif i == 1:
        labels=['第一次停止','恢复','再次停止','新消息进入队列']
        for j,label in enumerate(labels):
            colour='#88dac7' if t >= j*1.8 else '#657386'
            node(d,(115,425+j*146,605,525+j*146),label,colour,29)
        text(d,'标记触发步骤，不伪造软件运行结果',55,1060,26,'#a5b4c8')
    elif i == 2:
        node(d,(75,435,645,558),'查看命令输出与文件差异','#88dac7',28)
        for j in range(4):
            w=int(420*min(max((t-j*.45)/3,0),1)); y=603+j*44
            d.rounded_rectangle((125,y,126+w,y+17),radius=6,fill='#809fc1')
        node(d,(75,852,645,973),'启停插件：清理依赖映射','#edcb8c',28)
        text(d,'替换已安装包的版本：仍需重启',60,1060,26,'#a5b4c8')
    else:
        node(d,(95,450,625,570),'桌面应用启动','#88dac7',31)
        node(d,(95,710,625,830),'由系统分配端口','#88dac7',31)
        d.line((360,570,360,710),fill='#577969',width=4)
        y=580+min(t/8,1)*115;d.ellipse((350,y-10,370,y+10),fill='#edcb8c')
        text(d,'这是发布方修复报告',95,951,29,'#a5b4c8')
        text(d,'不是本轮独立测试',95,1003,29,'#a5b4c8')
    d.line((50,1160,670,1160),fill='#2d4159',width=2)
    text(d,'原创机制示意 · 静音补充素材 · 待审',50,1202,22,'#a6bacd')
    return im

def main():
    cid=content_id(EVENT); body=(SOURCE/'source.txt').read_text(encoding="utf-8"); meta=json.loads((SOURCE/'source.json').read_text(encoding="utf-8"))
    stamp='Release publication datetime attribute: 2026-10-03T06:42:19Z'
    assert stamp in body and 'datetime="2026-10-03T06:42:19Z"' in (SOURCE/'source.html').read_text(encoding="utf-8")
    source=dict(id='S1',url=URL,title=meta['title'],publisher='DeepSeek Harness开发团队',role='primary',published='2026-10-03',date_evidence=stamp,archive_path=str((SOURCE/'source.html').relative_to(R)),sha256=sha256(SOURCE/'source.html'),text_path=str((SOURCE/'source.txt').relative_to(R)),text_sha256=sha256(SOURCE/'source.txt'),retrieved_at=meta['fetched_at'],acquisition='Full official release notes; precise publication timestamp extracted from archived relative-time datetime attribute, not commit or crawl date. Repair claims publisher-reported; no runtime installed or tested.')
    facts=[]
    for n,(pred,obj,quote,claim) in enumerate(CLAIMS,1):
        a=body.index(quote);f=dict(id=f'F{n}',text=claim,subject=ENTITY,predicate=pred,object=obj,modality='reported',verification='source_supported',evidence=[dict(source_id='S1',quote=quote,start=a,end=a+len(quote))]);f['fingerprint']=fact_id(f);facts.append(f)
    out=C/'media/harness_states';out.mkdir(parents=True,exist_ok=True);prov=out/'original_provenance.json';assets=[];shots=[];clips=[]
    for i in range(4):
        path=out/f'S{i+1:03d}.mp4'
        if not path.exists():
            p=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','720x1280','-r','24','-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],stdin=subprocess.PIPE)
            try:
                for n in range(192):p.stdin.write(frame(i,n/24).tobytes())
            finally:p.stdin.close()
            assert p.wait()==0
        info=probe(path);v=next(x for x in info['streams'] if x['codec_type']=='video');assert (v['width'],v['height'])==(720,1280);assert abs(float(info['format']['duration'])-8)<.01;assert not any(x['codec_type']=='audio' for x in info['streams'])
        subprocess.run(['ffmpeg','-v','error','-i',str(path),'-threads','2','-f','null','-'],check=True)
        frames=[]
        for at in (.5,4,7.5):
            jpg=out/f'S{i+1:03d}_{at}.jpg';subprocess.run(['ffmpeg','-y','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1',str(jpg)],check=True);frames.append(dict(at=at,path=str(jpg.relative_to(R)),sha256=sha256(jpg)))
        assets.append(dict(id=f'A{i+1}',source_id='S1',url=URL,path=str(path.relative_to(R)),sha256=sha256(path),bytes=path.stat().st_size,kind='video',duration=8,use='production',rights=dict(status='original',owner='本素材任务原创机制图',evidence_path=str(prov.relative_to(R)),note='No original product UI, photographs, audio, logos or software recordings; supplemental schematic only.')))
        shots.append(dict(id=f'S{i+1:03d}',asset_id=f'A{i+1}',start=0,end=8,status='candidate',observation=None,keyframes=frames,boundary_method='Authored eight-second segment; full decode and 0.5/4/7.5s samples'));clips.append(path)
    write_json(prov,dict(renderer=str(Path(__file__).relative_to(ROOT)),renderer_sha256=sha256(Path(__file__)),helper='workflow/material_pipeline/build_cycle_20261003.py',helper_sha256=sha256(Path(__file__).with_name('build_cycle_20261003.py')),classification='Original explanatory supplements for official release; never real product interface or independent fix verification',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips],audio='silent'))
    concat=out/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in clips));preview=out/'preview.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(preview)],check=True);subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-f','null','-'],check=True)
    beats=[dict(id=f'B{i+1}',purpose=p[0],fact_ids=[f'F{n}' for n in p[2]],original_narration=p[3],visual_plan='原创状态与诊断补充示意；正式主场景须主管线另制，不能拿图形序列代替整片场景。') for i,p in enumerate(PANELS)]
    write_json(out/'narration_draft.json',dict(segments=[dict(start=i*8,end=(i+1)*8,text=p[3],fact_ids=beats[i]['fact_ids'],timing_status='draft, voice and alignment pending') for i,p in enumerate(PANELS)],audio='not synthesized'))
    package=dict(schema_version=1,content_id=cid,title=TITLE,event=EVENT,sources=[source],facts=facts,assets=assets,shots=shots,transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original schematics',limitations=['Narration timings are draft, not measured voice alignment.']),editorial=dict(audience='普通观众',angle=TITLE,beats=beats,cautions=['v0.2.1-alpha.1为预览版，不能写成稳定版。','时间是10月3日发布，不代表故障开始或每项修复实施时间。','所有修复是开发团队报告，本轮没有下载运行产品或复现故障。','四个原创片段是机制补充，不能当实机界面或修复成功记录。','版本替换仍需重启，不承诺所有插件热更新。'],verification_scope='Five exact facts in archived official release; no independent runtime test.'),collection_pending=[],production_pending=['主管线旁白、字幕、结构审核','正式人物动作场景与真实关键帧','自然配音、整板审核及粗剪预检'],production_asset_plan_reviewed=False)
    Draft202012Validator(json.loads((ROOT/'workflow/material_pipeline/package.schema.json').read_text(encoding="utf-8")),format_checker=FormatChecker()).validate(package);validate(package,R);write_json(C/'drafts'/f'{cid}.json',package)
    sheet=Image.new('RGB',(960,1280),'#07101c')
    for row,at in enumerate((.5,4,7.5)):
        for i in range(4):sheet.paste(Image.open(out/f'S{i+1:03d}_{at}.jpg').resize((240,426)),(i*240,row*426))
    sheet.save(C/'harness_decoded_review.jpg',quality=92)
    write_json(C/'draft_results.json',[dict(content_id=cid,title=TITLE,package=str(C/'drafts'/f'{cid}.json'),preview=str(preview),facts=5,new_clips=4,clip_seconds=32,schema='pass',decode='4 clips and preview pass',state='awaiting_visual_QA_then_enqueue')])
    print(json.dumps(dict(content_id=cid,contact_sheet=str(C/'harness_decoded_review.jpg'),state='awaiting_visual_QA'),ensure_ascii=False))

if __name__=='__main__':main()
