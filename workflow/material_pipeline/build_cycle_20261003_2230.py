"""One source-backed research candidate and an original mechanism insert.

The insert is not a full film or a recording of the team's verification run.
No external model calls, production queue writes or accelerator execution.
"""
import datetime as dt
import json
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .core import content_id, fact_id, sha256, validate, write_json, legacy_records, deduplicate

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'research/material_pipeline'
C=R/'monitor/runs/20261003T223051_106950'
FONT='/System/Library/Fonts/STHeiti Medium.ttc'


def frame(t):
    im=Image.new('RGB',(720,1280),'#ece8df');d=ImageDraw.Draw(im)
    def text(x,y,s,n=28,fill='#25303a'):
        d.text((x,y),s,font=ImageFont.truetype(FONT,n),fill=fill)
    text(48,70,'证明里的每一步，怎样接起来',35)
    text(48,123,'原创机制插入片段 · 不是实际证明图',22,'#625f59')
    nodes=[(150,310,'前置结论'),(570,310,'前置结论'),(150,530,'中间步骤'),(570,530,'中间步骤'),(360,740,'待检验命题')]
    edges=[(0,2),(1,3),(2,4),(3,4)]
    for i,(a,b) in enumerate(edges):
        x,y,_=nodes[a];xx,yy,_=nodes[b];progress=max(0,min(1,(t-i*.7)/1.8))
        d.line((x,y,xx,yy),fill='#c2b8aa',width=4)
        if progress:d.line((x,y,x+(xx-x)*progress,y+(yy-y)*progress),fill='#a06834',width=7)
    for x,y,label in nodes:
        d.rounded_rectangle((x-90,y-38,x+90,y+38),18,fill='#fffaf1',outline='#8b8174',width=2)
        text(x-65,y-15,label,25)
    # Two routes illustrate independent checking, not an animated success log.
    for x,label in [(170,'检验路径一'),(550,'检验路径二')]:
        d.line((360,790,x,925),fill='#8b8174',width=3)
        d.ellipse((x-64,925-64,x+64,925+64),fill='#d8d6ce',outline='#716f66',width=3)
        phase=t*1.4
        d.line((x-35*math.cos(phase),925-35*math.sin(phase),x+35*math.cos(phase),925+35*math.sin(phase)),fill='#596963',width=6)
        text(x-68,1010,label,23)
    text(48,1120,'这里只解释依赖与检查的关系',26)
    text(48,1163,'不表示本轮重跑 Lean / Comparator',22,'#625f59')
    return im


