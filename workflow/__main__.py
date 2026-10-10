import argparse
import json
from pathlib import Path
from .plan import generate
from .queue import Queue
from .runner import drain, submit


def main():
    parser = argparse.ArgumentParser(description='Short-video production controller; CPU queue stays on local disk.')
    parser.add_argument('--root',default='output/production')
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('plan');p.add_argument('brief');p.add_argument('--out',required=True)
    p = sub.add_parser('story-check');p.add_argument('manifest')
    p = sub.add_parser('submit');p.add_argument('manifest');p.add_argument('--backend')
    p = sub.add_parser('run');p.add_argument('--workers',type=int,default=2);p.add_argument('--capabilities',default='cpu');p.add_argument('--auto-repair',action='store_true');p.add_argument('--max-repair-rounds',type=int,choices=[1,2],default=2)
    p = sub.add_parser('collect');p.add_argument('config');p.add_argument('--out',required=True);p.add_argument('--as-of')
    p = sub.add_parser('batch');p.add_argument('catalog');p.add_argument('config')
    p = sub.add_parser('repair');p.add_argument('job_id');p.add_argument('--max-rounds',type=int,default=2)
    p = sub.add_parser('research-import');p.add_argument('package')
    p = sub.add_parser('research-status');p.add_argument('bundle_id')
    p = sub.add_parser('research-verification-config');p.add_argument('request_id');p.add_argument('--out',required=True)
    p = sub.add_parser('research-process');p.add_argument('request_id');p.add_argument('--config',required=True)
    sub.add_parser('status')
    sub.add_parser('report')
    args = parser.parse_args()
    if args.command=='story-check':
        from .plan import validate
        from .storyboard import enabled, missing_previsuals
        path=Path(args.manifest).resolve();plan=validate(json.loads(path.read_text(encoding="utf-8")))
        if not enabled(plan):
            parser.error('story-check requires storyboard_v1')
        missing=missing_previsuals(plan,path.parent)
        print(json.dumps({'story_contract':'passed','state':'awaiting_storyboard' if missing else 'ready_for_preflight',
                          'missing':missing,'shots':len(plan['shots']),'duration_seconds':plan['duration_seconds']},ensure_ascii=False,indent=2))
        return
    if args.command=='plan':
        generate(json.loads(Path(args.brief).read_text(encoding="utf-8")),args.out)
        print(args.out)
        return
    if args.command=='collect':
        from .ingest import collect
        import datetime
        result=collect(json.loads(Path(args.config).read_text(encoding="utf-8")),args.out,datetime.date.fromisoformat(args.as_of) if args.as_of else None)
        print(json.dumps({'topics':len(result['topics']),'errors':result['errors'],'catalog':str(Path(args.out).resolve()/'catalog.json')},ensure_ascii=False,indent=2))
        if not result['topics']:raise SystemExit(2)
        return
    root = Path(args.root).resolve()
    queue = Queue(root/'queue.sqlite')
    if args.command.startswith('research-'):
        from .research_inbox import ResearchInbox, MAX_BYTES
        inbox = ResearchInbox(queue, root)
        if args.command == 'research-import':
            with open(args.package, 'rb') as f:
                result = inbox.receive(f.read(MAX_BYTES + 1))
        elif args.command == 'research-status':
            result = inbox.status(args.bundle_id)
        elif args.command == 'research-process':
            result = inbox.process(args.request_id, json.loads(Path(args.config).read_text(encoding="utf-8")))
        else:
            from .core.io import write_json
            result = inbox.verification_config(args.request_id)
            write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command=='report':
        from .report import build
        print(build(root))
    elif args.command=='batch':
        from .batch import produce
        print(json.dumps(produce(queue,json.loads(Path(args.catalog).read_text(encoding="utf-8")),json.loads(Path(args.config).read_text(encoding="utf-8")),root),ensure_ascii=False,indent=2))
    elif args.command=='repair':
        from .repair import prepare
        if args.max_rounds not in (1,2):parser.error('max-rounds must be 1 or 2')
        print(json.dumps(prepare(queue,args.job_id,root/'repairs',args.max_rounds),ensure_ascii=False,indent=2))
    elif args.command=='submit':
        backend=json.loads(Path(args.backend).read_text(encoding="utf-8")) if args.backend else None
        print(json.dumps(submit(queue,args.manifest,root,backend),ensure_ascii=False,indent=2))
    elif args.command=='run':
        if not 1<=args.workers<=16:parser.error('workers must be 1–16')
        drain(queue,args.capabilities.split(','),args.workers)
        if args.auto_repair:
            from .repair import repair_batch
            for _ in range(args.max_repair_rounds):
                repairs=repair_batch(queue,root,args.max_repair_rounds)
                if not any(r.get('submission',{}).get('new') for r in repairs):break
                drain(queue,args.capabilities.split(','),args.workers)
        states=queue.status()
        print(json.dumps({'tasks':len(states),'counts':{s:sum(t['status']==s for t in states) for s in ('pending','running','succeeded','failed','blocked','reconcile')}},indent=2))
        if any(t['status'] in ('failed','blocked','reconcile') for t in states):raise SystemExit(1)
        if any(t['status'] in ('pending','running') for t in states):raise SystemExit(2)
    else:
        print(json.dumps(queue.status(),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
