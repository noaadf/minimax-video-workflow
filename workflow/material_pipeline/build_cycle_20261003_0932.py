"""This cycle: two publisher-reported AI application stories, original diagrams."""
import html,json,math,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
from .build_cycle_20261003 import font,text,node,dot
from .core import content_id,enqueue_candidate,fact_id,legacy_records,sha256,write_json
from .media import probe
ROOT=Path(__file__).resolve().parents[2];R=ROOT/'research/material_pipeline';C=R/'monitor/runs/20261003T093203_971855'
W,H,FPS,D=720,1280,24,8
SPECS=[
 dict(key='pet',title='把宠物照片带进手机，模型怎样跨过端云两边',entity='魔芯科技摸小宠MoWorld端云迁移案例',date='2026-10-02',
 publisher='昇腾CANN社区账号',url='https://cann.csdn.net/6abea225ad85947e68cd76ad.html',date_evidence='2026-10-02 02:10:38 发布',
 acquisition='Public CANN developer community case, posted by 昇腾CANN; full original HTML and body archived. Vendor report, no independent product test.',
 action='昇腾CANN公开报告摸小宠端云迁移案例',object='MoWorld模型量化裁剪麒麟部署端云生成分工及Vector算子代码复用技术披露',
 facts=[('研发迁移','昇腾训练量化裁剪后麒麟部署','其MoWorld-2.0模型在昇腾上完成训练，经量化与裁剪形成端侧版本，再通过算子代码的沿用与针对性适配，完成麒麟端侧部署。'),
 ('输入与目标','一至四张照片还原4D数字分身','实现了通过用户的1-4张照片就能在云端还原出拟真的用户宠物的4D数字分身。'),
 ('端侧模型结构','约0.6B保留核心计算结构','通过量化与裁剪，形成约0.6B的端侧版本模型，其核心计算模块及相关算子结构与云侧保持一致'),
 ('端云分工','麒麟flash推理昇腾扩散生成手机渲染','摸小宠基于用户输入的宠物图片，在麒麟端侧运行约6亿参数规模的MoWorld-2.0-flash模型；云侧昇腾平台上的扩散模型进一步参与生成，构建出更高质量的3D高斯模型，再回到手机端完成渲染与呈现。'),
 ('代码复用范围','此次Vector类算子约85%代码原样保留','此次迁移涉及的Vector类算子中，约85%的代码可原样保留，仅Tile等硬件强相关部分需要面向端侧调整。'),
 ('开发团队','魔芯科技','这款由魔芯科技开发的宠物3D重建应用')],
 claims=['昇腾CANN报告：MoWorld-2.0在昇腾训练，量化裁剪后，经算子代码沿用和适配部署到麒麟。','账号报告应用以1-4张宠物照片生成4D数字分身，未独立验证还原质量。','账号报告端侧模型约0.6B，保留云侧核心计算模块和相关算子结构。','账号说明麒麟运行flash模型，昇腾云侧扩散模型继续生成3D高斯模型，手机最终渲染。','账号报告此次迁移的Vector类算子约85%代码原样保留，Tile等硬件相关部分需调整；不是全部模型代码的85%。','公开案例将摸小宠的开发者标为魔芯科技。'],
 panels=[('几张照片，变成可互动形象','魔芯科技摸小宠 · 发布方案例',[2,6],'想把宠物照片变成可互动形象，模型还得从云上走进手机。魔芯科技做了这次迁移。'),
 ('云上训练，端侧适配','昇腾训练 → 量化裁剪 → 麒麟',[1,3],'据昇腾CANN介绍，模型在昇腾上训练，经量化和裁剪，形成手机端版本。'),
 ('手机与云，各做一部分','端侧推理 → 云侧生成 → 手机渲染',[4],'手机先做部分推理，云侧继续生成三维模型，再回到手机渲染。并非全部离线完成。'),
 ('复用代码，也要适配硬件','此次Vector类算子约85%代码保留',[5],'案例称，这次Vector类算子约八成五的代码可保留，硬件相关部分仍要调整。效果待实测。')],
 cautions=['2026-10-02是技术案例披露日期；实际训练、部署及Mate90发布会日期未确认，不把它们写成10月2日完成。','照片、宠物图案和三维点阵为原创概念图，不是摸小宠真实输出或应用截图。','全部迁移、规模和代码复用事实为昇腾CANN账号所述；85%仅指此次涉及的Vector类算子代码。','不声称全部离线、不用云、不用适配、不需联网，或固定比例的实际成本下降。']),
 dict(key='audit',title='用AI查漏洞，先把实验关进沙盒',entity='Anthropic研究团队GLM5.3隔离浏览器实验报告',date='2026-09-29',
 publisher='Anthropic研究团队',url='https://www.anthropic.com/research/glm-5-3-and-the-spread-of-advanced-cyber-capabilities',date_evidence='Publication metadata article:published_time: 2026-09-29T15:46:00.000Z',
 acquisition='Public original research report with visible Sep29 date and matching HTML publication metadata. Metadata timestamp extracted explicitly into archived text; no experiment execution.',
 action='公开报告国产模型GLM5.3参与隔离浏览器漏洞研究',object='离线测试环境研究者辅助发现未知浏览器漏洞及向维护者披露进展',
 facts=[('实验边界','隔离沙盒中的离线目标','we ran the tested models in isolated and sandboxed environments so they can only attack offline targets'),
 ('研究行动','研究者在本地Linux浏览器中使用GLM5.3','a researcher used GLM-5.3 on a sandboxed machine with a local Linux build of a popular web browser.'),
 ('团队报告发现','浏览器JavaScript引擎此前未知漏洞','GLM-5.3 found several previously unknown vulnerabilities in the browser’s JavaScript engine'),
 ('披露进展','已向浏览器维护者披露','(We’ve disclosed these vulnerabilities to the maintainer.)')],
 claims=['Anthropic研究报告称，测试模型置于隔离沙盒，目标仅为研究者设置的离线对象。','报告称，一名研究者在沙盒机器的本地Linux浏览器构建中使用GLM-5.3。','报告称，GLM-5.3发现该浏览器JavaScript引擎此前未知的多个漏洞；未独立复现。','报告称，已向维护者披露这些浏览器漏洞；并未证明维护者已经修复。'],
 panels=[('测试先划清边界','隔离沙盒 · 研究者设置的离线目标',[1],'国产模型参与漏洞研究，先把边界说清：这份报告中的实验，针对隔离环境里的离线目标。'),
 ('研究者与模型共同排查','本地Linux浏览器 · GLM-5.3',[2],'研究者在本地浏览器构建中使用GLM，不是让它任意访问真实用户的设备。'),
 ('发现还要核验和披露','团队称发现此前未知的引擎漏洞',[3,4],'报告称模型找到了此前未知的浏览器漏洞，团队已把这些问题披露给维护者。'),
 ('披露，不等于已经修复','来源：研究团队报告 · 未独立复现',[4],'到这里，能确认的是团队报告了发现与披露，不能把它写成漏洞已经修好。')],
 cautions=['2026-09-29为研究报告发布日期，实验执行日期未公布。','发现与披露均为Anthropic团队报告；不宣称已复现、已修复或可泛化至其他平台。','本素材只表现隔离研究和维护者披露流程，不提供漏洞利用代码、绕过办法、敏感路径或可操作攻击细节。','所有目标、流程及报告标记为原创概念示意，不是原始实验画面或真实用户数据。'])]

