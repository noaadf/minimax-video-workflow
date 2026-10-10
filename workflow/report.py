"""Build a local, inspectable production dashboard from durable receipts."""
import html
import json
import os
from pathlib import Path
from .queue import Queue


def build(root):
    root=Path(root).resolve();q=Queue(root/'queue.sqlite');tasks=q.status()
    jobs={}
    for t in tasks:jobs.setdefault(t['job_id'],[]).append(t)
    cards=[]
    def link(path,label):
        return '<a href="'+html.escape(os.path.relpath(path,root),quote=True)+'">'+html.escape(label)+'</a>'
    for jid, rows in jobs.items():
        table=''.join('<tr>'+''.join('<td>'+html.escape(str(t[k] or ''))+'</td>' for k in ('stage','capability','status','attempts'))+'</tr>' for t in rows)
        exports=[json.loads(t['result']) for t in rows if t['stage']=='export' and t['status']=='succeeded']
        media=''
        if exports:
            e=exports[-1]
            media='<div class="media"><video controls preload="metadata" src="'+html.escape(os.path.relpath(e['video_path'],root),quote=True)+'"></video><img alt="封面" src="'+html.escape(os.path.relpath(e['cover_path'],root),quote=True)+'"></div>'
            media+='<p>'+link(e['video_path'],'视频')+' · '+link(e['cover_path'],'封面')+' · '+link(e['publication_path'],'发布文案')+'</p>'
        quality=[json.loads(t['result']) for t in rows if t['stage']=='quality' and t['status']=='succeeded']
        findings=''
        if quality:
            report=quality[-1]
            findings='<p><strong>内容状态：'+html.escape(report['editorial_status'])+'</strong> '+link(report['report_path'],'完整质检')+'</p>'
            findings+='<ul>'+''.join('<li>'+html.escape(f['issue'])+'</li>' for f in report.get('editorial',{}).get('review',{}).get('findings',[]))+'</ul>'
        cards.append('<section><h2>'+html.escape(jid)+'</h2>'+media+findings+'<details><summary>任务与重试记录</summary><table><tr><th>阶段</th><th>资源组</th><th>状态</th><th>次数</th></tr>'+table+'</table></details></section>')
    additions=''
    for name,label in [('source_live_acceptance/catalog.json','真实来源与事实证据'),('batch_status.json','批次排产状态'),('repair_acceptance/acceptance.json','选择性修复集成验收'),('workflow_v2_acceptance.json','本轮验收边界')]:
        if (root/name).exists():additions+='<p>'+link(root/name,label)+'</p>'
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>国之重器 · 生产流水线</title>
<style>body{max-width:1080px;margin:40px auto;padding:0 24px;background:#11151c;color:#d9e0e8;font:16px/1.7 system-ui}h1,h2{color:#ffd35a}small{color:#91a0b2}section,.intro{background:#1b2330;padding:24px;border-radius:16px;margin:24px 0}a{color:#91c8ff}code{color:#ffd35a}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #344052;text-align:left;padding:7px}.media{display:flex;gap:20px}.media>*{width:calc(50% - 10px);max-height:560px;object-fit:contain;background:#101318;border-radius:8px}li{margin:8px 0}</style>
<h1>国之重器 · 生产流水线</h1><small>首轮验收 / 本地控制器 / 尚未连接已验证的昇腾 H3 服务</small>
<div class="intro"><strong>事实包 → 文案分镜 → 分镜队列 → 男声与音效 → 黄色字幕与封面 → 技术检查 + 视觉初筛 → 交付</strong>
<p>下方成片复用既有素材，验证自动后期，不能当作昇腾新生成效果或扩卡吞吐量。</p>
<ul><li>本地 SQLite 保存任务与心跳；同设备组互斥。模型调用结果未知时锁住资源组，等待核对，避免重复占卡。</li><li>来源采集支持域名白名单、日期窗口、原文快照、逐条引文匹配及模型语义核验；同一来源自动去重排产。四类叙事模板：工程紧张、科学揭秘、产品形态、国际连接。每条独立的连续性设定和评论问题。</li><li>解说先实测时长，超时句最多两轮自动缩写并重新配音，字幕同步更新。旁白末句后保留至少 0.9 秒；单独检查末句下的音乐响度。封面和大号黄色字幕自动生成。</li><li>视觉初筛只检查抽帧，不能证明完整运动、发音与新闻事实准确。可定位的画面问题进入最多两轮选择性修复，复用已通过镜头和配音。事实或未分类问题保留待处理；不自动发布。</li></ul>
<h2>运行</h2><pre><code>python3 -m workflow collect workflow/examples/source_collection.json --out output/production/sources
python3 -m workflow batch output/production/sources/catalog.json workflow/examples/batch_live_acceptance.json
python3 -m workflow plan workflow/examples/bridge_brief.json --out /tmp/new-plan.json
python3 -m workflow submit /tmp/new-plan.json --backend /path/to/verified-backend.json
.venv-workflow/bin/python -m workflow run --workers 3 --capabilities cpu,ascend-group-0 --auto-repair
python3 -m workflow report</code></pre>
<p>后端配置必须使用已验证的 SSH 端口、设备组和模型服务。本地队列不可放共享 SFS；当前使用单控制器；来源支持性核验不等于独立证实事件。真实官方报道的抓取、事实提取与引文核验已验收。选择性修复已用模拟生成接口加真实 FFmpeg 合成验收；真实昇腾重画和多节点吞吐仍待接入。</p>
<p>放大顺序：先测单组首次加载和热启动 → 同节点两组并行 → 2 个节点 → 4 个节点。以合格成片/小时、重画率、排队时间为依据扩卡。</p></div>'''+additions+''.join(cards)+'</html>'
    out=root/'index.html';out.write_text(page, encoding="utf-8")
    return out
