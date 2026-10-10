"""First scheduled intake: two new sourced stories, original explanatory motion.

Consumes this cycle's public source archives. No production queue or inference.
Undated primary README snapshots remain an explicit research readiness blocker.
"""
import functools
import html
import json
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .core import content_id, enqueue_candidate, fact_id, legacy_records, sha256, write_json
from .media import probe

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'research/material_pipeline'
CYCLE=R/'monitor/runs/20261003T032933_528166'
W,H,FPS,DURATION=720,1280,24,8

@functools.lru_cache()
def font(n):return ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',n)

def text(d,value,x,y,size=28,color='#e8f0f8',width=615):
    f=font(size);line='';parts=[]
    for c in value:
        if d.textlength(line+c,font=f)>width:parts.append(line);line=c
        else:line+=c
    parts.append(line)
    for line in parts:d.text((x,y),line,font=f,fill=color);y+=size+13
    return y

def dot(d,path,t,color='#73dfce'):
    phase=(t/DURATION)%1;lengths=[math.dist(a,b) for a,b in zip(path,path[1:])];pos=phase*sum(lengths)
    for a,b,length in zip(path,path[1:],lengths):
        if pos<=length:
            f=pos/max(length,.001);x=a[0]+(b[0]-a[0])*f;y=a[1]+(b[1]-a[1])*f
            d.ellipse((x-9,y-9,x+9,y+9),fill=color);return
        pos-=length

def node(d,box,label,color='#73dfce',size=25):
    d.rounded_rectangle(box,radius=15,outline=color,width=3,fill='#17283d')
    text(d,label,box[0]+18,box[1]+20,size,width=box[2]-box[0]-30)

