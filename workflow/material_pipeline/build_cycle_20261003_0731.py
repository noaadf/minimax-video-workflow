"""One new dated PCS release story, with original silent conceptual motion."""
import html,json,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
from .build_cycle_20261003 import font,text,node,dot
from .core import content_id,enqueue_candidate,fact_id,legacy_records,sha256,write_json
from .media import probe

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'research/material_pipeline'
C=R/'monitor/runs/20261003T073133_142849'
W,H,FPS,D=720,1280,24,8
URL='https://api.github.com/repos/openJiuwen-ai/jiuwenswarm/releases/tags/v0.2.7'
TITLE='AI开工前，先把上下文准备好'
ENTITY='openJiuwen WorkSwarm PCS'
FACTS=[
 ('版本发布','v0.2.7引入PCS','This version focuses on introducing **Proactive Context Service (PCS) and Cross-Agent Sessions**','官方v0.2.7发布说明列出PCS上下文服务。'),
 ('授权同步','用户授权后增量同步文件文档笔记平台会话','with user authorization, incrementally synchronize local files, project documents, notes, collaboration platforms, and historical sessions','发布说明称，用户授权后可增量同步本地文件、项目文档、笔记、协作平台和历史会话。'),
 ('知识库','按主题整理且可搜索追溯审计','organizing them into a context knowledge base with clear themes that is searchable, traceable, and auditable','发布说明称，资料整理为主题明确、可搜索、可追溯、可审计的上下文知识库。'),
 ('证据核验','先结构化概览再按需核对原文','at task start, first provide a structured context overview, then verify original text on demand.','发布说明称，任务开始先给出结构化上下文概览，再按需核对原文。'),
 ('故障隔离','一个来源或模型失败不影响其他来源','Failure of a single source or model does not affect other sources.','发布说明称，一个来源或模型失败不影响其他来源；本轮未运行验证。')]
PANELS=[
 ('每次任务，都临时找资料？','WorkSwarm v0.2.7加入PCS',[1],'每次开始任务，都得临时找资料。WorkSwarm的新版本，加入了PCS上下文服务。'),
 ('授权后，增量同步','文件、文档和历史会话 → 上下文',[2,3],'按发布说明，它在用户授权后，增量同步文件、文档和历史会话。'),
 ('先看概览，再核对原文','主题知识库 → 概览 → 原文',[3,4],'资料按主题整理。任务开始先看概览，需要证据时再核对原文。'),
 ('单个来源出错，其他继续','发布说明所述机制 · 尚未实测',[5],'一个来源或模型出错，其他来源仍可继续。这是发布说明，具体效果待实测。')]
CAUTIONS=['2026-09-30为v0.2.7正式发布记录日期，不是运行测试或产品部署日期。','所有功能事实是社区发布说明所述行为，未安装或运行PCS，未验证性能、故障隔离和权限实现。','全部画面为原创概念示意；不是实际软件界面、访问授权流程或真实用户文件。','旁白、结构、字幕、配音及连续画面审核交主管线；素材包不代表生产审批。']

