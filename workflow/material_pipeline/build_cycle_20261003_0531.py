"""This cycle only: dated SIA research reports and bespoke original motion diagrams."""
import html,json,math,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
from .build_cycle_20261003 import font,text,node,dot
from .core import content_id,enqueue_candidate,fact_id,legacy_records,sha256,write_json
from .media import probe
ROOT=Path(__file__).resolve().parents[2];R=ROOT/'research/material_pipeline';C=R/'monitor/runs/20261003T053134_787841'
W,H,FPS,D=720,1280,24,6
SPECS=[
 dict(key='vision',title='换个天气，机器人还能认出同一件东西吗',entity='沈阳自动化所HierarchicalDG团队',date='2026-09-30',
 url='https://sia.cas.cn/xwzx/kydt/202609/t20260930_8288766.html',object='跨模态因果感知层次表征学习与跨场景目标检测研究公告',
 facts=[('问题','天气光照成像设备引起数据偏移','受天气条件、光照强弱、成像设备差异等因素影响，容易产生数据分布偏移问题，进而造成模型漏检、误检'),
 ('学习链路','图像特征场景注意力目标表征视觉子原型因果原型','构建“图像特征—场景注意力—目标表征—视觉子原型—因果原型”的层次学习链路'),
 ('语义干预','文本语义引导稳定视觉原型','该框架将文本语义作为因果干预信号，在维持类别内部特征多样性的前提下，学习具有稳定语义结构的视觉原型'),
 ('研究验证','五类跨场景基准任务','该框架在多天气变化、图像扰动、真实图像到艺术图像迁移、跨相机以及仿真到现实五类基准测试任务中，均取得了良好的跨场景泛化效果'),
 ('推理结构','推理时无需调用视觉语言模型分支','推理阶段无需调用视觉语言模型分支'),
 ('人员','刘雅静与田建东','论文第一作者为博士研究生刘雅静，通讯作者为田建东研究员')],
 claims=['研究所指出，天气、光照及成像设备变化可能造成目标检测漏检和误检。','团队构建五级层次学习链路，从图像特征逐步形成因果原型。','团队以文本语义作为干预信号，学习稳定语义结构的视觉原型。','研究所报告在五类跨场景基准任务中取得泛化效果，未提供此处可独立核验的数字。','研究所说明，推理时无需调用视觉语言模型分支。','公告署名第一作者刘雅静、通讯作者田建东。'],
 panels=[('天气变了，识别也会变','分布偏移可能造成漏检、误检',[1],'同一件东西，换个天气就可能难认。沈阳自动化所团队研究怎样跨场景识别。'),
 ('逐层提炼稳定的线索','HierarchicalDG的五级学习链路',[2],'他们从图像特征一路提炼到因果原型，把知识逐层传递。'),
 ('用语义帮助训练','训练引入文本语义；推理分支减少',[3,5],'训练时用文本语义作引导。推理时，则不用再调用视觉语言模型分支。'),
 ('把方法带去不同场景','五类基准测试；研究公告所报告',[4,6],'团队报告完成五类跨场景测试。这是研究进展，仍要看真实部署验证。')],
 cautions=['2026-09-30是研究所公告日期，未取得实验执行日期。','五类基准为作者团队报告，未独立运行或复现，不宣称自动驾驶安全或已部署。','框架示意仅表现公告概念，非完整论文架构复刻；原论文访问结果另存。']),
 dict(key='eeg',title='读懂运动想象，先别丢掉脑电的结构',entity='沈阳自动化所URCNet团队',date='2026-09-28',
 url='https://sia.cas.cn/xwzx/kydt/202609/t20260928_8287350.html',object='URCNet多尺度时空特征与U型黎曼流形学习脑电解码研究公告',
 facts=[('问题','忽视频带耦合与结构衰减','往往忽视脑电不同频带间的时空耦合关联，多级特征映射过程还会造成流形结构信息衰减'),
 ('特征提取','多尺度时空卷积','利用多尺度时空卷积有效提取脑电的局部时空动态耦合特征'),
 ('结构机制','U型流形与黎曼重心跳跃连接','团队提出了基于黎曼重心的跳跃连接机制，在非欧空间对对称正定矩阵（SPD）做归一化处理'),
 ('研究验证','BCIIV2a BCIIV2b Cho2017','科研团队基于国际通用BCI Competition IV 2a、BCI Competition IV 2b和Cho2017数据集进行了验证'),
 ('作者报告结果','83.14% 83.57% 71.83%','URCNet的解码准确率分别达到83.14%、83.57%和71.83%'),
 ('人员','邓川吴雪健褚亚奇赵新刚','该论文第一作者为沈阳自动化所硕士研究生邓川、博士研究生吴雪健，通讯作者为褚亚奇副研究员和赵新刚研究员')],
 claims=['研究所指出，已有方法可能忽视频带耦合，映射时也会损失流形结构信息。','URCNet以多尺度时空卷积提取局部时空耦合特征。','团队提出基于黎曼重心的跳跃连接，对SPD矩阵归一化，帮助保持几何结构。','团队报告在BCI Competition IV 2a、2b和Cho2017三个数据集上验证。','公告报告对应解码准确率为83.14%、83.57%及71.83%；这些是团队结果，非临床成功率。','公告署名第一作者邓川、吴雪健，通讯作者褚亚奇、赵新刚。'],
 panels=[('脑电里的线索，彼此有关','图为合成信号；没有真实受试者数据',[1],'要读懂运动想象，脑电的时间和空间关联不能忽略，结构也可能在映射中损失。'),
 ('从不同尺度观察','多尺度时空卷积提取局部耦合特征',[2],'沈阳自动化所团队用多尺度时空卷积，从不同尺度提取相关特征。'),
 ('把结构信息留住','U型学习与基于黎曼重心的跳跃连接',[3],'再通过U型流形学习和跳跃连接，帮助保持特征的几何结构。'),
 ('先看数据集上的验证','三个研究数据集；不代表临床效果',[4,5,6],'团队在三个脑电数据集上做了验证。这是算法研究结果，不是临床成功率。')],
 cautions=['2026-09-28是研究所公告日期，不冒充论文首发或实验日期。','合成波形与U型关系图为概念示意，不是脑电实测或完整网络层定义。','三个准确率均为团队报告的数据集结果，未独立复现，不宣称诊断、康复疗效或脑机产品部署。','不引用含义不清的5%-20%比较增幅，正式数值字幕需主线复核。'])]

