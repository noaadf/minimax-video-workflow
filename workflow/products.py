"""Product projection: repairs stay with their original film, jobs remain unchanged."""
import json,re,sqlite3
from pathlib import Path
from urllib.parse import quote
from .dashboard_actions import catalog,repair_requests


CHAIN_SUFFIX = re.compile(r'-(?:ref|labels|continuity|continuous|topology|closing|e)-[0-9a-f]{6,64}|-conditioned\d+(?:-audiomute)?|-repair\d+-[0-9a-f]{6,64}|-final(?=-|$)')


def suffix_chain(jid):
    """Parse known legacy suffixes without trimming arbitrary hyphenated IDs."""
    matches = list(CHAIN_SUFFIX.finditer(jid))
    if not matches:
        return jid, []
    first = matches[0].start()
    cursor = first
    tokens = []
    for match in matches:
        if match.start() != cursor:
            return jid, []  # Unknown middle token: do not guess product identity.
        tokens.append(match.group()[1:])
        cursor = match.end()
    return (jid[:first], tokens) if cursor == len(jid) else (jid, [])


def product_id(jid,plans):
    seen=set()
    while jid in plans and jid not in seen:
        seen.add(jid)
        parent=plans[jid].get('product_id') or plans[jid].get('root_job_id') or plans[jid].get('parent_job_id') or plans[jid].get('repair_parent_id')
        if not parent:break
        jid=parent
    # Legacy pilot IDs predate explicit product identity; match a complete dated root.
    m=re.match(r'^(pilot6_(?:bridge|east|matext)_[ab]_\d{8})(?:-|$)',jid)
    return m.group(1) if m else suffix_chain(jid)[0]


def read_cards(root):
    """Read-only card projection; legacy jobs remain available during rollout."""
    cards = {}
    for path in sorted((Path(root) / 'runs').glob('*/card.json')):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                continue
            card = json.loads(path.read_text(encoding="utf-8"))
            if card.get('schema_version') != 1 or card.get('product_id') != path.parent.name:
                continue
            history = card.get('versions', [])
            if not isinstance(history, list):
                continue
            # Reject unsafe directory pointers instead of following them.
            if any(v.get('directory') != f"versions/{v.get('version')}" or
                   type(v.get('version')) is not int or v['version'] < 1 for v in history):
                continue
            if (not history or not isinstance(card.get('title'), str) or
                    any(not isinstance(v.get('job_id'), str) or
                        not isinstance(v.get('repair_reason'), str) or
                        not isinstance(v.get('video_sha256'), str) or
                        type(v.get('created_at')) not in (int, float) or
                        not isinstance(v.get('acceptance'), dict) or
                        not isinstance(v['acceptance'].get('conclusion'), str) for v in history)):
                continue
            current = card.get('current_version')
            if current is not None and not any(v['version'] == current and
                    v['acceptance'].get('ready_for_delivery') is True for v in history):
                continue
            cards[card['product_id']] = {**card, 'card_path': str(path)}
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return cards


def _product_groups(jobs, plans, items, cards):
    groups={}
    for job in jobs:
        key=product_id(job['id'],plans)
        groups.setdefault(key,[]).append(job)
    for item in items:
        key=product_id(item['id'],plans)
        if not any(j['id']==item['id'] for j in groups.get(key,[])):
            groups.setdefault(key,[]).append({'id':item['id'],'title':item['title'],'status':'completed','created_at':0})
    for key, card in cards.items():
        for version in card['versions']:
            if not any(j['id'] == version['job_id'] for j in groups.get(key, [])):
                groups.setdefault(key, []).append({'id': version['job_id'], 'title': card['title'],
                                                   'status': 'completed', 'created_at': version['created_at']})
    return groups


def _current_product(current, key, byid, cards, signed):
    card = cards.get(key)
    effective = next((v for v in card['versions'] if v['version'] == card.get('current_version')), None) if card else None
    if card:
        # Card identity wins over the age/order of unrelated legacy jobs.
        current = byid.get(effective['job_id']) if effective else None
        if current and current['video_sha256'] != effective['video_sha256']:
            current = None
    approval=signed.get(key)
    if approval:current=byid.get(approval['job_id'])
    return current, card, effective, approval


def _product_assessment(current, effective, assessments, receipts):
    assessment=assessments.get(current['video_sha256'],{}) if current else {}
    # Only the same job's successful, SHA-bound export can carry its queue assessment.
    if current and not assessment:
        receipt=receipts.get(current['id'],{})
        export=receipt.get('export',{});quality=receipt.get('quality',{})
        if (export.get('sha256',{}).get(export.get('video_path'))==current['video_sha256']
            and quality.get('passed') is True):
            policy=quality.get('delivery_policy',{})
            if isinstance(policy,dict):assessment=policy
    if current and effective and not assessment:
        assessment = effective['acceptance'].get('delivery_policy', {})
    return assessment


def _card_history(row, card, current, effective, byid, media):
    row['card_path'] = card['card_path']
    row['current_version'] = card['current_version']
    row['version_history'] = card['versions']
    row['repair_reason_chain'] = [v['repair_reason'] for v in card['versions']]
    card_versions = []
    for version in card['versions']:
        jid = version['job_id']
        conclusion = {'validated_delivery': '已验收交付',
                      'historical_unverified': '历史导入·未重新验收'}.get(
                          version['acceptance']['conclusion'], version['acceptance']['conclusion'])
        # Existing products.html renders status: expose reason there too.
        card_versions.append({'id': jid, 'version': version['version'],
            'status': f"{conclusion} · 返修原因：{version['repair_reason']}",
            'created_at': version['created_at'], 'repair_reason': version['repair_reason'],
            'directory': version['directory'], 'video_sha256': version['video_sha256'],
            'acceptance': version['acceptance'],
            'video_url': media(jid) if jid in byid and byid[jid]['video_sha256'] == version['video_sha256'] else None,
            'current': bool(current and effective and version['version'] == effective['version'] and
                            current['id'] == jid and current['video_sha256'] == version['video_sha256'])})
    return card_versions


