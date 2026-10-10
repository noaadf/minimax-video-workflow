"""Source-backed first publication batch; original CPU schematic clips, file inbox only.

No queue, production controller, network models, accelerator or social publication.
Run with the existing research Python after public source archives have been collected.
"""
import datetime as dt
import html
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .core import content_id, enqueue_candidate, fact_id, legacy_records, sha256, write_json
from .media import probe

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / 'research/material_pipeline'
BATCH = RESEARCH / 'batches/20261003_first'
FONT = '/System/Library/Fonts/STHeiti Medium.ttc'
W, H, FPS, SECONDS = 720, 1280, 24, 6

# All factual clauses have exact archived evidence; interpretation is explicitly editorial.
SPECS = [
    dict(key='water_rescue', title='危险水域，先让机器人去', publisher='江淮实验室',
         folder='2796d8af7421734b6569', date='2026-06-18', date_quote='2026.06.18',
         entity='江淮中心水面搜救机器人', action='公开介绍应急装备', obj='双体搜救机器人与多传感器融合', theme='engineering',
         facts=[
             ('水面搜救机器人与微型无人直升机参加了安全宣传咨询日主场活动。', '参展装备', '水面搜救机器人和微型无人直升机', '江淮中心研制的水面搜救机器人和“智影”微型智能无人直升机参展。'),
             ('研制方介绍，水面搜救机器人采用双体构型。', '构型', '双体', '江淮中心自主研发的水面搜救机器人采用双体构型设计'),
             ('研制方介绍，设备融合多类传感器，进行自主巡航、目标识别与避障。', '感知与巡航', '多传感器融合自主巡航识别避障', '整机集成RTK、激光雷达、广谱相机、IMU等多类感知传感器，依靠多传感器融合算法实现水面高精度自主巡航，可对水面目标进行识别和主动避障。'),
             ('研制方介绍，装备具备自主搜寻与协同打捞功能。', '作业功能', '自主搜寻协同打捞', '具有自主搜寻、协同打捞一体化作业功能')],
         panels=[('先找到，再靠近', '危险水域的另一种办法', ['搜寻','靠近','协同'], [1], '水域救援，能不能先让机器去探路？江淮中心研制了水面搜救机器人。'),
                 ('双体构型', '研制方公开介绍的设计', ['双体','水面巡航'], [2], '它采用双体构型。画面是概念示意，真实结构以研制方资料为准。'),
                 ('多种传感器一起工作', '巡航 · 识别 · 避障', ['定位','感知','避障'], [3], '多种传感器配合，让机器人在水面巡航，识别目标并主动避障。'),
                 ('让救援多一个选择', '自主搜寻与协同打捞', ['搜寻','协同'], [4], '研制方介绍，它还具备协同打捞功能。进入现场前，仍要看具体环境和验证。')],
         cautions=['报道发表于2026年6月18日；不包装为今天新发布。', '性能和功能是研制方介绍，未独立测试；不虚构成功救人、事故现场或伤亡数字。', '示意不表示真实设备比例、结构细节或传感器安装位置。']),
    dict(key='cann_install', title='装好AI软件，先把选包这一步变简单', publisher='昇腾社区',
         folder='97ef8e57b26fedf5de95', date='2026-02-10', date_quote='发表于 2026/02/10',
         entity='CANN一站式安装部署页面', action='发布升级', obj='自动匹配软件包与简化下载安装', theme='product',
         facts=[
             ('CANN一站式页面根据产品系列、CPU架构和操作系统匹配软件包。', '选包方式', '按产品系列CPU架构操作系统匹配', '根据产品系列、CPU架构、操作系统，自动匹配对应软件包。'),
             ('该发布介绍软件包可以无需注册登录直接下载。', '下载方式', '无需注册登录', '无需注册登录，直接下载软件包。'),
             ('页面支持wget命令直连官网下载到本地服务器。', '服务器下载', 'wget直连官网', '支持wget一行命令，直连官网下载软件包到本地服务器。'),
             ('该发布介绍提供yum和Conda安装方式。', '安装方式', 'yum和Conda', '提供yum、Conda安装方式')],
         panels=[('先选对，再安装', 'CANN一站式部署页面', ['产品','架构','系统'], [1], '装好AI软件，先得选对软件包。CANN把产品、架构和系统放进同一个入口。'),
                 ('三项条件，匹配软件包', '按公开页面的选择逻辑示意', ['产品系列','CPU架构','操作系统'], [1], '开发者选好这三项条件，页面再匹配对应的软件包。'),
                 ('下载也少一道门槛', '2026年2月发布介绍', ['直接下载','服务器下载'], [2,3], '这次发布介绍，无需注册登录就能下载，也支持命令直连官网。'),
                 ('把精力留给开发', '提供yum与Conda安装方式', ['选包','下载','安装'], [4], '安装提供多种方式。具体怎么用，仍要对应设备与版本查官方文档。')],
         cautions=['这是2026年2月的功能发布，不代表已实测当前安装页面。', '不宣称任何硬件都兼容、不保证零门槛或一次安装成功。', '不将官方不同版本安装包尺寸混为当前性能比较。']),
    dict(key='spring_engineers', title='春节里，500多人把模型落到机器上', publisher='昇腾社区',
         folder='31cca4d41323e88b0d3c', date='2026-03-20', date_quote='发表于 2026/03/20',
         entity='昇腾练秋湖DeepSeek攻关团队', action='回顾2025春节模型部署攻关', obj='500多人攻关与一体机参考配置', theme='engineering',
         facts=[
             ('华为在2026年演讲中回顾：2025年春节期间在上海练秋湖集结500多人攻关。', '历史攻关规模', '2025春节上海练秋湖500多人', '去年DeepSeek一发布，春节期间我们迅速在上海练秋湖集结了500多人攻关会战'),
             ('该演讲称，团队不到一周发布大模型应用一体机参考配置。', '阶段结果', '不到一周发布参考配置', '不到一周就发布了大模型应用一体机参考配置。'),
             ('该演讲称，80多家伙伴在两周内发布了自己的一体机产品。', '伙伴响应', '80多家伙伴两周内发布一体机', '使能80多家伙伴2周内发布了自己的一体机产品')],
         panels=[('2025年春节', '一段2026年公开回顾的攻关经历', ['模型','部署'], [1], '春节里，上海练秋湖集结了五百多人。华为后来这样回顾一次模型部署攻关。'),
                 ('从模型到机器', '原创概念示意，不是攻关实拍', ['模型','软件','设备'], [1,2], '模型发布之后，怎样落到具体设备上？团队把工作推进到一体机参考配置。'),
                 ('不到一周', '演讲披露的阶段结果', ['参考配置','应用一体机'], [2], '按演讲披露的说法，这份参考配置在不到一周里发布。'),
                 ('伙伴继续落地', '演讲称：80多家伙伴在两周内发布产品', ['参考','集成','产品'], [3], '接下来，八十多家伙伴在两周内发布产品。这里讲的是部署攻关，不是训练新模型。')],
         cautions=['数字来自企业演讲回顾，未独立核验；旁白明确归因。', '事件发生在2025年春节，发布日期2026年3月20日。', '发布参考配置不等于研发或训练出DeepSeek模型；不虚构团队姓名、对白、通宵或家庭故事。']),
    dict(key='deepseek_ascend', title='模型之外，DeepSeek还开源了哪些工具', publisher='DeepSeek官方代码仓库',
         folder='823940b07cea9b338457', date='2026-09-30', date_quote='2026.09.30: Initial release of DeepGEMM Ascend',
         entity='DeepGEMM Ascend', action='首次开源发布', obj='Ascend950计算库与兼容API', theme='science',
         facts=[
             ('官方README记载DeepGEMM Ascend于2026年9月30日首次发布，支持Ascend 950。', '首次发布', '2026-09-30支持Ascend950', '2026.09.30: Initial release of DeepGEMM Ascend, with support for Ascend 950 devices.'),
             ('DeepGEMM Ascend是DeepGEMM在华为昇腾平台上的实现。', '实现平台', '华为昇腾', 'DeepGEMM Ascend 是华为昇腾平台上的 DeepGEMM 实现。'),
             ('官方说明它完全兼容DeepGEMM API，支持多种计算算子。', '接口与算子', 'DeepGEMM API兼容BF16FP8FP4GEMM', '它完全兼容 DeepGEMM 的 API，支持 BF16、FP8、FP4 GEMM、MQA logits 和 MegaMoE 算子。'),
             ('官方说明用户可以沿用其他平台上DeepGEMM的API和开发流程。', '开发流程', '沿用API和开发流程', '用户只需安装当前包，即可沿用其他平台上 DeepGEMM 的 API 和开发流程。')],
         panels=[('模型之外，还有工具', 'DeepGEMM Ascend · 2026.09.30', ['模型','计算库'], [1,2], 'DeepSeek这次开源的，还有模型下面的工具。九月三十日，DeepGEMM昇腾版首次发布。'),
                 ('把计算落到昇腾平台', '官方代码仓库中的计算库', ['接口','计算','芯片'], [2,3], '它把DeepGEMM带到昇腾平台，支持矩阵计算等算子。'),
                 ('熟悉的接口继续使用', '官方说明：API兼容', ['原有API','昇腾实现'], [3,4], '开发者可以沿用原有接口和开发流程。变化藏在底层的实现里。'),
                 ('开源后，可以查代码', '具体支持范围以官方仓库为准', ['代码','复核','适配'], [1,3], '想知道它怎么工作，可以回到官方代码。兼容接口，也要核对硬件和版本要求。')],
         cautions=['量子位是发现入口，事实回到DeepSeek官方README。', '不将API兼容写成任意软件无修改兼容或所有芯片支持。', '不把单算子性能数字写成全系统、在线服务或用户体验提升。'])
]


