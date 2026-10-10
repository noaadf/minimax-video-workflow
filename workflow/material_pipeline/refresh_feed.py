"""Refresh the cumulative file handoff after new immutable material intake."""
import datetime as dt
import json
from pathlib import Path
from .core import sha256, validate, write_json


def bridge_if_enabled(root):
    path=root/'production_bridge.json'
    if not path.exists():return None
    policy=json.loads(path.read_text(encoding="utf-8"))
    if policy.get('enabled') is not True:return None
    from .production_bridge import run
    result=run(root,Path(policy['production_root']),limit=policy.get('max_plans_per_cycle',2))
    return {'state':result['state'],'processed':result.get('processed',[]),
            'status_path':str(root/'production_status.json')}


def refresh(root):
    root=Path(root).resolve();rows=[];withdrawn=[]
    dispositions_path=root/'editorial_dispositions.json'
    dispositions=json.loads(dispositions_path.read_text(encoding="utf-8")) if dispositions_path.exists() else {}
    for path in sorted((root/'inbox').glob('*/package.json')):
        package=json.loads(path.read_text(encoding="utf-8"));validate(package,root)
        disposition=dispositions.get(package['content_id'])
        if disposition and disposition.get('status')=='withdrawn':
            if disposition.get('package_sha256')!=sha256(path) or not disposition.get('reason'):
                raise ValueError('Editorial withdrawal must bind the exact package and a reason')
            withdrawn.append(dict(content_id=package['content_id'],title=package['title'],
                                  package=str(path),**disposition))
            continue
        receipt_path=path.with_name('receipt.json')
        receipt=json.loads(receipt_path.read_text(encoding="utf-8"))
        rows.append(dict(content_id=package['content_id'],title=package['title'],package=str(path),
                         package_sha256=sha256(path),receipt=str(receipt_path),readiness=receipt['readiness'],
                         handoff_kind='research_candidate_unapproved',
                         story_eligibility='Requires separate source-only story v2 review; fact receipt is insufficient'))
    feed_path=root/'candidate_feed.json'
    old=json.loads(feed_path.read_text(encoding="utf-8")) if feed_path.exists() else {}
    if (old.get('candidates',[])==rows
            and old.get('withdrawn',[])==withdrawn):
        return {'state':'unchanged','candidates':len(rows),'production_bridge':bridge_if_enabled(root)}
    now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    archive=root/'monitor/handoffs'/now.replace(':','-')
    archive.mkdir(parents=True)
    for name in ('candidate_feed.json','handoff.json'):
        path=root/name
        if path.exists():(archive/name).write_bytes(path.read_bytes())
    feed={**old,'updated_at':now,'state':'cumulative_candidate_handoff','candidates':rows,
          'withdrawn':withdrawn,'withdrawn_count':len(withdrawn),
          'total_candidate_count':len(rows),'integration':'Immutable file inbox; explicitly enabled production_bridge receives and reviews candidates; main owner controls storyboard, admission, approval and scheduling'}
    feed['candidate_count_scope']='Research archives awaiting separate editorial/production gates; this count does not mean approved stories or production jobs'
    for name in ('candidate_feed.json','handoff.json'):write_json(root/name,feed)
    return {'state':'updated','candidates':len(rows),'entrypoint':str(feed_path),'production_bridge':bridge_if_enabled(root)}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[2]/'research/material_pipeline'
    print(json.dumps(refresh(root),ensure_ascii=False))
