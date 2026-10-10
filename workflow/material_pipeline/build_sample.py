"""Build the real drop-tower/Tianwen-2 sample from already archived evidence."""
import json
from pathlib import Path
from .core import content_id, enqueue_candidate, fact_id, legacy_records, sha256, write_json
from .media import probe


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT/'research/material_pipeline'


def main():
    sources = []
    for sid, folder, publisher, published, date_quote, role in [
        ('CNSA', 'a4738ee49678aaa5a817', '国家航天局', '2025-05-29', '发布时间：2025-05-29', 'primary'),
        ('IMECH', 'ad3a57eefaba6c5bf3c5', '中国科学院力学研究所', '2025-06-04', '发布时间：2025-06-04', 'primary'),
        ('CCTV', 'bbfff1a6302922967ca3', '央视网', '2025-05-29', '2025-05-29 19:15', 'secondary')]:
        directory = RESEARCH/'sources'/folder
        old = json.loads((directory/'source.json').read_text(encoding="utf-8"))
        body = directory/'source.txt'
        sources.append({'id': sid, 'url': old['url'], 'title': old['title'], 'publisher': publisher,
                        'role': role, 'published': published, 'date_evidence': date_quote,
                        'archive_path': str((directory/'source.html').relative_to(RESEARCH)),
                        'sha256': sha256(directory/'source.html'), 'text_path': str(body.relative_to(RESEARCH)),
                        'text_sha256': sha256(body), 'retrieved_at': old['fetched_at'],
                        'acquisition': 'Direct HTTPS HTML; visible publication line manually checked; raw archive preserved'})
    facts = []
    definitions = [
        ('F1', 'CNSA', '天问二号的任务目标包括对2016HO3探测、取样返回，随后探测311P彗星。',
         '天问二号', '任务目标', '2016HO3采样返回后探测311P', 'planned',
         '天问二号主要任务目标是对小行星2016HO3进行探测、取样并返回地球，此后再对主带彗星311P开展科学探测。'),
        ('F2', 'CNSA', '弱引力天体表面取样是天问二号需要突破的关键技术之一。',
         '天问二号', '关键技术', '弱引力天体表面取样', 'reported', '突破弱引力天体表面取样'),
        ('F3', 'IMECH', '力学所与北京卫星制造厂合作，完成了120余次地基短时微重力试验。',
         '北京落塔天问二号采样试验', '完成次数', '120余次地基短时微重力试验', 'reported',
         '中国科学院力学研究所微重力重点实验室与北京卫星制造厂有限公司合作，利用力学所落塔装备，先后完成了120余次地基短时微重力试验'),
        ('F4', 'IMECH', '试验覆盖采样器、机械臂采样及模拟星壤的微重力特性。',
         '北京落塔天问二号采样试验', '试验范围', '激励采样器、机械臂及模拟星壤', 'reported',
         '对激励采样器微重力采样、机械臂微重力采样及模拟星壤微重力特性等开展了系统试验'),
        ('F5', 'IMECH', '新型落舱的有效载荷质量从90千克提高到160千克。',
         '北京落塔新型大载荷落舱', '有效载荷质量提升', '90kg到160kg', 'reported',
         '将有效载荷质量由原来的90 kg提高到160 kg'),
        ('F6', 'IMECH', '这项地面试验从2020年8月持续至2023年10月。',
         '北京落塔天问二号采样试验', '试验期间', '2020年8月至2023年10月', 'reported',
         '本项试验自2020年8月开始，至2023年10月结束'),
        ('F7', 'CNSA', '天问二号于2025年5月29日1时31分在西昌发射升空。',
         '天问二号', '发射时间地点', '2025-05-29T01:31:00+08:00西昌', 'reported',
         '5月29日1时31分，我国在西昌卫星发射中心用长征三号乙Y110运载火箭，成功将行星探测工程天问二号探测器发射升空。'),
        ('F8', 'CNSA', '2025年发布的任务计划预计返回舱在2027年底着陆地球，不能写成已完成采样返回。',
         '天问二号返回舱', '计划返回时间', '2027年底', 'planned',
         '预计于2027年底着陆地球并完成回收')]
    for fid, sid, text, subject, predicate, obj, modality, quote in definitions:
        source = next(s for s in sources if s['id'] == sid)
        body = (RESEARCH/source['text_path']).read_text(encoding="utf-8"); start = body.index(quote)
        fact = {'id': fid, 'text': text, 'subject': subject, 'predicate': predicate, 'object': obj,
                'modality': modality, 'verification': 'source_supported',
                'evidence': [{'source_id': sid, 'quote': quote, 'start': start, 'end': start+len(quote)}]}
        fact['fingerprint'] = fact_id(fact); facts.append(fact)
    assets = []
    for aid, sid, name, kind, url in [
        ('A1', 'CNSA', 'tianwen2_mission.mp4', 'video', 'https://www.cnsa.gov.cn/n6758823/n6758843/0529twehdh.mp4'),
        ('A2', 'CCTV', 'tianwen2_news.mp4', 'video', sources[2]['url']),
        ('A3', 'IMECH', 'W020250604731467872365_ORIGIN.gif', 'image',
         'https://imech.cas.cn/news/202506/W020250604731467872365_ORIGIN.gif'),
        ('A4', 'IMECH', 'W020250604731468104764_ORIGIN.gif', 'image',
         'https://imech.cas.cn/news/202506/W020250604731468104764_ORIGIN.gif')]:
        path = RESEARCH/'media'/name
        duration = float(probe(path)['format']['duration']) if kind == 'video' else 0
        assets.append({'id': aid, 'source_id': sid, 'url': url, 'path': str(path.relative_to(RESEARCH)),
                       'sha256': sha256(path), 'bytes': path.stat().st_size, 'kind': kind, 'duration': duration,
                       'use': 'research_only', 'rights': {'status': 'unknown', 'owner': next(s['publisher'] for s in sources if s['id']==sid),
                                                         'evidence_path': None,
                                                         'note': '公开可访问不代表可商用。只供私有研究；原片、配乐、照片均未获得转载或商业使用许可。'}})
    shots = json.loads((RESEARCH/'media/tianwen2_index/shots.json').read_text(encoding="utf-8"))
    for shot, observation in zip(shots, [
        '抽帧可见探测器悬停于灰色小天体上方，绿色光束为动画示意。',
        '抽帧可见探测器及向下的条纹状探测示意，不能推断真实采样结果。',
        '抽帧可见探测器接近地球及日心轨道图，属于计划流程动画；该段内可能还有渐变转场。']):
        shot['observation'] = observation
    news = json.loads((RESEARCH/'media/tianwen2_news_index/shots.json').read_text(encoding="utf-8"))
    for shot in news:
        shot['id'] = 'N' + shot['id'][1:]; shot['asset_id'] = 'A2'
    shots += news
    transcript_path = RESEARCH/'media/tianwen2_news_index/transcript.json'
    event = {'entity': '北京落塔天问二号采样试验', 'action': '披露地基试验成果',
             'date': '2025-06-04', 'object': '120余次试验及160kg大载荷落舱'}
    package = {'schema_version': 1, 'content_id': content_id(event),
               'title': '飞向小行星之前，中国团队先把采样器送进落塔', 'event': event,
               'sources': sources, 'facts': facts, 'assets': assets, 'shots': shots,
               'transcript': {'status': 'automatic_unreviewed', 'asset_id': 'A2',
                              'path': str(transcript_path.relative_to(RESEARCH)), 'sha256': sha256(transcript_path),
                              'method': 'faster-whisper-small CPU int8；完整央视报道自动转写，未人工逐句校对',
                              'limitations': ['专名、数字需复核，报道原话不直接迁入原创旁白。',
                                              'A1动画音轨实测约-91dBFS；自动ASR为空，音轨测量见独立日志。']},
               'editorial': {'audience': '普通观众', 'angle': '从地面测试的具体动作，解释星际采样为什么需要提前验证。',
                             'beats': [
                                 {'id': 'B1', 'purpose': '具体问题', 'fact_ids': ['F1','F2'],
                                  'original_narration': '去小行星取一份样品，难的不是伸出机械臂。引力很弱，采样动作该怎样提前验证？',
                                  'visual_plan': '原创弱引力采样示意；不把动画伪装成探测器实拍。'},
                                 {'id': 'B2', 'purpose': '地面办法与行动', 'fact_ids': ['F3','F4','F6'],
                                  'original_narration': '答案的一部分在北京落塔。2020年到2023年，团队做了120多次短时微重力试验，把采样器、机械臂和模拟星壤放进测试。',
                                  'visual_plan': '经授权的实验现场或原创装置示意；自由落体原理补证据后才写。'},
                                 {'id': 'B3', 'purpose': '工程变化与回报', 'fact_ids': ['F5'],
                                  'original_narration': '为了装下试验载荷，他们还研制了新落舱，把有效载荷质量从90千克提高到160千克。',
                                  'visual_plan': '按官方照片核对结构，原创质量对比图；数字由剪辑叠加，避免生成乱码。'},
                                 {'id': 'B4', 'purpose': '回扣与时间边界', 'fact_ids': ['F7','F8'],
                                  'original_narration': '2025年，天问二号发射升空。飞向远方之前，采样的关键动作，已经在地面经历了一轮轮验证。',
                                  'visual_plan': '发射年份明确可见；不出现已采样返回、实时任务进度等未核实说法。'}],
                             'cautions': ['报道日期2025-06-04不同于试验结束时间2023-10，不能包装成2026年刚发生。',
                                          '不使用地表引力百万分之一的估计值，不擅加落塔高度和失重时长。',
                                          '地面试验成功不等于星际采样成功；任务计划与已完成节点分开。',
                                          'CCTV对准卫星轨道的简化表述不进入本包事实。',
                                          '官方图片保留出处，感谢信仅作来源证据，不用于煽情或虚构对白。'],
                             'verification_scope': '官方原文阅读和精确证据位置校验；来源支持不等于独立验证事件。原创旁白仍需主对话叙事审核。'},
               'collection_pending': [], 'production_pending': ['全部候选镜头边界和内容复核', '自动转写专名数字复核',
                                                                  '原片版权未授权，生产须原创素材或另取许可',
                                                                  '落塔工作原理若用于旁白须补官方原理来源',
                                                                  '新故事分镜与旁白主对话审核'],
               'production_asset_plan_reviewed': False}
    paths = list((ROOT/'research/deadline30_20261002').glob('catalog_?.json'))
    paths += list((ROOT/'research').glob('scale*/catalog.json'))
    existing = legacy_records(paths)
    receipt = enqueue_candidate(package, RESEARCH, existing)
    write_json(RESEARCH/'legacy_dedup_scope.json', {'catalogs': [str(p.relative_to(ROOT)) for p in paths],
                                                  'records': len(existing), 'production_database_read': False})
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