def _append_initial_products(root, result):
    batch=root/'production_first_20261002/status.json'
    briefs=root/'pilot_six_20261001/briefs.json'
    if batch.exists() and briefs.exists():
        progress=json.loads(batch.read_text(encoding="utf-8"));entries={x['id']:x for x in progress.get('items',[])}
        for i,brief in enumerate(json.loads(briefs.read_text(encoding="utf-8"))[:6]):
            jid=f'production_first_20261002_{i+1}'
            if any(p['id']==jid for p in result):continue
            entry=entries.get(jid,{})
            failed=entry.get('state','').endswith('failed')
            result.append({'id':jid,'title':brief['topic'],'pilot':False,'production':True,'status':'需要处理 · 本条已隔离' if failed else '迭代中 · 准备文案和分镜','status_reason':entry.get('error','首批试产；文案完成后自动进入镜头生成。'),'current':None,'versions':[],'requests':[],'active_count':0,'can_approve':False,'approved':False,'approval':None})


def _product_activity(key, versions, plans, requests, current, effective, assessments, receipts):
    notes=[r for r in requests if product_id(r['job_id'],plans)==key]
    active=[v for v in versions if v['status'] in ('running','queued')]
    pending=any(r['status'] in ('pending','running','processing') for r in notes)
    assessment = _product_assessment(current, effective, assessments, receipts)
    ready=bool(current and assessment.get('ready_for_delivery') is True and not assessment.get('blockers') and not active and not pending)
    return notes, active, pending, ready


def _product_status(approval, pending, active, ready):
    status='已审批定稿' if approval else '迭代中 · 返修待处理' if pending else '迭代中 · 正在处理' if active else '已完成迭代 · 待你审批' if ready else '待检查 · 尚未完成迭代'
    reason='你已批准此版本；停止自动返修，导出保持此定稿。' if approval else '完整成片通过程序检查和内容放行，无待处理返修。' if ready else '完成处理和检查后，才能审批定稿。'
    return status, reason


def _review_indexes(root, plans):
    assessments={}
    for path in root.glob('*/throughput_policy_acceptance.json'):
        for row in json.loads(path.read_text(encoding="utf-8")).get('items',[]):
            assessments[row.get('video_sha256')]=row
    items=catalog(root);byid={x['id']:x for x in items};selected={}
    for p in root.glob('*/latest_review_bundle.json'):
        for row in json.loads(p.read_text(encoding="utf-8")).get('videos',[]):selected[product_id(row['id'],plans)]=row['id']
    return assessments, items, byid, selected


def products(root,jobs):
    root=Path(root);plans={};receipts={};cards=read_cards(root)
    with sqlite3.connect(root/'queue.sqlite') as c:
        for jid,raw in c.execute('SELECT id,manifest FROM jobs'):plans[jid]=json.loads(raw)
        for jid,stage,raw in c.execute("SELECT job_id,stage,result FROM tasks WHERE status='succeeded' AND stage IN ('quality','export')"):
            receipts.setdefault(jid,{})[stage]=json.loads(raw or '{}')
    from .product_approval import approvals
    with sqlite3.connect(root/'queue.sqlite') as c: signed=approvals(c)
    assessments, items, byid, selected = _review_indexes(root, plans)
    requests=repair_requests(root)
    groups = _product_groups(jobs, plans, items, cards)
    result=[]
    for key,versions in groups.items():
        versions.sort(key=lambda j:j.get('created_at',0))
        current=byid.get(selected.get(key)) or next((byid[v['id']] for v in reversed(versions) if v['id'] in byid),None)
        current, card, effective, approval = _current_product(current, key, byid, cards, signed)
        notes, active, pending, ready = _product_activity(key, versions, plans, requests, current, effective, assessments, receipts)
        status, reason = _product_status(approval, pending, active, ready)
        def media(jid):return '/product-media/'+quote(jid,safe='')
        result.append({'id':key,'title':current['title'] if current else versions[-1]['title'],'production':key.startswith('production_'),'pilot':bool(re.fullmatch(r'pilot6_(?:bridge|east|matext)_[ab]_\d{8}',key)),'status':status,'status_reason':reason,'can_approve':ready and not approval,'approved':bool(approval),'approval':approval,'current':dict(current,video_url=media(current['id'])) if current else None,'versions':[{'id':v['id'],'status':v['status'],'created_at':v.get('created_at',0),'video_url':media(v['id']) if v['id'] in byid else None,'current':bool(current and v['id']==current['id'])} for v in versions],'requests':notes,'active_count':len(active)})
        if card:
            row = result[-1]
            card_versions = _card_history(row, card, current, effective, byid, media)
            stored_jobs = {v['id'] for v in card_versions}
            row['versions'] = card_versions + [v for v in row['versions'] if v['id'] not in stored_jobs]
    _append_initial_products(root, result)
    from .production_control import withdrawn
    result=[p for p in result if not withdrawn(root,p['id'],plans)]
    return sorted(result,key=lambda p:(not p.get('production',False),not p['pilot'],p['id']))