def font(size):
    return ImageFont.truetype(FONT, size)


def lines(draw, text, xy, size=36, color='#eef5ff', max_width=600, spacing=15):
    f=font(size); result=[]; line=''
    for ch in text:
        if draw.textlength(line+ch,font=f)>max_width:
            result.append(line);line=ch
        else:line+=ch
    result.append(line)
    x,y=xy
    for line in result:
        draw.text((x,y),line,font=f,fill=color);y+=size+spacing
    return y


def frame(spec, index, t):
    im=Image.new('RGB',(W,H),'#0a1526');d=ImageDraw.Draw(im)
    accent='#68ddd0' if spec['key']=='water_rescue' else '#ffa766'
    title,sub,labels,_,narration=spec['panels'][index]
    d.text((50,50),f"{index+1:02d} / 04",font=font(26),fill=accent)
    d.text((50,99),spec['date']+'  来源报道日期',font=font(23),fill='#91a7be')
    lines(d,title,(50,168),size=47,max_width=615)
    lines(d,sub,(50,315),size=25,color='#91a7be')
    # Abstract motion deliberately avoids photorealistic people, logos and device geometry.
    if spec['key']=='water_rescue':
        for j in range(7):
            y=525+j*36
            points=[(x,y+7*math.sin(x/50+t*2+j)) for x in range(45,680,8)]
            d.line(points,fill='#255876',width=2)
        x=170+160*math.sin(t*.35)
        for dx in (0,95):d.rounded_rectangle((x+dx,580,x+dx+38,718),radius=16,fill=accent)
        d.line((x+19,645,x+114,645),fill=accent,width=12)
        d.ellipse((550,606,596,652),outline='#ffa766',width=4)
        r=45+int(t*25)%110;d.arc((x-r+65,650-r,x+r+65,650+r),-65,65,fill='#d7fbf7',width=3)
    else:
        boxes=[(65,535,235,690),(275,535,445,690),(485,535,655,690)]
        for j,b in enumerate(boxes):
            d.rounded_rectangle(b,radius=20,outline=accent,width=3)
            if spec['key']=='spring_engineers':
                for k in range(3):d.line((b[0]+28,b[1]+42+k*32,b[2]-28,b[1]+42+k*32),fill='#59778d',width=4)
            else:
                for k in range(4):
                    px=b[0]+32+(k%2)*67;py=b[1]+35+(k//2)*65
                    d.rounded_rectangle((px,py,px+35,py+35),radius=5,fill='#28586d')
        for j in range(2):
            x=240+j*210;d.line((x,610,x+29,610),fill='#607b90',width=3)
            p=x+(t*22+j*12)%30;d.ellipse((p-5,605,p+5,615),fill=accent)
    y=790
    for label in labels:
        d.rounded_rectangle((50,y,670,y+62),radius=12,fill='#162b42')
        d.text((72,y+14),label,font=font(28),fill='#dcecf7');y+=78
    d.line((50,1118,670,1118),fill='#2c435a',width=2)
    d.text((50,1148),'原创概念示意 · 非现场实拍',font=font(23),fill='#aabed0')
    # A deterministic progress cue, no asserted performance metrics.
    d.rectangle((50,1220,50+int(620*t/SECONDS),1224),fill=accent)
    return im


def render(spec, out):
    out.mkdir(parents=True,exist_ok=True); clips=[]; shots=[]
    for i in range(4):
        path=out/f'shot_{i+1:02d}.mp4';jpg=out/f'shot_{i+1:02d}.jpg'
        if not path.exists():
            cmd=['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}',
                 '-r',str(FPS),'-i','-','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','20',
                 '-pix_fmt','yuv420p','-movflags','+faststart',str(path)]
            proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
            try:
                for n in range(FPS*SECONDS):proc.stdin.write(frame(spec,i,n/FPS).tobytes())
            finally:proc.stdin.close()
            if proc.wait()!=0:raise RuntimeError('render failed')
        frame(spec,i,3).save(jpg,quality=93)
        info=probe(path)
        if abs(float(info['format']['duration'])-SECONDS)>.01:raise ValueError('clip duration mismatch')
        video=next(s for s in info['streams'] if s['codec_type']=='video')
        if (video['width'],video['height'])!=(W,H):raise ValueError('clip dimensions mismatch')
        clips.append(path)
    listing=out/'concat.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in clips), encoding="utf-8")
    preview=out/'preview.mp4'
    if not preview.exists():subprocess.run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(listing),
                                          '-c','copy','-movflags','+faststart',str(preview)],check=True)
    return clips,preview