def draw_frame(i,t):
 im=Image.new('RGB',(W,H),'#0b1727');d=ImageDraw.Draw(im);accent='#8dd9c6'
 d.text((50,52),f'0{i+1} / 04 · 发布 2026-09-30',font=font(23),fill=accent)
 text(d,PANELS[i][0],50,142,43);text(d,PANELS[i][1],50,288,24,'#9db1c8')
 if i==0:
  for j,label in enumerate(('项目文件','工作笔记','历史会话')):
   x=50+j*212;y=448+int((t%2)*4)
   d.rounded_rectangle((x,y,x+193,y+222),radius=12,fill='#1d354a',outline='#4e7188',width=2)
   text(d,label,x+16,y+25,26,accent)
   for n in range(4):d.line((x+20,y+85+n*26,x+155-n*9,y+85+n*26),fill='#6d8496',width=4)
  node(d,(145,830,575,938),'任务开始：准备上下文',accent,28)
  for j in range(3):
   path=[(146+j*212,680),(360,830)];d.line(path,fill='#4e7188',width=3);dot(d,path,t,accent)
  text(d,'PCS针对的准备过程 · 概念示意',50,1045,25,'#9db1c8')
 elif i==1:
  for j,label in enumerate(('文件','文档','会话')):
   y=423+j*154;node(d,(48,y,227,y+85),label,accent,29)
   path=[(227,y+43),(325,660)];d.line(path,fill='#4e7188',width=3);dot(d,path,t,accent)
  node(d,(257,610,463,712),'用户授权', '#edca81',27)
  node(d,(478,610,674,712),'主题知识库',accent,25)
  d.line((463,660,478,660),fill=accent,width=4)
  node(d,(180,891,540,988),'新增内容逐步积累',accent,28)
  d.line((575,712,575,839,360,891),fill='#4e7188',width=3)
  d.rounded_rectangle((230,1008,230+min(t/D,1)*260,1020),radius=5,fill=accent)
  text(d,'此图不代表已访问任何用户资料',50,1081,24,'#9db1c8')
 elif i==2:
  labels=['按主题组织资料','先提供上下文概览','需要时核对原文']
  for j,label in enumerate(labels):
   y=430+j*220;node(d,(108,y,612,y+112),label,accent if j<2 else '#edca81',30)
   if j<2:
    path=[(360,y+112),(360,y+220)];d.line(path,fill='#4e7188',width=4);dot(d,path,t,accent)
  text(d,'概览用于导航，事实回到原文核对',50,1081,25,'#9db1c8')
 else:
  for j,(label,color) in enumerate([('来源A · 继续',accent),('来源B · 出错','#ef8a8a'),('来源C · 继续',accent)]):
   y=428+j*166;node(d,(50,y,345,y+103),label,color,28)
   path=[(345,y+51),(514,875)];d.line(path,fill='#4e7188' if j!=1 else '#664454',width=3)
   if j!=1:dot(d,path,t,accent)
   else:
    d.line((378,y+35,406,y+63),fill=color,width=5);d.line((378,y+63,406,y+35),fill=color,width=5)
  node(d,(358,873,675,986),'其他来源仍可用',accent,27)
  text(d,'官方所述功能 · 具体效果待实测',50,1081,25,'#9db1c8')
 d.line((50,1160,670,1160),fill='#2d4159',width=2)
 text(d,'原创概念示意 · 发布说明 · 静音素材',50,1202,22,'#a6bacd')
 return im