def draw_frame(spec,i,t):
 im=Image.new('RGB',(W,H),'#0b1727');d=ImageDraw.Draw(im);accent='#8ed8ff' if spec['key']=='vision' else '#c6acf4'
 d.text((50,52),f'0{i+1} / 04 · 公告 {spec["date"]}',font=font(23),fill=accent)
 text(d,spec['panels'][i][0],50,142,43);text(d,spec['panels'][i][1],50,288,24,'#9db1c8')
 if spec['key']=='vision':
  if i==0:
   for j,weather in enumerate(('晴天','雨天')):
    x=58+j*315;d.rounded_rectangle((x,460,x+288,843),radius=15,fill='#1e3954');text(d,weather,x+20,482,28)
    d.rectangle((x+88,650,x+186,760),fill='#ab8062');d.line((x+88,650,x+138,614,x+186,650),fill='#ddb690',width=5)
    if j==0:d.ellipse((x+197,533,x+236,572),fill='#e9ce80')
    else:
     for n in range(14):
      xx=x+20+(n*43)%253;yy=550+(n*59+int(t*65))%240;d.line((xx,yy,xx-9,yy+22),fill='#6f9cc1',width=2)
   text(d,'同一概念物体 · 无检测模型运行',60,925,25,'#9db1c8')
  elif i==1:
   labels=['图像特征','场景注意力','目标表征','视觉子原型','因果原型']
   for j,label in enumerate(labels):
    y=423+j*124;node(d,(173,y,547,y+77),label,accent,26)
    if j<4:d.line((360,y+77,360,y+124),fill='#58718c',width=4)
   dot(d,[(360,500),(360,997)],t*8/D,accent)
  elif i==2:
   node(d,(60,452,305,553),'文本语义',accent);node(d,(410,452,655,553),'视觉原型',accent)
   d.line((305,502,410,502),fill='#58718c',width=4);dot(d,[(305,502),(410,502)],t*8/D,accent)
   node(d,(130,725,590,822),'推理：不调用视觉语言分支',accent,25)
   text(d,'训练概念示意，未运行分类或检测',50,962,25,'#9db1c8')
  else:
   for j,label in enumerate(['多天气变化','图像扰动','真实图像 → 艺术图像','跨相机','仿真 → 现实']):
    y=425+j*117;node(d,(75,y,645,y+79),label,accent if int(t)%5==j else '#526a85',26)
   text(d,'团队报告 · 未独立复现或部署验证',50,1084,24,'#9db1c8')
 else:
  if i==0:
   for j in range(3):
    pts=[(65+x,520+j*155+math.sin(x/(25+j*12)-t*2)*28+math.sin(x/67+t)*13) for x in range(581)]
    d.line(pts,fill=accent,width=3)
   text(d,'合成波形：只说明不同尺度关联',55,1010,25,'#9db1c8')
  elif i==1:
   for j,(label,width) in enumerate([('尺度A',55),('尺度B',96),('尺度C',150)]):
    y=440+j*168;node(d,(70,y,230,y+86),label,accent)
    pts=[(270+x,y+42+math.sin(x/18-t*2)*17) for x in range(140)];d.line(pts,fill='#ad91d8',width=2)
    x=275+((t/D)*90);d.rectangle((x,y+10,x+width,y+78),outline='#f7dd8d',width=3)
   text(d,'观察窗口为概念标记，非实际核尺寸',50,1045,24,'#9db1c8')
  elif i==2:
   node(d,(60,440,278,530),'编码端',accent);node(d,(442,440,660,530),'解码端',accent)
   node(d,(215,810,505,910),'流形结构学习',accent)
   path=[(170,530),(170,690),(360,810),(550,690),(550,530)];d.line(path,fill='#526a85',width=4);dot(d,path,t*8/D,accent)
   d.line((278,485,442,485),fill='#e5be7a',width=4);dot(d,[(278,485),(442,485)],t*8/D,'#e5be7a')
   text(d,'跳跃连接：基于黎曼重心',80,620,27,'#e5be7a');text(d,'U型概念关系图 · 非完整层结构',55,1030,25,'#9db1c8')
  else:
   for j,(label,score) in enumerate([('BCI Competition IV 2a','83.14%'),('BCI Competition IV 2b','83.57%'),('Cho2017','71.83%')]):
    y=449+j*174;node(d,(58,y,662,y+134),label,accent,26)
    text(d,'团队报告准确率 '+score,82,y+75,25,'#d5c6ec')
   d.line((62,1040,62+min(t/D,1)*590,1040),fill=accent,width=5)
   text(d,'研究数据集结果 ≠ 临床成功率',55,1090,25,'#9db1c8')
 d.line((50,1160,670,1160),fill='#2d4159',width=2);text(d,'原创概念示意 · 团队报告 · 静音素材',50,1202,22,'#a6bacd')
 return im