def main():
    now=dt.datetime.now(dt.timezone.utc).isoformat();receipts=[];topics=[];previews=[]
    old=legacy_records(list((ROOT/'research/deadline30_20261002').glob('catalog_?.json'))+
                       list((ROOT/'research').glob('scale*/catalog.json')))
    for spec in SPECS:
        folder=BATCH/'sources'/spec['folder'];meta=json.loads((folder/'source.json').read_text(encoding="utf-8"))
        body=(folder/'source.txt').read_text(encoding="utf-8")
        if spec['date_quote'] not in body:raise ValueError('date evidence absent')
        source=dict(id='P1',url=meta['url'],title=meta['title'],publisher=spec['publisher'],role='primary',
                    published=spec['date'],date_evidence=spec['date_quote'],archive_path=str((folder/'source.html').relative_to(RESEARCH)),
                    sha256=sha256(folder/'source.html'),text_path=str((folder/'source.txt').relative_to(RESEARCH)),
                    text_sha256=sha256(folder/'source.txt'),retrieved_at=meta['fetched_at'],
                    acquisition='Public HTTPS HTML archived; exact visible date/release line and evidence checked')
        facts=[]
        for i,(text,predicate,obj,quote) in enumerate(spec['facts'],1):
            start=body.index(quote)
            f=dict(id=f'F{i}',text=text,subject=spec['entity'],predicate=predicate,object=obj,modality='reported',
                   verification='source_supported',evidence=[dict(source_id='P1',quote=quote,start=start,end=start+len(quote))])
            f['fingerprint']=fact_id(f);facts.append(f)
        event=dict(entity=spec['entity'],action=spec['action'],date=spec['date'],object=spec['obj'])
        cid=content_id(event);out=BATCH/'media'/spec['key'];clips,preview=render(spec,out)
        rights=out/'original_provenance.json'
        write_json(rights,dict(method='Pillow vector schematic drawn by this task and encoded locally with FFmpeg; no source imagery, logo, music or footage used',
                               creator='Codex material task',renderer='workflow/material_pipeline/build_batch.py',renderer_sha256=sha256(Path(__file__)),
                               source_url_semantics='asset.url points to factual basis, not a download URL for the original asset',
                               audio='silent; original narration is provided separately for main pipeline voice synthesis',
                               classification='conceptual illustration, not a documentary reconstruction',clips=[dict(path=p.name,sha256=sha256(p)) for p in clips]))
        assets=[];shots=[];beats=[];timed=[]
        for i,(clip,panel) in enumerate(zip(clips,spec['panels']),1):
            jpg=out/f'shot_{i:02d}.jpg';aid=f'A{i}'
            assets.append(dict(id=aid,source_id='P1',url=source['url'],path=str(clip.relative_to(RESEARCH)),sha256=sha256(clip),
                               bytes=clip.stat().st_size,kind='video',duration=SECONDS,use='production',
                               rights=dict(status='original',owner='本素材任务原创绘制',evidence_path=str(rights.relative_to(RESEARCH)),
                                           note='原创概念示意，无原站影像/人物/声音/配乐；url仅指事实依据。')))
            shots.append(dict(id=f'S{i:03d}',asset_id=aid,start=0,end=SECONDS,status='candidate',observation=None,
                              keyframes=[dict(at=3,path=str(jpg.relative_to(RESEARCH)),sha256=sha256(jpg))],boundary_method='deterministic six-second authored segment'))
            beats.append(dict(id=f'B{i}',purpose=panel[0],fact_ids=[f'F{x}' for x in panel[3]],original_narration=panel[4],
                              visual_plan='使用对应原创示意片段；正式视频由主管线审稿后重新配音、剪辑和字幕。'))
            timed.append(dict(start=(i-1)*SECONDS,end=i*SECONDS,text=panel[4],fact_ids=[f'F{x}' for x in panel[3]],
                              audio_status='not_synthesized',timing_status='draft_requires_voice_alignment'))
        write_json(out/'narration_draft.json',dict(content_id=cid,language='zh-CN',segments=timed,note='原创旁白草稿；未配音；分段时间是视觉参考，不是已验证语音时长'))
        package=dict(schema_version=1,content_id=cid,title=spec['title'],event=event,sources=[source],facts=facts,assets=assets,shots=shots,
                     transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Original silent schematic assets; no source speech to transcribe',
                                     limitations=['旁白稿单独提供；正式配音和对齐由主生产管线完成。']),
                     editorial=dict(audience='普通观众',angle=spec['title'],beats=beats,cautions=spec['cautions'],
                                    verification_scope='Source-supported reporting, not independent experimental verification; no quantitative performance claims added'),
                     collection_pending=[],production_pending=['主管线叙事审核、配音与字幕对齐','原创示意镜头运动与结构审核'],production_asset_plan_reviewed=False)
        receipt=enqueue_candidate(package,RESEARCH,old);receipts.append(receipt)
        topics.append(dict(id=cid,title=spec['title'],theme=spec['theme'],status='candidate_only',evidence_status='source_supported',
                           source_url=source['url'],published=source['published'],source_archive=str(folder/'source.json'),
                           facts=[dict(id=f['id'],text=f['text'],source_url=source['url'],source_published=source['published'],
                                       evidence_quote=f['evidence'][0]['quote'],verification_scope=package['editorial']['verification_scope']) for f in facts],
                           visual_potential=4,cautions=spec['cautions'],material_package=str(RESEARCH/'inbox'/cid/'package.json'),
                           preview=str(preview),narration_draft=str(out/'narration_draft.json'),readiness=receipt['readiness'],
                           admission='Main production owner must review before changing candidate_only status; no queue admission performed here'))
        previews.append(dict(title=spec['title'],path=str(preview),clips=[str(p) for p in clips]))
        print(json.dumps(dict(content_id=cid,title=spec['title'],readiness=receipt['readiness']),ensure_ascii=False),flush=True)
    oldpackage=next((RESEARCH/'inbox').glob('material_bf4*/package.json'))
    catalog=dict(schema_version=1,generated_at=now,mode='candidate_only_file_handoff',topics=topics,
                 existing_candidate=str(oldpackage),production_queue_jobs_created=0)
    write_json(BATCH/'candidate_catalog.json',catalog)
    coverage=json.loads((RESEARCH/'publication_sources.json').read_text(encoding="utf-8"))
    write_json(BATCH/'source_pool.json',coverage)
    blocked=[dict(name='新智元',status='listing_archived; no verified original story in this batch',reason='Discovered article list retained for later primary-source verification'),
             dict(name='机器之心',status='public homepage archived; article list requires dynamic rendering',reason='Rescue story sourced from original research institution, not claimed to be a collected WeChat push'),
             dict(name='量子位',status='two full dated articles archived',selection='DeepSeek components followed back to official repository'),
             dict(name='昇腾CANN',status='community account entry archived; two dated official Ascend articles used',reason='No WeChat subscription or private push history collected'),
             dict(name='格致论道讲坛',status='speaker page archived; dinosaur talk has no visible full text/date',reason='Not promoted into a fact package from title alone'),
             dict(name='华为终端新闻中心',status='XMAGE page archived; article body/date absent in static response',reason='Not promoted into a fact package from title alone')]
    write_json(BATCH/'coverage.json',dict(priority_accounts=blocked,all_sources=len(coverage['sources']),selected_new_stories=4,
                                         latest_feed_claim=False,wechat_push_history_collected=False))
    cards=[]
    for t in topics:
        rel=Path(t['preview']).relative_to(BATCH)
        cards.append(f'<article><h2>{html.escape(t["title"])}</h2><p>{t["published"]} · 原创概念示意 · 静音素材</p><video controls preload="metadata" src="{rel}"></video><p>{html.escape(" ".join(b[4] for b in next(s for s in SPECS if s["title"]==t["title"])["panels"]))}</p><a href="{html.escape(t["source_url"])}">原始事实来源</a><p>待主生产管线审稿、配音和镜头审核；未批准生产。</p></article>')
    gallery=BATCH/'review.html';gallery.write_text('<!doctype html><meta charset="utf-8"><title>首批视频素材</title><style>body{background:#101723;color:#e9f0f6;font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}main{display:grid;grid-template-columns:repeat(2,1fr);gap:28px}article{padding:20px;background:#1c2c3c;border-radius:16px}video{width:100%;max-height:550px}a{color:#75ddd2}</style><h1>首批视频素材 · 4个新故事</h1><p>16段原创竖屏镜头，4条24秒静音预览。原文、事实证据、旁白稿及素材索引已入文件候选inbox。</p><main>'+''.join(cards)+'</main>', encoding="utf-8")
    feed=dict(schema_version=1,updated_at=now,state='batch_candidate_handoff_complete',source_pool=str(BATCH/'source_pool.json'),
              coverage=str(BATCH/'coverage.json'),catalog=str(BATCH/'candidate_catalog.json'),review=str(gallery),
              candidates=[dict(content_id=r['content_id'],package=str(RESEARCH/'inbox'/r['content_id']/'package.json'),
                               receipt=str(RESEARCH/'inbox'/r['content_id']/'receipt.json'),readiness=r['readiness']) for r in receipts],
              previous_candidate=str(oldpackage),counts=dict(source_pool=12,new_stories=4,previous_stories=1,new_original_clips=16,new_preview_videos=4,
                                                            new_clip_seconds=96,new_facts=sum(len(s['facts']) for s in SPECS),production_jobs=0),
              integration='File inbox delivered; production controller has not consumed or accepted these candidates. Read catalog then package and evidence; main owner controls admission.',
              previews=previews,missing=['Main narrative review','Voice synthesis and alignment','Motion/structure review'],
              isolation=dict(production_queue_writes=0,production_controls_changed=0,accelerator_jobs=0,other_chats_messaged=0,social_publications=0))
    write_json(BATCH/'handoff.json',feed)
    archive=BATCH/'previous_handoff';archive.mkdir(exist_ok=True)
    for name in ('handoff.json','progress.json'):
        original=RESEARCH/name
        if original.exists() and not (archive/name).exists():shutil.copy2(original,archive/name)
    write_json(RESEARCH/'candidate_feed.json',feed)
    write_json(RESEARCH/'handoff.json',feed)
    write_json(RESEARCH/'progress.json',dict(updated_at=now,state=feed['state'],handoff=str(RESEARCH/'handoff.json'),counts=feed['counts'],
                                              collection_complete=True,research_ready=True,production_ready=False,production_approved=False))
    print(json.dumps(feed['counts'],ensure_ascii=False),flush=True)


if __name__=='__main__':main()
