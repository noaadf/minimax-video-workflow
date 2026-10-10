"""Original optical-route and editable-timeline diagrams; draft before visual QA."""
import html,json,math,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
from .build_cycle_20261003 import font,text,node,dot
from .core import content_id,enqueue_candidate,fact_id,legacy_records,sha256,write_json,validate
from .media import probe
ROOT=Path(__file__).resolve().parents[2];R=ROOT/'research/material_pipeline';C=R/'monitor/runs/20261003T113235_327595'
W,H,FPS,D=720,1280,24,8
SPECS=[dict(key='laser',title='把紫外激光做成样机，难点在光路之外',entity='中国科学院理化所先进激光课题组紫外固体激光样机报告',date='2026-09-30',
 publisher='中国科学院理化技术研究所',url='https://ipc.cas.cn/xwzx/kyjz/202609/t20260930_8288954.html',date_evidence='发布时间：2026-09-30',
 acquisition='Dated official institute report archived in full; September2026 prototype work, September30 disclosure. Results institute-reported; no independent experiment.',
 action='研究所公开报告紫外固体激光样机研制进展',object='1064nm二倍频与和频路线关键技术突破样机及后续材料相互作用研究计划',
 facts=[('技术路线','1064nm倍频为532nm再和频为355nm','1064 nm Nd:YAG激光二倍频产生532nm绿激光再和频产生355\u2002nm紫外激光'),
 ('工程攻关','振荡放大变频与抗损伤薄膜四类关键技术','突破了热近非稳谐振腔激光振荡、高效率低波前畸变多级行波放大、高效紫外变频、高抗损伤紫外激光薄膜制备等关键技术'),
 ('阶段结果','研制出355nm紫外脉冲固体激光器样机','成功研制出355\u2002nm紫外脉冲固体激光器样机'),
 ('报告输出','样机平均功率312W','输出平均功率312W'),
 ('后续计划','研究紫外激光与材料相互作用并推进应用','后续将进一步开展紫外激光与相关材料相互作用研究，实现在镀膜、退火、精密加工等领域的应用。')],
 claims=['理化所报告：先进激光课题组采用1064nm倍频生成532nm、再和频生成355nm紫外光的路线。','理化所报告团队攻克振荡、低波前畸变放大、紫外变频和抗损伤薄膜等关键技术。','研究所称团队于2026年9月研制出355nm紫外脉冲固体激光器样机，9月30日公开报告。','研究所报告样机平均输出功率312W；未独立复测，不能写成峰值功率。','研究所计划继续研究激光与相关材料相互作用，并推进镀膜、退火、精密加工应用；不代表应用已落地。'],
 panels=[('做出紫外激光，还得跨过工程难点','理化所先进激光课题组 · 官方报告',[2],'紫外固体激光要走向应用，光路之外，还有放大、变频和薄膜等工程难点。理化所团队做了攻关。'),
 ('先倍频，再和频','1064nm + 532nm → 355nm',[1],'团队先把一千零六十四纳米的光倍频成绿光，再通过和频得到三百五十五纳米紫外光。'),
 ('目前做到样机阶段','研究所报告：平均功率312W',[3,4],'研究所报告，团队在九月做出紫外脉冲激光器样机，平均功率三百一十二瓦。这是样机结果。'),
 ('下一步，验证光与材料怎样作用','镀膜、退火、精密加工：后续方向',[5],'接下来，团队计划研究激光与材料的相互作用。镀膜和精密加工，是后续应用方向。')],
 cautions=['公开报告日为2026-09-30；研制时间仅确认2026年9月，不杜撰具体完成日。','所有技术进展及312W均为研究所报告；没有独立复测或产业量产证据。','平均功率不能当作峰值；不采用国际先进、全球首创、替代进口等未独立核对结论。','和频图表示1064nm与532nm合成355nm；是概念关系图，不是实际光学设计或实验设备。','应用属于后续计划，素材不声称已进入半导体产线。']),
 dict(key='studio',title='AI剪完的视频，人怎样接着改',entity='Manus团队Studio可编辑视频时间轴公告',date='2026-09-28',publisher='Manus产品团队',url='https://manus.im/zh-cn/blog/introducing-manus-2-0',date_evidence='Publication metadata createdAt: 2026-09-28',
 acquisition='Full original Chinese product announcement; article createdAt protobuf timestamp explicitly extracted into text, visible MondaySep28 matches. Article updatedOct1, media coverageSep30. Vendor capabilities, not independently tested.',
 action='公开介绍Studio视频编辑器的人与AI接续修改工作流',object='独立时间轴元素用户替换素材导出与交回AI基于手工编辑继续创作',
 facts=[('产品行动','桌面应用升级为Manus Studio','在 Manus 2.0 中,桌面应用升级为 Manus Studio:一个供人与 AI 共享的工作空间。'),
 ('编辑结构','片段图像文字动态图形音频成为独立元素','它使用的片段、图像、文字、动态图形和音频都以独立元素的形式呈现'),
 ('用户操作','换配乐插素材调整动画后导出','你可以更换配乐、插入自己的素材、调整动画位置,满意后即可导出。'),
 ('接续创作','交回AI基于用户编辑再修一版','你也可以把它交回给 Manus。让 Manus 再修一版,它会基于你的编辑继续创作。'),
 ('适用场景','30至60秒短产品广告等视频','视频编辑器适用于短产品广告(30 至 60 秒)、AI UGC、数据动态图形、教程、Vlog 和动效视频。')],
 claims=['Manus团队在公告中介绍桌面应用升级为供人与AI共享的Manus Studio。','公告称视频时间轴把片段、图像、文字、动态图形与音频作为独立元素。','公告称用户可更换配乐、插入自有素材、调整动画位置并导出。','公告称用户也可交回Manus，让AI基于手工编辑继续修改。','公告列出30至60秒短产品广告及教程、Vlog等适用场景；未实测效果或生成速度。'],
 panels=[('初剪完成，还想换一张图','Manus Studio · 厂商工作流披露',[1],'AI初剪完成，你还想换配乐、放自己的产品图。Manus提出了让人接着改的工作空间。'),
 ('把结果拆成可编辑的元素','片段、图像、文字、动态图形、音频',[2],'公告称，视频结果拆到时间轴上。画面、文字和声音成为独立元素，方便继续编辑。'),
 ('换素材，再决定如何交付','用户可替换、调整并导出',[3,5],'用户可以换配乐、插入自己的素材，再导出。公告列出的场景包括三十到六十秒短广告。'),
 ('手工修改，还能交回AI','基于用户编辑继续修一版',[4],'也可以把改好的版本交回AI，让它沿着你的修改继续做。实际体验和效果，仍待实测。')],
 cautions=['公告createdAt为2026-09-28，正文页面updatedAt为10月1日；量子位报道为9月30日。不能将媒体报道日写成唯一首发日。','本轮确认当前公告的功能披露；不证明每项功能在9月28日首版均已存在。','用户例子是产品方说明的假设场景，没有具体用户成功案例或独立实测。','时间轴和替换素材均为原创概念图，不是实际Manus界面、操作记录或生成结果。','不宣称大陆已可用、节省固定成本、几分钟必成片，或工作流已经接入本生产管线。'])]