def pet_icon(d,x,y,scale=1,color='#edd7bd',wire=False):
 box=(x-52*scale,y-45*scale,x+52*scale,y+45*scale)
 d.ellipse(box,outline=color,width=3)
 d.line([(x-45*scale,y-20*scale),(x-51*scale,y-75*scale),(x-12*scale,y-39*scale)],fill=color,width=3)
 d.line([(x+45*scale,y-20*scale),(x+51*scale,y-75*scale),(x+12*scale,y-39*scale)],fill=color,width=3)
 if wire:
  for k in (-.6,-.3,0,.3,.6):
   d.line((x-44*scale,y+k*45*scale,x+44*scale,y+k*45*scale),fill=color,width=1)
   d.line((x+k*50*scale,y-37*scale,x+k*50*scale,y+37*scale),fill=color,width=1)
 else:
  for a in (-20,20):d.ellipse((x+(a-3)*scale,y-9*scale,x+(a+3)*scale,y-3*scale),fill=color)
  d.line((x-6*scale,y+10*scale,x,y+16*scale,x+6*scale,y+10*scale),fill=color,width=3)

def draw_frame(spec,i,t):
 im=Image.new('RGB',(W,H),'#0b1727');d=ImageDraw.Draw(im);accent='#edc398' if spec['key']=='pet' else '#94c3f0'
 d.text((50,52),f'0{i+1} / 04 · 报告 {spec["date"]}',font=font(23),fill=accent)
 text(d,spec['panels'][i][0],50,142,43);text(d,spec['panels'][i][1],50,288,24,'#9db1c8')
 if spec['key']=='pet':
  if i==0:
   for j in range(3):
    x=60+j*209;d.rounded_rectangle((x,439,x+181,665),radius=12,fill='#22334a',outline=accent,width=2)
    pet_icon(d,x+91,557,.85,color=accent)
   node(d,(125,860,595,968),'照片输入 → 数字分身',accent,28)
   d.line((360,665,360,860),fill='#657183',width=4);dot(d,[(360,665),(360,860)],t,accent)
   text(d,'原创宠物图案 · 并非真实重建结果',50,1060,24,'#9db1c8')
  elif i==1:
   for j,label in enumerate(('昇腾：模型训练','量化、裁剪','麒麟：端侧部署')):
    y=427+j*211;node(d,(115,y,605,y+119),label,accent,31)
    if j<2:
     path=[(360,y+119),(360,y+211)];d.line(path,fill='#657183',width=4);dot(d,path,t,accent)
   text(d,'保留核心计算结构，仍需针对性适配',50,1080,24,'#9db1c8')
  elif i==2:
   labels=['麒麟端：flash模型推理','昇腾云：扩散模型继续生成','手机端：三维模型渲染']
   for j,label in enumerate(labels):
    y=420+j*191;node(d,(92,y,628,y+108),label,accent,28)
    if j<2:
     path=[(360,y+108),(360,y+191)];d.line(path,fill='#657183',width=4);dot(d,path,t,accent)
   pet_icon(d,360+math.sin(t)*8,1065,.65,color=accent,wire=True)
  else:
   text(d,'约85%',160,440,78,accent)
   text(d,'此次涉及的Vector类算子代码',65,555,29)
   for j in range(20):
    x=65+(j%10)*59;y=647+(j//10)*67;d.rounded_rectangle((x,y,x+45,y+44),radius=6,fill=accent if j<17 else '#597082')
   node(d,(93,882,627,993),'Tile等硬件相关部分：调整', '#9bd7dc',27)
   text(d,'不是全部模型代码；复用比例为发布方报告',50,1080,23,'#9db1c8')
 else:
  if i==0:
   d.rounded_rectangle((56,437,664,951),radius=23,outline=accent,width=4)
   text(d,'隔离研究环境',85,462,30,accent)
   node(d,(129,596,591,723),'研究者设置的离线目标',accent,29)
   text(d,'范围由实验定义',185,806,28)
   dot(d,[(129,760),(591,760)],t,accent)
  elif i==1:
   node(d,(58,435,319,551),'研究者',accent,30);node(d,(401,435,663,551),'GLM-5.3',accent,30)
   node(d,(90,790,630,947),'本地Linux浏览器构建',accent,31)
   for path in [[(187,551),(270,790)],[(531,551),(455,790)]]:d.line(path,fill='#627b91',width=4);dot(d,path,t,accent)
   text(d,'原创关系图 · 无真实浏览器或攻击过程',50,1070,24,'#9db1c8')
  elif i==2:
   node(d,(89,431,631,568),'团队报告：发现未知漏洞',accent,28)
   for j in range(3):
    x=93+j*181;d.rounded_rectangle((x,650,x+150,768),radius=10,fill='#23405b');text(d,'待核验',x+23,690,26,accent)
   node(d,(135,914,585,1031),'披露给维护者', '#e5c88b',31)
   path=[(360,768),(360,914)];d.line(path,fill='#627b91',width=4);dot(d,path,t,accent)
  else:
   node(d,(91,446,629,572),'团队称：已披露',accent,32)
   node(d,(91,790,629,916),'是否修复：本素材未确认','#e5c88b',29)
   text(d,'发现 → 披露 → 后续处理',105,647,30)
   d.line((90,1060,90+t/D*530,1060),fill=accent,width=4)
 d.line((50,1160,670,1160),fill='#2d4159',width=2);text(d,'原创概念示意 · 发布方报告 · 静音素材',50,1202,22,'#a6bacd')
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
   start=body.index(quote);f=dict(id=f'F{n}',text=claim,subject=spec['entity'],predicate=predicate,object=obj,modality='reported',verification='source_supported',evidence=[dict(source_id='S1',quote=quote,start=start,end=start+len(quote))]);f['fingerprint']=fact_id(f);facts.append(f)
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
  write_json(provenance,dict(renderer=str(Path(__file__).relative_to(ROOT)),renderer_sha256=sha256(Path(__file__)),helper='workflow/material_pipeline/build_cycle_20261003.py',helper_sha256=sha256(Path(__file__).with_name('build_cycle_20261003.py')),classification='Original conceptual motion; no real pet reconstruction, software execution, vulnerability payload or experiment footage',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips],audio='silent'))
  concat=out/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in clips));preview=out/'preview.mp4'
  if not preview.exists():subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(preview)],check=True)
  subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-threads','2','-f','null','-'],check=True)
  write_json(out/'narration_draft.json',dict(content_id=cid,segments=narration,audio='not synthesized',review='Main pipeline narration and timing review required'))
  package=dict(schema_version=1,content_id=cid,title=spec['title'],event=event,sources=[source],facts=facts,assets=assets,shots=shots,transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original diagrams',limitations=['Suggested narration timings are not voice alignment.']),editorial=dict(audience='普通观众',angle=spec['title'],beats=beats,cautions=spec['cautions'],verification_scope='Exact support in dated publisher case or research report; methods/results are publisher-reported, no independent execution or deployment validation.'),collection_pending=[],production_pending=['主管线旁白、叙事、结构、示意图与数值字幕审核','正式配音、字幕和节奏对齐'],production_asset_plan_reviewed=False)
  validator.validate(package);receipt=enqueue_candidate(package,R,old)
  result=dict(content_id=cid,title=spec['title'],package=str(R/'inbox'/cid/'package.json'),preview=str(preview),facts=len(facts),new_clips=4,clip_seconds=32,schema='pass',decode='4 clips and preview pass',readiness=receipt['readiness'],dedup=receipt['dedup']);results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
  sheet=Image.new('RGB',(960,1280),'#07101c')
  for row,at in enumerate((.5,4,7.5)):
   for i in range(4):sheet.paste(Image.open(out/f'S{i+1:03d}_{at}.jpg').resize((240,426)),(i*240,row*426))
  sheet.save(C/f'{spec["key"]}_decoded_review.jpg',quality=92)
 write_json(C/'intake_results.json',results)
 cards=[]
 for result in results:
  spec=next(x for x in SPECS if x['title']==result['title']);cards.append('<article><h2>'+html.escape(result['title'])+'</h2><video controls preload="metadata" src="'+str(Path(result['preview']).relative_to(C))+'"></video><p>'+html.escape(' '.join(x[3] for x in spec['panels']))+'</p><p>'+html.escape(' '.join(spec['cautions']))+'</p></article>')
 (C/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>本轮AI应用素材预览</title><style>body{font:17px system-ui;background:#101b2a;color:#e7eef7;max-width:1100px;margin:40px auto;padding:20px}main{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{background:#1d3048;padding:22px;border-radius:15px}video{width:100%;max-height:600px}</style><h1>本轮新增AI应用素材</h1><p>8段原创静音示意，64秒。发布方报告已核对，未独立复现实验或应用效果；未完成配音、字幕及生产审批。</p><main>'+''.join(cards)+'</main>', encoding="utf-8")
if __name__=='__main__':main()
