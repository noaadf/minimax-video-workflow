"""Read-only rollout and storyboard asset status for the existing production UI."""
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

def snapshot(root):
    root=Path(root).resolve();folder=root/'storyboard_rollout'
    try:state=json.loads((folder/'status.json').read_text(encoding="utf-8"))
    except (OSError,ValueError):state={'state':'not_deployed'}
    progress={}
    if state.get('job_id') and (root/'queue.sqlite').is_file() and state.get('state') in ('generating','composing','preflight_running') and not state.get('film_ready'):
        try:
            with sqlite3.connect((root/'queue.sqlite').as_uri()+'?mode=ro',uri=True,timeout=2) as c:
                tasks=c.execute('SELECT stage,status FROM tasks WHERE job_id=?',(state['job_id'],)).fetchall()
            shots=[status for stage,status in tasks if stage=='shot']
            progress={'done':shots.count('succeeded'),'total':len(shots),'running':shots.count('running')}
            if shots and not any(status in ('failed','blocked','reconcile') for _,status in tasks):
                phase='composing' if all(s=='succeeded' for s in shots) else 'generating' if ('story_preflight','succeeded') in tasks else 'preflight_running'
                state={**state,'state':phase,'next_step':{'composing':'合成新镜头并检查完整成片',
                    'generating':f"已完成 {progress['done']}/{progress['total']} 个镜头，{progress['running']} 个正在生成；之后逐镜检查动作与连续性",
                    'preflight_running':'生产入口核对冻结素材与粗剪证据，通过后生成动作镜头'}[phase]}
        except sqlite3.Error:pass
    def link(path):
        path=Path(path).resolve()
        return '/media/'+quote(str(path.relative_to(root)),safe='/') if path.is_relative_to(folder) and path.is_file() else None
    frames=[{'name':p.stem,'url':url} for p in sorted((folder/'keyframes').glob('*.png'),
        key=lambda p:(p.stem.rsplit('_',1)[0],0 if p.stem.endswith('_start') else 1)) if (url:=link(p))]
    # A selected film must never inherit another product's global storyboard.
    if state.get('job_id'):
        frames=[]
        try:
            with sqlite3.connect((root/'queue.sqlite').as_uri()+'?mode=ro',uri=True,timeout=2) as c:
                row=c.execute('SELECT manifest FROM jobs WHERE id=?',(state['job_id'],)).fetchone()
            manifest=json.loads(row[0]) if row else {}
            for shot in manifest.get('shots',[]):
                for i,path in enumerate(shot.get('reference_images',[])):
                    if isinstance(path,str) and (url:=link(path)):
                        frames.append({'name':shot['id']+('_start' if i==0 else '_end'),'url':url})
        except (sqlite3.Error,ValueError,TypeError):
            frames=[]
    labels={'not_deployed':'剧情管线尚未启用','preparing_rollout':'正在准备上线','deployed':'新剧情管线已启用',
            'preflight_running':'正在检查分镜粗剪','preflight_needs_revision':'分镜粗剪需要修改',
            'preflight_unavailable':'粗剪审核服务暂不可用',
            'ready_for_admission':'粗剪检查通过，等待生产入口接纳',
            'generating':'正在生成新动作镜头','film_review_required':'新片待完整检查',
            'composing':'正在合成并检查成片',
            'film_needs_revision':'新片画面需要返修',
            'film_ready':'新版成片已完成','awaiting_controller':'等待现有生产入口'}
    result={'at':time.time(),'state':state.get('state'),'label':labels.get(state.get('state'),'正在处理'),
            'deployed':bool(state.get('deployed')),'policy':state.get('policy','storyboard_v1'),
            'keyframes':frames,'keyframes_count':len(frames),'film_ready':state.get('film_ready') is True,
            'next_step':state.get('next_step','完成首尾关键帧与粗剪检查'),
            'notes':state.get('public_notes',[]),'preview_url':None,'preview_kind':None,'shot_progress':progress}
    for key,kind in [('film_path','成片'),('preview_path','实际镜头粗剪 · 待完整检查' if state.get('preview_is_motion') else '分镜粗剪 · 静帧动作示意')]:
        if state.get(key):
            url=link(state[key])
            if url:
                result.update(preview_url=url,preview_kind=kind);break
    return result