def main():
 from jsonschema import Draft202012Validator,FormatChecker
 validator=Draft202012Validator(json.loads((ROOT/'workflow/material_pipeline/package.schema.json').read_text(encoding="utf-8")),format_checker=FormatChecker())
 old=legacy_records(list((ROOT/'research/deadline30_20261002').glob('catalog_?.json'))+list((ROOT/'research').glob('scale*/catalog.json')))
 row=next(x for x in json.loads((C/'supplementary_results.json').read_text(encoding="utf-8")) if x['url']==URL)
 folder=Path(row['folder']);body=(folder/'source.txt').read_text(encoding="utf-8")
 source=dict(id='S1',url=URL,title=row['title'],publisher='openJiuwen社区',role='primary',published=row['published'],date_evidence='Release published_at: 2026-09-30T10:00:36Z',archive_path=str((folder/'source.json.raw').relative_to(R)),sha256=sha256(folder/'source.json.raw'),text_path=str((folder/'source.txt').relative_to(R)),text_sha256=sha256(folder/'source.txt'),retrieved_at=row['fetched_at'],acquisition='Public GitHub release API, raw JSON and complete release body archived. HTML origin: '+row['original_article_url']+'. Publisher description, no independent runtime validation.')
 facts=[]
 for n,(predicate,obj,quote,claim) in enumerate(FACTS,1):
  start=body.index(quote);f=dict(id=f'F{n}',text=claim,subject=ENTITY,predicate=predicate,object=obj,modality='reported',verification='source_supported',evidence=[dict(source_id='S1',quote=quote,start=start,end=start+len(quote))]);f['fingerprint']=fact_id(f);facts.append(f)
 event=dict(entity=ENTITY,action='发布v0.2.7包含PCS上下文服务',date='2026-09-30',object='授权增量同步主题知识库原文核验和来源故障隔离的发布说明');cid=content_id(event)
 out=C/'media/pcs';out.mkdir(parents=True,exist_ok=True);provenance=out/'original_provenance.json'
 assets=[];shots=[];beats=[];clips=[];narration=[]
 for i,panel in enumerate(PANELS):
  path=out/f'S{i+1:03d}.mp4'
  if not path.exists():
   proc=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],stdin=subprocess.PIPE)
   try:
    for n in range(FPS*D):proc.stdin.write(draw_frame(i,n/FPS).tobytes())
   finally:proc.stdin.close()
   if proc.wait():raise RuntimeError('encode failure')
  info=probe(path);v=next(x for x in info['streams'] if x['codec_type']=='video');duration=float(info['format']['duration'])
  assert (v['width'],v['height'])==(W,H) and v['avg_frame_rate']=='24/1' and abs(duration-D)<.01
  assert not any(x['codec_type']=='audio' for x in info['streams'])
  subprocess.run(['ffmpeg','-v','error','-i',str(path),'-threads','2','-f','null','-'],check=True)
  frames=[]
  for at in (.5,4,7.5):
   jpg=out/f'S{i+1:03d}_{at}.jpg';subprocess.run(['ffmpeg','-y','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1',str(jpg)],check=True)
   frames.append(dict(at=at,path=str(jpg.relative_to(R)),sha256=sha256(jpg)))
  clips.append(path);assets.append(dict(id=f'A{i+1}',source_id='S1',url=URL,path=str(path.relative_to(R)),sha256=sha256(path),bytes=path.stat().st_size,kind='video',duration=duration,use='production',rights=dict(status='original',owner='本素材任务原创示意图',evidence_path=str(provenance.relative_to(R)),note='Original diagrams without source photos, audio or user data. Source URL is factual basis, not media download.')))
  shots.append(dict(id=f'S{i+1:03d}',asset_id=f'A{i+1}',start=0,end=D,status='candidate',observation=None,keyframes=frames,boundary_method='Authored eight-second segment; decoded 0.5/4/7.5s samples'))
  beats.append(dict(id=f'B{i+1}',purpose=panel[0],fact_ids=[f'F{n}' for n in panel[2]],original_narration=panel[3],visual_plan='对应原创概念示意；发布方所述行为，不是实测界面。叙事与结构待主管线审核。'))
  narration.append(dict(start=i*D,end=(i+1)*D,text=panel[3],fact_ids=beats[-1]['fact_ids'],timing_status='Draft suggested cut, no synthesized or aligned voice'))
 write_json(provenance,dict(renderer=str(Path(__file__).relative_to(ROOT)),renderer_sha256=sha256(Path(__file__)),helper='workflow/material_pipeline/build_cycle_20261003.py',helper_sha256=sha256(Path(__file__).with_name('build_cycle_20261003.py')),classification='Original conceptual motion; no actual PCS execution, private data or software UI',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips],audio='silent'))
 concat=out/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in clips));preview=out/'preview.mp4'
 if not preview.exists():subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(preview)],check=True)
 subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-threads','2','-f','null','-'],check=True)
 write_json(out/'narration_draft.json',dict(content_id=cid,segments=narration,audio='not synthesized',review='Main pipeline narration and timing review required'))
 package=dict(schema_version=1,content_id=cid,title=TITLE,event=event,sources=[source],facts=facts,assets=assets,shots=shots,transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original diagrams',limitations=['Suggested narration timings are not voice alignment.']),editorial=dict(audience='普通观众',angle='从任务开始临时找资料，到授权后持续积累上下文，再回原文核验。',beats=beats,cautions=CAUTIONS,verification_scope='Exact support in official dated v0.2.7 release notes; functionality is publisher-reported and not independently executed.'),collection_pending=[],production_pending=['主管线旁白、叙事、结构和示意画面审核','正式配音、字幕及节奏对齐','若改为已验证功能叙事，需独立运行核验'],production_asset_plan_reviewed=False)
 validator.validate(package);receipt=enqueue_candidate(package,R,old)
 result=dict(content_id=cid,title=TITLE,package=str(R/'inbox'/cid/'package.json'),preview=str(preview),facts=len(facts),new_clips=4,clip_seconds=32,schema='pass',decode='4 clips and preview pass',readiness=receipt['readiness'],dedup=receipt['dedup'])
 write_json(C/'intake_results.json',[result]);print(json.dumps(result,ensure_ascii=False),flush=True)
 sheet=Image.new('RGB',(960,1280),'#07101c')
 for row,at in enumerate((.5,4,7.5)):
  for i in range(4):sheet.paste(Image.open(out/f'S{i+1:03d}_{at}.jpg').resize((240,426)),(i*240,row*426))
 sheet.save(C/'pcs_decoded_review.jpg',quality=92)
 (C/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>PCS素材预览</title><style>body{font:17px system-ui;background:#101b2a;color:#e7eef7;max-width:900px;margin:40px auto;padding:20px}article{background:#1d3048;padding:22px;border-radius:15px}video{width:100%;max-height:650px}</style><h1>'+html.escape(TITLE)+'</h1><p>4段原创静音素材，共32秒。来源为9月30日官方发布说明；功能尚未运行验证，旁白与结构未经主管线审核。</p><article><video controls preload="metadata" src="media/pcs/preview.mp4"></video><p>'+html.escape(' '.join(p[3] for p in PANELS))+'</p><p>'+html.escape(' '.join(CAUTIONS))+'</p></article>', encoding="utf-8")

if __name__=='__main__':main()