def main():
    doc=next(x for x in json.loads((C/'supplementary/proof_sources.json').read_text(encoding="utf-8"))
             if x['url']=='https://frenzymath.com/news/poincare-formalization/')
    source_text=Path(doc['text_path']);body=source_text.read_text(encoding="utf-8");folder=source_text.parent
    assert 'Sep 28, 2026' in body
    event={'entity':'北京大学BICMR FrenzyMath庞加莱猜想形式化团队',
           'action':'公布既有数学证明的Lean形式化与核验阶段报告','date':'2026-09-28',
           'object':'数学家和AI开发者协作补齐基础理论并报告Lean及Comparator检查'}
    cid=content_id(event);out=C/'media/poincare';out.mkdir(parents=True,exist_ok=True)
    claims=[
        ('团队说明，这次工作主要沿用Morgan与田刚整理的佩雷尔曼既有证明。','证明来源','既有证明的形式化',
         'Our formalization mainly follows the Morgan–Tian exposition of Perelman’s proof.'),
        ('团队称，为完成这项形式化，需要在项目内补齐尚未形式化的前置理论。','具体难题','补齐前置理论',
         'Much of the foundations in all these areas had not yet been formalized, requiring all prerequisite theory to be formalized within this project.'),
        ('报告称，三名有数学背景的AI开发者搭建并运行智能体流程，志愿数学家参与，使用公开商用大模型。','团队实际行动','开发者搭建运行流程与数学家协作',
         'The effort brought together three AI developers with mathematical backgrounds, who designed, built and ran the agentic pipelines, and several volunteer mathematicians',
         'all working with publicly available commercial large language models.'),
        ('团队报告，主要Lean 4形式化工作在半个月内完成。','阶段用时','主要形式化工作半个月',
         'In our work, we completed the main body of the formalization in Lean 4 in half a month.'),
        ('团队9月28日公告称完整形式化已完成，并报告通过Lean build与Comparator；本轮未独立重跑。','团队报告核验','形式化完成及两类检验',
         'We have produced a complete formalization of the Poincaré Conjecture in Lean 4, to our knowledge the first to be verified by both Lean build and Comparator'),
        ('团队坦言，现有代码质量距离可复用的高质量基础设施仍有差距，改进属于后续工作。','保留限制','现有代码质量尚不足以成为可复用基础设施',
         'Our current code quality is still far from achieving this.',
         'toward building reusable, high-quality infrastructure.')]
    facts=[]
    for i,(claim,predicate,obj,*quotes) in enumerate(claims,1):
        evidence=[]
        for quote in quotes:
            assert quote in body and len(quote)<=200
            start=body.index(quote);evidence.append(dict(source_id='S1',quote=quote,start=start,end=start+len(quote)))
        f=dict(id=f'F{i}',text=claim,subject=event['entity'],predicate=predicate,object=obj,modality='reported',verification='source_supported',evidence=evidence)
        f['fingerprint']=fact_id(f);facts.append(f)
    source=dict(id='S1',url=doc['url'],title='Formalization of the Poincaré Conjecture',publisher='FrenzyMath，AI4M Team BICMR@PKU',role='primary',
        published='2026-09-28',date_evidence='Sep 28, 2026',archive_path=str((folder/'source.html').relative_to(R)),sha256=sha256(folder/'source.html'),
        text_path=str(source_text.relative_to(R)),text_sha256=sha256(source_text),retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        acquisition='Full original dated team announcement. English visible date retained verbatim. Supplemental current repository and verification evidence archived; results team-reported, not rerun.')
    beats=[
        ('为什么已经证明，还要再检查',['F1','F2'],'一道定理已经被证明，机器为什么还需要检查？北大团队这次要把已有证明里的每一步，都变成能被程序逐步核对的代码。','正式场景待主管线创作：旧证明与依赖层级相连的工作过程。原创演绎须披露；不冒充真实实验录像或发明新证明。'),
        ('难点在缺失的基础理论',['F2'],'难点还在地基。证明需要的许多基础理论，机器库里还没有，团队得先把这些前置结论补齐。','可插入本包8秒原创依赖与检验关系示意；它不是真实证明蓝图，也不表示已执行核验。'),
        ('人搭流程，数学家与模型协作',['F3'],'三名有数学背景的AI开发者搭起智能体流程，多位志愿数学家一起参与，使用的是公开商用大模型。','实际人物/团队行动依据原公告；如创作人物演绎，使用匿名通用角色且标注，不编造私人对话、使用者故事或具体个体贡献。'),
        ('公布完成阶段，同时保留限制',['F4','F5','F6'],'团队9月28日报告，主要形式化工作在半个月内完成，并通过两类检查。但他们也坦言，代码还需要改进，才能成为好用的基础工具。','结果采用团队公告和原样保存的核验记录归因；字幕写团队报告，本轮未独立重跑。结尾保留代码质量和后续改进边界；不得虚构成功日志或声称首次证明庞加莱。')]
    editorial=dict(audience='普通观众',angle='已有数学证明，怎样交给机器逐步检查',beats=[dict(id=f'B{i}',purpose=a,fact_ids=f,original_narration=n,visual_plan=v) for i,(a,f,n,v) in enumerate(beats,1)],
        cautions=['事件和原公告日期2026-09-28；新智元转载2026-09-30，二者区分。','这是既有证明的形式化，不是AI首次证明庞加莱猜想。','团队报告通过核验；本轮只阅读公开说明与保存的记录，没有独立运行Lean、Comparator或Nanoda。','不采用全球首个、零漏洞或可靠性保证；完整数学语义与声明忠实性仍需专家审查。','3.2百万行和3万美元是团队统计，此稿不以代码量证明质量，也不将统计写成独立测量。','仅提供一段机制插入片段，整部视频仍需主管线真实画面、声音和预检；不能全片套用示意图。'],
        verification_scope='Exact original-source spans, live original announcement/current README and local verification evidence read. No independent proof verification; no manufactured user/team story.')
    # Check legacy event/source overlap before any media work.
    stub=dict(content_id=cid,event=event,title='已有证明，怎样让机器逐步检查',sources=[source],facts=facts,assets=[])
    catalogs=[]
    for path in (ROOT/'research').glob('**/catalog_*.json'):
        document=json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document,dict) and isinstance(document.get('topics'),list):catalogs.append(path)
    prior=legacy_records(catalogs)
    duplicate=deduplicate(stub,prior)
    if duplicate['status']!='clear_in_scanned_scope':raise ValueError('Legacy overlap requires review: '+str(duplicate))
    video=out/'proof_dependency_insert.mp4'
    if video.exists():raise ValueError('Do not rerender a completed insert')
    process=subprocess.Popen(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','720x1280','-r','24','-i','-','-an','-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],stdin=subprocess.PIPE)
    for i in range(8*24):process.stdin.write(frame(i/24).tobytes())
    process.stdin.close()
    if process.wait()!=0:raise ValueError('CPU insert encoding failed')
    keyframes=[]
    for at in (.5,4,7.5):
        p=out/f'keyframe_{at}.jpg';subprocess.run(['ffmpeg','-v','error','-ss',str(at),'-i',str(video),'-frames:v','1',str(p)],check=True)
        keyframes.append(dict(at=at,path=str(p.relative_to(R)),sha256=sha256(p)))
    write_json(out/'original_provenance.json',dict(author='This material chat, original Python/Pillow CPU drawing',purpose='8-second supplementary explanation of proof dependency and two checking routes',source_code=str(Path(__file__).resolve()),source_code_sha256=sha256(__file__),copied_source_media=False,actual_verification_footage=False,full_film=False))
    asset=dict(id='A1',source_id='S1',url=source['url'],path=str(video.relative_to(R)),sha256=sha256(video),bytes=video.stat().st_size,kind='video',duration=8.,use='production',
        rights=dict(status='original',owner='本素材任务原创机制示意',evidence_path=str((out/'original_provenance.json').relative_to(R)),note='Original drawing/animation; source URL is factual reference, no source images, footage or audio copied. Insert only; not original proof graph or test output.'))
    package=dict(schema_version=1,content_id=cid,title=stub['title'],event=event,sources=[source],facts=facts,assets=[asset],
        shots=[dict(id='S001',asset_id='A1',start=0.,end=8.,status='candidate',observation=None,keyframes=keyframes,boundary_method='One authored 8s insert; three decoded samples; not a full scene plan')],
        transcript=dict(status='not_applicable',asset_id='A1',path=None,sha256=None,method='Silent original CPU mechanism insert',limitations=['Narration is authored draft, not synthesized voice or measured alignment.']),
        editorial=editorial,collection_pending=[],production_pending=['主管线v2来源选题与source_story_fidelity叙事审核','正式人物/科研场景、整段配音、字幕、整板与粗剪预检；8秒示意仅为补充'],production_asset_plan_reviewed=False)
    from jsonschema import Draft202012Validator,FormatChecker
    Draft202012Validator(json.loads((ROOT/'workflow/material_pipeline/package.schema.json').read_text(encoding="utf-8")),format_checker=FormatChecker()).validate(package)
    validate(package,R);write_json(C/'drafts'/f'{cid}.json',package)
    write_json(C/'supplementary/source_story_precheck.json',dict(content_id=cid,kind='research_experiment',actors='FrenzyMath AI developers and volunteer mathematicians',problem=facts[1]['evidence'],action=facts[2]['evidence'],outcome=facts[4]['evidence'],decision='Human source screening supports a real case; production model v2 review still pending',production_ready=False))
    print(json.dumps({'draft':str(C/'drafts'/f'{cid}.json'),'media':str(video),'content_id':cid,'facts':len(facts),'clip_seconds':8,'enqueued':False},ensure_ascii=False))


if __name__=='__main__':main()