def frame(key,i,t,panels):
    im=Image.new('RGB',(W,H),'#0b1727');d=ImageDraw.Draw(im);accent='#73dfce' if key=='router' else '#ffb36b'
    d.text((50,52),f'0{i+1} / 04  ·  2026-10-02报道',font=font(24),fill=accent)
    text(d,panels[i][0],50,142,45)
    text(d,panels[i][1],50,286,25,'#9db1c8')
    if key=='router':
        if i in (0,1):
            node(d,(190,425,530,505),'请求：翻译一句话 / 研究问题')
            node(d,(230,565,490,655),'任务难度判断')
            node(d,(50,770,300,870),'本地模型')
            node(d,(420,770,670,870),'配置的其他模型')
            paths=[[(360,505),(360,565)],[(300,655),(175,710),(175,770)],[(420,655),(545,710),(545,770)]]
            for path in paths:d.line(path,fill='#526a85',width=3)
            path=paths[1] if int(t/4)%2==0 else paths[2]
            dot(d,path,t*2,accent)
            text(d,'按配置的能力上限分流；并非实测任务',50,926,24,'#9db1c8')
        elif i==2:
            node(d,(80,480,320,575),'路由返回模型ID')
            node(d,(400,480,650,575),'应用实际调用')
            node(d,(250,770,570,865),'报告调用结果')
            paths=[[(320,525),(400,525)],[(525,575),(525,700),(410,770)],[(250,815),(195,815),(195,575)]]
            for p in paths:d.line(p,fill='#526a85',width=3)
            for p in paths:dot(d,p,t,accent)
            text(d,'选择模型与执行调用分开',50,941,26,'#9db1c8')
        else:
            node(d,(70,470,330,570),'历史结果与评分')
            node(d,(410,470,650,570),'相似经验')
            node(d,(200,760,570,875),'满足证据条件才调整')
            paths=[[(330,520),(410,520)],[(535,570),(535,680),(410,760)]]
            for p in paths:d.line(p,fill='#526a85',width=3);dot(d,p,t,accent)
            text(d,'Bandit默认关闭；启用后仍需要反馈',50,935,25,'#9db1c8')
        tiers=['SIMPLE','MEDIUM','COMPLEX','RESEARCH','REASONING']
        for j,v in enumerate(tiers):d.text((50+j*125,1048),v,font=font(17),fill='#83a9c5')
    elif i==0:
        text(d,'CPU：10毫秒',60,450,28)
        d.rounded_rectangle((60,505,480,575),radius=10,fill='#467ca0')
        text(d,'GPU：6 + 1 + 6 = 13毫秒',60,665,28)
        labels=[('搬入 6',252,'#355774'),('算1',42,'#ffa766'),('搬出 6',252,'#355774')]
        x=60
        for label,width,color in labels:
            d.rectangle((x,725,x+width-2,800),fill=color)
            text(d,label,x+6,748,21,width=width-5);x+=width
        marker=60+(t/DURATION)*546;d.line((marker,716,marker,811),fill='#f4f6ff',width=3)
        text(d,'这是报道中的假设例子，不是性能实测',60,933,25,'#9db1c8')
    elif i==1:
        text(d,'42项任务 · 三种输入规模',50,424,32)
        for j in range(42):
            x=72+(j%7)*83;y=540+(j//7)*60
            active=(int(t*8)%42)==j
            d.rounded_rectangle((x,y,x+54,y+36),radius=6,fill=accent if active else '#294762')
        for j,v in enumerate(['小规模','中规模','大规模']):
            d.rounded_rectangle((65+j*207,955,240+j*207,1013),radius=12,outline=accent,width=2)
            text(d,v,82+j*207,972,23)
    elif i==2:
        node(d,(60,450,320,580),'参考结果')
        node(d,(400,450,660,580),'生成代码的结果')
        for j in range(3):
            y=672+j*55;d.line((90,y,285,y),fill='#355774',width=14);d.line((430,y,625,y),fill='#355774',width=14)
        path=[(190,580),(190,860),(530,860),(530,580)];d.line(path,fill='#526a85',width=3);dot(d,path,t,accent)
        text(d,'先核对正确性，再比较完整耗时',60,971,28)
    else:
        labels=['内存分配','搬入数据','GPU计算','搬出与清理']
        for j,v in enumerate(labels):node(d,(185,415+j*140,535,495+j*140),v,accent)
        path=[(550,455),(620,455),(620,970),(550,970)];d.line(path,fill='#526a85',width=3);dot(d,path,t,accent)
        text(d,'完整入口计时：别把开销藏到外面',50,1060,26,'#9db1c8')
    d.line((50,1150,670,1150),fill='#2d4159',width=2)
    text(d,'原创流程示意 · 非运行结果 · 静音素材',50,1190,23,'#a6bacd')
    return im

STORIES=[
    dict(key='router',title='AI做事，也要先选对帮手',entity='openJiuwen X-Router',obj='任务难度分级、模型分流与反馈机制',
         sources=[('S1','acddebd833dc6d33f7dc','量子位','secondary','2026-10-02','2026-10-02'),
                  ('S2','', 'openJiuwen官方代码仓库','primary',None,'undated README snapshot; publication date unavailable')],
         primary_url='https://github.com/openJiuwen-ai/model-router/blob/main/python/openjiuwen/x_router/README.md',
         facts=[('S1','openJiuwen团队在Agent与模型之间增加路由决策层。','路由位置','Agent与模型之间的请求决策层','在Agent与模型之间，增加一层面向请求的智能决策能力','reported'),
                ('S2','官方文档说明，小分类器将任务划为五个难度等级。','分类方式','SIMPLE MEDIUM COMPLEX RESEARCH REASONING','A small classifier reads the conversation and labels it SIMPLE, MEDIUM,\nCOMPLEX, RESEARCH or REASONING.','reported'),
                ('S2','在配置能力范围内的任务留在本地模型，超出范围的任务交给对应等级配置的模型。','分流规则','按配置本地能力上限和等级映射','Anything at or below a configured\ncapability level stays on the local model; anything above it goes to the model\nyou mapped that tier to.','reported'),
                ('S2','路由器返回模型ID，实际模型调用由应用完成。','调用责任','路由选择应用调用','it returns a model id and your application makes the call.','reported'),
                ('S2','Bandit经验修正默认关闭，启用后利用历史会话结果辅助路由。','经验修正','默认关闭启用后利用历史结果','Off by default. When enabled, x-router remembers how conversations turned out','reported')],
         panels=[('同一个AI，背后有不同帮手','问题的轻重不同，选择也可以不同',[1,2],'简单翻译和复杂研究，都要用同一个模型吗？openJiuwen给出了路由层。'),
                 ('先判断任务，再选择模型','按配置能力上限和难度等级分流',[2,3],'它先判断任务难度。在配置的能力范围内留给本地，超出范围再选对应模型。'),
                 ('路由负责选，应用负责做','官方文档明确的调用分工',[4],'路由返回选中的模型编号。真正发出调用的是应用，然后再报告结果。'),
                 ('经验要有条件地使用','默认不开启经验修正，不保证越用越好',[5],'经验修正默认关闭。开启后，它才利用历史反馈，具体效果仍要验证。')],
         cautions=['2026-10-02是量子位报道日期，不冒充官方代码首发日期。','不引用标题中的50%以上Token或成本降幅；未独立复现性能或效果。','原始README未提供可核对的发布日期，按现有严格研究闸门保持research_ready=false。','分类和路由图为流程说明，示例任务未实际运行。']),
    dict(key='accel',title='GPU算得更快，为什么答案反而更晚',entity='AccelEval',obj='端到端计时、42项任务和三种规模',
         sources=[('S1','40129d510a38d55d68fe','新智元','secondary','2026-10-02','API publication date: 2026-10-02T08:08:09'),
                  ('S2','f99b16c9319efcb77195','AccelEval作者代码仓库','primary',None,'undated README snapshot; publication date unavailable')],
         primary_url='https://github.com/Echoscd/AccelEval',
         facts=[('S1','报道用一个假设例子解释端到端开销：CPU10毫秒，GPU计算1毫秒，但输入与输出相关开销各6毫秒，合计13毫秒。','说明性假设','CPU10ms GPU6+1+6=13ms','CPU完成任务需要10毫秒；换到GPU后，核心计算只需1毫秒，但准备与输入搬运花了6毫秒，收尾与输出搬运又花了6毫秒。','hypothesis'),
                ('S2','作者仓库列出42项任务，每项有小、中、大三种输入规模。','任务与规模','42 tasks x3 scales','Tasks: 42 tasks × 3 input scales (small / medium / large)','reported'),
                ('S2','作者仓库规定端到端计时包括分配、CPU/GPU间搬运、计算、库调用与清理。','计时边界','allocation transfer kernel library cleanup','timed end-to-end (allocation, H↔D copy, kernel, library, cleanup)','reported'),
                ('S1','生成的GPU程序先编译运行并与CPU结果比较，数值敏感任务使用规定的容差。','正确性验证','编译运行比较输出及规定误差容限','模型提交的GPU版本首先要成功编译和运行，再在相同输入下与CPU输出比较；数值敏感任务采用预先规定的误差容限。','reported'),
                ('S2','作者仓库将通过验证的实现按43类CUDA优化策略进行标注。','策略分析','43 pattern CUDA optimization catalog','a 43-pattern CUDA optimization catalog','reported')],
         panels=[('算得快，不等于更早拿到答案','先看一个明确标注的假设例子',[1],'假设CPU用十毫秒。GPU只算一毫秒，搬入搬出却各花六毫秒，总共十三毫秒。'),
                 ('把问题放进不同规模','AccelEval：42项任务，三种输入规模',[2],'AccelEval用四十二项任务、三种输入规模，检查AI写出的GPU程序。'),
                 ('先算对，再谈快','图为验证流程，不代表已运行通过',[4],'生成程序先要跑起来，并与参考结果核对。数值任务还要符合误差要求。'),
                 ('从输入到答案，一起计时','分配、搬运、计算与清理都在计时边界内',[3,5],'它把分配、搬运、计算和清理放进同一个计时入口。用户等的是完整答案。')],
         cautions=['10/1/6/6毫秒全部是报道中的假设，不是AccelEval实测结果。','不使用报道中的通用倍速或模型排行榜，也不宣称已验证NeurIPS接收信息。','作者仓库未提供可核对的发布日期，严格研究闸门保持research_ready=false。','未执行任何benchmark、安装仓库代码或提交GPU/NPU任务。'])]

def main():
    supplemental=json.loads((CYCLE/'supplementary_results.json').read_text(encoding="utf-8"))
    primary_folders={x['url']:Path(x['folder']) for x in supplemental if x['status']=='archived'}
    old=legacy_records(list((ROOT/'research/deadline30_20261002').glob('catalog_?.json'))+list((ROOT/'research').glob('scale*/catalog.json')))
    schema=json.loads((ROOT/'workflow/material_pipeline/package.schema.json').read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator,FormatChecker
    validator=Draft202012Validator(schema,format_checker=FormatChecker());results=[]
    for spec in STORIES:
        sources=[];bodies={}
        for sid,sfolder,publisher,role,published,date_evidence in spec['sources']:
            folder=CYCLE/'supplementary/sources'/sfolder if sfolder else primary_folders[spec['primary_url']]
            meta=json.loads((folder/'source.json').read_text(encoding="utf-8"));body=(folder/'source.txt').read_text(encoding="utf-8");bodies[sid]=body
            archive=folder/('source.json.raw' if meta.get('format')=='public_wordpress_json' else 'source.html')
            sources.append(dict(id=sid,url=meta['url'],title=meta['title'],publisher=publisher,role=role,published=published,
                                date_evidence=date_evidence,archive_path=str(archive.relative_to(R)),sha256=sha256(archive),
                                text_path=str((folder/'source.txt').relative_to(R)),text_sha256=sha256(folder/'source.txt'),retrieved_at=meta['fetched_at'],
                                acquisition='Public HTTPS original HTML or declared public WordPress JSON; exact evidence checked. README snapshot is undated; no publication date inferred.'))
        facts=[]
        for i,(sid,value,predicate,obj,quote,modality) in enumerate(spec['facts'],1):
            start=bodies[sid].index(quote)
            f=dict(id=f'F{i}',text=value,subject=spec['entity'],predicate=predicate,object=obj,modality=modality,
                   verification='source_supported',evidence=[dict(source_id=sid,quote=quote,start=start,end=start+len(quote))]);f['fingerprint']=fact_id(f);facts.append(f)
        event=dict(entity=spec['entity'],action='公开报道技术能力与原始实现',date='2026-10-02',object=spec['obj']);cid=content_id(event)
        out=CYCLE/'media'/spec['key'];out.mkdir(parents=True,exist_ok=True);clips=[];assets=[];shots=[];beats=[];narration=[]
        provenance=out/'original_provenance.json'
        for i,panel in enumerate(spec['panels']):
            path=out/f'S{i+1:03d}.mp4'
            if not path.exists():
                proc=subprocess.Popen(['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-',
                                       '-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],stdin=subprocess.PIPE)
                try:
                    for n in range(FPS*DURATION):proc.stdin.write(frame(spec['key'],i,n/FPS,spec['panels']).tobytes())
                finally:proc.stdin.close()
                if proc.wait()!=0:raise RuntimeError('encode failed')
            info=probe(path);duration=float(info['format']['duration']);video=next(v for v in info['streams'] if v['codec_type']=='video')
            if abs(duration-DURATION)>.01 or (video['width'],video['height'])!=(W,H):raise ValueError('wrong media geometry/duration')
            if any(s['codec_type']=='audio' for s in info['streams']):raise ValueError('unexpected audio')
            subprocess.run(['ffmpeg','-v','error','-i',str(path),'-threads','2','-f','null','-'],check=True)
            frames=[]
            for at in (0.5,4,7.5):
                jpg=out/f'S{i+1:03d}_{at}.jpg'
                subprocess.run(['ffmpeg','-y','-v','error','-ss',str(at),'-i',str(path),'-frames:v','1',str(jpg)],check=True)
                frames.append(dict(at=at,path=str(jpg.relative_to(R)),sha256=sha256(jpg)))
            clips.append(path)
            assets.append(dict(id=f'A{i+1}',source_id='S1',url=sources[0]['url'],path=str(path.relative_to(R)),sha256=sha256(path),bytes=path.stat().st_size,
                               kind='video',duration=duration,use='production',rights=dict(status='original',owner='本素材任务原创流程图',evidence_path=str(provenance.relative_to(R)),
                                                                                      note='Original deterministic diagrams, no third-party imagery/audio; URL is factual basis.')))
            shots.append(dict(id=f'S{i+1:03d}',asset_id=f'A{i+1}',start=0,end=duration,status='candidate',observation=None,keyframes=frames,boundary_method='authored eight-second segment; decoded first/mid/end samples'))
            beats.append(dict(id=f'B{i+1}',purpose=panel[0],fact_ids=[f'F{n}' for n in panel[2]],original_narration=panel[3],visual_plan='对应原创流程动画；由主管线审稿后决定正式剪辑、配音和字幕。'))
            narration.append(dict(start=i*DURATION,end=(i+1)*DURATION,text=panel[3],fact_ids=beats[-1]['fact_ids'],timing_status='draft; voice alignment not performed'))
        write_json(provenance,dict(renderer=str(Path(__file__).relative_to(ROOT)),renderer_sha256=sha256(Path(__file__)),classification='authored illustrative workflow, not documentary imagery or actual test output',
                                   soundtrack='silent; original narration supplied separately',asset_url_semantics='factual basis, not download URL',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips]))
        concat=out/'concat.txt';concat.write_text(''.join(f"file '{p.name}'\n" for p in clips));preview=out/'preview.mp4'
        if not preview.exists():subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(preview)],check=True)
        write_json(out/'narration_draft.json',dict(content_id=cid,segments=narration,audio='not synthesized',review='main production owner required'))
        package=dict(schema_version=1,content_id=cid,title=spec['title'],event=event,sources=sources,facts=facts,assets=assets,shots=shots,
                     transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original diagrams; no source audio',limitations=['旁白稿未配音，时间轴是剪辑建议，非已对齐语音。']),
                     editorial=dict(audience='普通观众',angle=spec['title'],beats=beats,cautions=spec['cautions'],verification_scope='Exact source support and official README cross-check; no independent implementation/benchmark verification'),
                     collection_pending=[],production_pending=['原始README发布日期未提供，不虚构日期；需研究侧日期复核或主管线判断','主管线叙事、结构与示意运动审核','正式配音、字幕与节奏对齐'],production_asset_plan_reviewed=False)
        validator.validate(package);receipt=enqueue_candidate(package,R,old)
        results.append(dict(content_id=cid,title=spec['title'],package=str(R/'inbox'/cid/'package.json'),preview=str(preview),narration=str(out/'narration_draft.json'),
                            new_clips=4,clip_seconds=32,facts=5,readiness=receipt['readiness'],dedup=receipt['dedup'],schema='pass',media_decode='4 clips passed'))
        print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    write_json(CYCLE/'intake_results.json',results)
    cards=[]
    for result in results:
        rel=Path(result['preview']).relative_to(CYCLE);body=' '.join(p[3] for p in next(s for s in STORIES if s['title']==result['title'])['panels'])
        cards.append(f'<article><h2>{html.escape(result["title"])}</h2><video controls preload="metadata" src="{rel}"></video><p>{html.escape(body)}</p><p>报道日期2026-10-02；原始README未提供发布日期，research_ready=false。待日期、叙事、配音、字幕与结构审核。</p></article>')
    (CYCLE/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>自动采集首轮素材</title><style>body{font:17px system-ui;background:#101b2a;color:#e7eef7;max-width:1100px;margin:40px auto;padding:20px}main{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{background:#1d3048;padding:22px;border-radius:15px}video{width:100%;max-height:600px}</style><h1>本轮新增素材 · 两个故事</h1><p>8段原创静音流程动画，64秒。事实证据与媒体校验通过，原始技术文档日期仍有缺口；候选未获生产批准。</p><main>'+''.join(cards)+'</main>', encoding="utf-8")

if __name__=='__main__':main()