def draw_frame(spec,i,t):
 im=Image.new('RGB',(W,H),'#0c1726');d=ImageDraw.Draw(im);accent='#bfabf5' if spec['key']=='laser' else '#9fd5be'
 d.text((50,52),f'0{i+1} / 04 · 来源报告 {spec["date"]}',font=font(22),fill=accent)
 text(d,spec['panels'][i][0],50,142,41);text(d,spec['panels'][i][1],50,295,24,'#a5b4c8')
 if spec['key']=='laser':
  if i==0:
   for j,label in enumerate(['激光振荡','低畸变放大','紫外变频','抗损伤薄膜']):
    x=66+(j%2)*306;y=450+(j//2)*235;node(d,(x,y,x+280,y+125),label,accent,27)
   path=[(207,575),(207,645),(512,645),(512,810)];d.line(path,fill='#7d6a99',width=4);dot(d,path,t,accent)
   text(d,'四类工程攻关 · 并非只列功率参数',55,990,26,'#a5b4c8')
  elif i==1:
   node(d,(65,430,320,546),'1064nm', '#d79292',32)
   node(d,(390,430,655,546),'倍频：532nm','#8bd0ac',27)
   node(d,(195,830,525,960),'和频：355nm',accent,29)
   paths=[([(320,489),(390,489)],'#8bd0ac'), ([(193,546),(193,720),(290,830)],'#d79292'), ([(523,546),(523,720),(430,830)],'#8bd0ac')]
   for path,color in paths:d.line(path,fill=color,width=4);dot(d,path,t,color)
   text(d,'红外与绿光参与和频 · 非实际光学设计',50,1040,25,'#a5b4c8')
  elif i==2:
   node(d,(85,435,635,558),'355nm紫外脉冲固体激光样机',accent,25)
   text(d,'平均功率',240,675,30,'#a5b4c8');text(d,'312 W',175,750,76,accent)
   path=[(110,965),(610,965)];d.line(path,fill='#6b5a88',width=3);dot(d,path,t,accent)
   text(d,'来源：研究所报告 · 未独立复测',70,1050,25,'#a5b4c8')
  else:
   node(d,(110,428,610,549),'下一步：光与材料相互作用',accent,26)
   for j,label in enumerate(['镀膜','退火','精密加工']):
    y=665+j*132;node(d,(215,y,505,y+84),label,accent,28)
   path=[(100,549),(100,1024)];d.line(path,fill='#6b5a88',width=3);dot(d,path,t,accent)
   text(d,'后续计划 · 尚非产线应用证据',75,1090,25,'#a5b4c8')
 else:
  if i==0:
   node(d,(80,442,640,564),'AI初剪',accent,32);node(d,(80,822,640,944),'用户还想换图、换配乐',accent,29)
   path=[(360,564),(360,822)];d.line(path,fill='#578777',width=4);dot(d,path,t,accent)
  elif i in (1,2):
   labels=['视频','图像','文字','动效','音频']
   for j,label in enumerate(labels):
    y=440+j*102;text(d,label,55,y+13,25,'#a5b4c8')
    for k in range(3):
     x=150+k*168;d.rounded_rectangle((x,y,x+146,y+64),radius=8,fill='#295043' if j!=1 else '#72643f')
    if i==2 and j==1:
     x=150+min(t/4,1)*168;d.rounded_rectangle((x,y-5,x+146,y+69),radius=8,outline='#edcd8e',width=4)
   d.line((150+t/8*480,425,150+t/8*480,934),fill=accent,width=3)
   text(d,'独立元素' if i==1 else '示意：替换自己的图像 → 导出',80,1040,29,accent)
  else:
   node(d,(80,433,640,566),'用户修改后的版本',accent,31);node(d,(80,825,640,958),'AI基于修改继续创作',accent,29)
   path=[(360,566),(360,825)];d.line(path,fill='#578777',width=4);dot(d,path,t,accent)
   text(d,'厂商披露 · 功能体验与效果待实测',65,1060,25,'#a5b4c8')
 d.line((50,1160,670,1160),fill='#2d4159',width=2);text(d,'原创概念示意 · 静音素材 · 待主管线审核',50,1202,22,'#a6bacd')
 return im

def main():
 from jsonschema import Draft202012Validator,FormatChecker
 validator=Draft202012Validator(json.loads((ROOT/'workflow/material_pipeline/package.schema.json').read_text(encoding="utf-8")),format_checker=FormatChecker())
 old=legacy_records(list((ROOT/'research/deadline30_20261002').glob('catalog_?.json'))+list((ROOT/'research').glob('scale*/catalog.json')))
 supplemental=json.loads((C/'supplementary_results.json').read_text(encoding="utf-8"));folders={x['url']:(Path(x['folder']) if 'folder' in x else Path(x['archive']).parent) for x in supplemental if x['status']=='archived'}
 results=[]
 for spec in SPECS:
  folder=folders[spec['url']];meta=json.loads((folder/'source.json').read_text(encoding="utf-8"));body=(folder/'source.txt').read_text(encoding="utf-8")
  source=dict(id='S1',url=spec['url'],title=meta['title'],publisher=spec['publisher'],role='primary',published=spec['date'],date_evidence=spec['date_evidence'],archive_path=str((folder/'source.html').relative_to(R)),sha256=sha256(folder/'source.html'),text_path=str((folder/'source.txt').relative_to(R)),text_sha256=sha256(folder/'source.txt'),retrieved_at=meta['fetched_at'],acquisition=spec['acquisition'])
  facts=[]
  for n,((predicate,obj,quote),claim) in enumerate(zip(spec['facts'],spec['claims']),1):
   start=body.index(quote);f=dict(id=f'F{n}',text=claim,subject=spec['entity'],predicate=predicate,object=obj,modality=('planned' if spec['key']=='laser' and n==5 else 'reported'),verification='source_supported',evidence=[dict(source_id='S1',quote=quote,start=start,end=start+len(quote))]);f['fingerprint']=fact_id(f);facts.append(f)
  event=dict(entity=spec['entity'],action=spec['action'],date=spec['date'],object=spec['object']);cid=content_id(event)
  out=C/'media'/spec['key'];out.mkdir(parents=True,exist_ok=True);provenance=out/'original_provenance.json';assets=[];shots=[];beats=[];clips=[];narration=[]
  for i,panel in enumerate(spec['panels']):
   path=out/f'S{i+1:03d}.mp4'
   if not path.exists():
    proc=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],stdin=subprocess.PIPE)
    try:
     for n in range(FPS*D):proc.stdin.write(draw_frame(spec,i,n/FPS).tobytes())
    finally:proc.stdin.close()
    if proc.wait():raise RuntimeError('encode failure')
   info=probe(path);v=next(x for x in info['streams'] if x['codec_type']=='video');duration=float(info['format']['duration'])
   assert (v['width'],v['height'])==(W,H) and abs(duration-D)<.01
   assert not any(x['codec_type']=='audio' for x in info['streams'])
   subprocess.run(['ffmpeg','-v','error','-i',str(path),'-threads','2','-f','null','-'],check=True)
   frames=[]
   for at in (.5,4,7.5):
    jpg=out/f'S{i+1:03d}_{at}.jpg';subprocess.run(['ffmpeg','-y','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1',str(jpg)],check=True)
    frames.append(dict(at=at,path=str(jpg.relative_to(R)),sha256=sha256(jpg)))
   clips.append(path);assets.append(dict(id=f'A{i+1}',source_id='S1',url=spec['url'],path=str(path.relative_to(R)),sha256=sha256(path),bytes=path.stat().st_size,kind='video',duration=duration,use='production',rights=dict(status='original',owner='本素材任务原创示意图',evidence_path=str(provenance.relative_to(R)),note='Original deterministic diagrams; no source photos/audio/data. Source URL supplies factual basis, not media download.')))
   shots.append(dict(id=f'S{i+1:03d}',asset_id=f'A{i+1}',start=0,end=D,status='candidate',observation=None,keyframes=frames,boundary_method='Authored eight-second segment; decoded 0.5/4/7.5s samples'))
   beats.append(dict(id=f'B{i+1}',purpose=panel[0],fact_ids=[f'F{n}' for n in panel[2]],original_narration=panel[3],visual_plan='对应概念示意；公告层面表现，正式架构、叙事和结果字幕须主管线审核。'))
   narration.append(dict(start=i*D,end=(i+1)*D,text=panel[3],fact_ids=beats[-1]['fact_ids'],timing_status='draft suggested cut, no synthesized or aligned voice'))
  write_json(provenance,dict(renderer=str(Path(__file__).relative_to(ROOT)),renderer_sha256=sha256(Path(__file__)),helper='workflow/material_pipeline/build_cycle_20261003.py',helper_sha256=sha256(Path(__file__).with_name('build_cycle_20261003.py')),classification='Original conceptual motion: optical frequency relations and video timeline workflow; no real apparatus, experiment, product UI or generated-video output',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips],audio='silent'))
  concat=out/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in clips));preview=out/'preview.mp4'
  if not preview.exists():subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(preview)],check=True)
  subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-threads','2','-f','null','-'],check=True)
  write_json(out/'narration_draft.json',dict(content_id=cid,segments=narration,audio='not synthesized',review='Main pipeline narration and timing review required'))
  package=dict(schema_version=1,content_id=cid,title=spec['title'],event=event,sources=[source],facts=facts,assets=assets,shots=shots,transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original diagrams',limitations=['Suggested narration timings are not voice alignment.']),editorial=dict(audience='普通观众',angle=spec['title'],beats=beats,cautions=spec['cautions'],verification_scope='Exact support in archived primary reports; laser result reported and applications planned; Studio capabilities vendor-described, no independent operation validation.'),collection_pending=[],production_pending=['主管线旁白、叙事、结构、示意图与数值字幕审核','正式配音、字幕和节奏对齐'],production_asset_plan_reviewed=False)
  validator.validate(package);validate(package,R);draft=C/'drafts'/f'{cid}.json';write_json(draft,package)
  result=dict(content_id=cid,title=spec['title'],package=str(draft),preview=str(preview),facts=len(facts),new_clips=4,clip_seconds=32,schema='pass',decode='4 clips and preview pass',state='awaiting_visual_QA_then_enqueue');results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
  sheet=Image.new('RGB',(960,1280),'#07101c')
  for row,at in enumerate((.5,4,7.5)):
   for i in range(4):sheet.paste(Image.open(out/f'S{i+1:03d}_{at}.jpg').resize((240,426)),(i*240,row*426))
  sheet.save(C/f'{spec["key"]}_decoded_review.jpg',quality=92)
 write_json(C/'draft_results.json',results)
 cards=[]
 for result in results:
  spec=next(x for x in SPECS if x['title']==result['title']);cards.append('<article><h2>'+html.escape(result['title'])+'</h2><video controls preload="metadata" src="'+str(Path(result['preview']).relative_to(C))+'"></video><p>'+html.escape(' '.join(x[3] for x in spec['panels']))+'</p><p>'+html.escape(' '.join(spec['cautions']))+'</p></article>')
 (C/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>本轮科研与创作素材预览</title><style>body{font:17px system-ui;background:#101b2a;color:#e7eef7;max-width:1100px;margin:40px auto;padding:20px}main{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{background:#1d3048;padding:22px;border-radius:15px}video{width:100%;max-height:600px}</style><h1>本轮新增科研与创作素材</h1><p>8段原创静音示意，64秒。发布方报告已核对，样机结果与应用计划分开记录，Studio功能未独立实测；未完成配音、字幕及生产审批。</p><main>'+''.join(cards)+'</main>', encoding="utf-8")
if __name__=='__main__':main()