def main():
 from jsonschema import Draft202012Validator,FormatChecker
 validator=Draft202012Validator(json.loads((ROOT/'workflow/material_pipeline/package.schema.json').read_text(encoding="utf-8")),format_checker=FormatChecker())
 old=legacy_records(list((ROOT/'research/deadline30_20261002').glob('catalog_?.json'))+list((ROOT/'research').glob('scale*/catalog.json')))
 supplemental=json.loads((C/'supplementary_results.json').read_text(encoding="utf-8"));folders={x['url']:Path(x['folder']) for x in supplemental if x['status']=='archived'}
 results=[]
 for spec in SPECS:
  folder=folders[spec['url']];meta=json.loads((folder/'source.json').read_text(encoding="utf-8"));body=(folder/'source.txt').read_text(encoding="utf-8")
  source=dict(id='S1',url=spec['url'],title=meta['title'],publisher='中国科学院沈阳自动化研究所',role='primary',published=spec['date'],date_evidence='发布时间：'+spec['date'],archive_path=str((folder/'source.html').relative_to(R)),sha256=sha256(folder/'source.html'),text_path=str((folder/'source.txt').relative_to(R)),text_sha256=sha256(folder/'source.txt'),retrieved_at=meta['fetched_at'],acquisition='Public HTTPS institute research announcement, exact date and full body archived. Author/team report, not independent replication.')
  facts=[]
  for n,((predicate,obj,quote),claim) in enumerate(zip(spec['facts'],spec['claims']),1):
   start=body.index(quote);f=dict(id=f'F{n}',text=claim,subject=spec['entity'],predicate=predicate,object=obj,modality='reported',verification='source_supported',evidence=[dict(source_id='S1',quote=quote,start=start,end=start+len(quote))]);f['fingerprint']=fact_id(f);facts.append(f)
  event=dict(entity=spec['entity'],action='研究所公开报告研究方法与验证',date=spec['date'],object=spec['object']);cid=content_id(event)
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
   for at in (.5,3,5.5):
    jpg=out/f'S{i+1:03d}_{at}.jpg';subprocess.run(['ffmpeg','-y','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1',str(jpg)],check=True)
    frames.append(dict(at=at,path=str(jpg.relative_to(R)),sha256=sha256(jpg)))
   clips.append(path);assets.append(dict(id=f'A{i+1}',source_id='S1',url=spec['url'],path=str(path.relative_to(R)),sha256=sha256(path),bytes=path.stat().st_size,kind='video',duration=duration,use='production',rights=dict(status='original',owner='本素材任务原创示意图',evidence_path=str(provenance.relative_to(R)),note='Original deterministic diagrams; no source photos/audio/data. Source URL supplies factual basis, not media download.')))
   shots.append(dict(id=f'S{i+1:03d}',asset_id=f'A{i+1}',start=0,end=D,status='candidate',observation=None,keyframes=frames,boundary_method='Authored six-second segment; decoded 0.5/3/5.5s samples'))
   beats.append(dict(id=f'B{i+1}',purpose=panel[0],fact_ids=[f'F{n}' for n in panel[2]],original_narration=panel[3],visual_plan='对应概念示意；公告层面表现，正式架构、叙事和结果字幕须主管线审核。'))
   narration.append(dict(start=i*D,end=(i+1)*D,text=panel[3],fact_ids=beats[-1]['fact_ids'],timing_status='draft suggested cut, no synthesized or aligned voice'))
  write_json(provenance,dict(renderer=str(Path(__file__).relative_to(ROOT)),renderer_sha256=sha256(Path(__file__)),helper='workflow/material_pipeline/build_cycle_20261003.py',helper_sha256=sha256(Path(__file__).with_name('build_cycle_20261003.py')),classification='Original conceptual motion; not actual EEG, detections or experiments',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips],audio='silent'))
  concat=out/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in clips));preview=out/'preview.mp4'
  if not preview.exists():subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(preview)],check=True)
  subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-threads','2','-f','null','-'],check=True)
  write_json(out/'narration_draft.json',dict(content_id=cid,segments=narration,audio='not synthesized',review='Main pipeline narration and timing review required'))
  package=dict(schema_version=1,content_id=cid,title=spec['title'],event=event,sources=[source],facts=facts,assets=assets,shots=shots,transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original diagrams',limitations=['Suggested narration timings are not voice alignment.']),editorial=dict(audience='普通观众',angle=spec['title'],beats=beats,cautions=spec['cautions'],verification_scope='Exact support in dated institute announcement. No independent paper-proof/experiment/clinical/deployment validation.'),collection_pending=[],production_pending=['主管线叙事、结构、示意图架构与数值字幕审核','正式配音、字幕和节奏对齐'],production_asset_plan_reviewed=False)
  validator.validate(package);receipt=enqueue_candidate(package,R,old)
  result=dict(content_id=cid,title=spec['title'],package=str(R/'inbox'/cid/'package.json'),preview=str(preview),facts=len(facts),new_clips=4,clip_seconds=24,schema='pass',decode='4 clips and preview pass',readiness=receipt['readiness'],dedup=receipt['dedup']);results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
  sheet=Image.new('RGB',(960,1280),'#07101c')
  for row,at in enumerate((.5,3,5.5)):
   for i in range(4):sheet.paste(Image.open(out/f'S{i+1:03d}_{at}.jpg').resize((240,426)),(i*240,row*426))
  sheet.save(C/f'{spec["key"]}_decoded_review.jpg',quality=92)
 write_json(C/'intake_results.json',results)
 cards=[]
 for result in results:
  spec=next(x for x in SPECS if x['title']==result['title']);cards.append('<article><h2>'+html.escape(result['title'])+'</h2><video controls preload="metadata" src="'+str(Path(result['preview']).relative_to(C))+'"></video><p>'+html.escape(' '.join(x[3] for x in spec['panels']))+'</p><p>'+html.escape(' '.join(spec['cautions']))+'</p></article>')
 (C/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>本轮科研素材预览</title><style>body{font:17px system-ui;background:#101b2a;color:#e7eef7;max-width:1100px;margin:40px auto;padding:20px}main{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{background:#1d3048;padding:22px;border-radius:15px}video{width:100%;max-height:600px}</style><h1>本轮新增科研素材</h1><p>8段原创静音示意，48秒。团队公告事实已核对；未复现实验，未完成配音、字幕及生产审批。</p><main>'+''.join(cards)+'</main>', encoding="utf-8")
if __name__=='__main__':main()
