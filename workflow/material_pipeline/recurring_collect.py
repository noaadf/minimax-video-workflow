"""Bounded incremental public-source discovery for the scheduled material chat.

Archives pages and discovery links only; editorial judgment and candidate creation
are performed by the returning Codex chat. No production queue or API inference.
"""
import argparse
import datetime as dt
import fcntl
import json
import re
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path
from urllib.parse import urlsplit

from workflow.ingest import fetch
from .core import canonical_url, write_json

ROOT = Path(__file__).resolve().parents[2] / 'research/material_pipeline'
ARTICLE = re.compile(r'/20\d\d/\d\d/\d+\.html|asi-post\.html\?id=\d+|/articles/[^/?]+|/[a-f0-9]{24}\.html|/dynamic-news/\d+|/t20\d+_\d+\.html|/20\d{2}/[^?#]+|/news/[^?#]+\.html', re.I)


def collect_page(entry, folder):
    url=canonical_url(entry['url'])
    try:
        page=fetch(url,[urlsplit(url).hostname],folder)
        archive=Path(page['archive_path']).parent
        (archive/'source.txt').write_text(page['text'], encoding="utf-8")
        articles=[]
        for link in page['links']:
            try:link=canonical_url(link)
            except ValueError:continue
            if urlsplit(link).hostname == urlsplit(url).hostname and ARTICLE.search(link):
                articles.append(link)
        return dict(name=entry['name'],url=url,status='archived',title=page['title'],
                    archive=page['archive_path'],sha256=page['content_sha256'],
                    article_urls=list(dict.fromkeys(articles))[:24],
                    body_status='dynamic_body_missing' if '正在取这篇稿子' in page['text'] else 'text_archived_requires_review',
                    observation='Public webpage collection; does not establish WeChat push history')
    except Exception as e:
        return dict(name=entry['name'],url=url,status='failed',error=type(e).__name__+': '+str(e)[:180],article_urls=[])


def run(config_path, limit=None):
    config=json.loads(Path(config_path).read_text(encoding="utf-8"))
    monitor=ROOT/'monitor';monitor.mkdir(exist_ok=True)
    with (monitor/'collection.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'state':'another_material_collection_is_running'}
        now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        cycle=monitor/'runs'/now.strftime('%Y%m%dT%H%M%S_%f')
        cycle.mkdir(parents=True)
        state_path=monitor/'state.json'
        state=json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {'seen_articles':{},'feeds':{}}
        # Baseline material packages before checking feeds. A fetched URL alone is not
        # an editorial decision; only packages and explicit dispositions consume it.
        existing={}
        for path in (ROOT/'inbox').glob('*/package.json'):
            package=json.loads(path.read_text(encoding="utf-8"))
            for source in package['sources']:existing[canonical_url(source['url'])]=package['content_id']
        for url,cid in existing.items():state['seen_articles'][url]={'status':'material_exists','content_id':cid}
        entries=config['sources'][:limit] if limit else config['sources']
        with ThreadPoolExecutor(max_workers=4) as pool:feeds=list(pool.map(lambda e:collect_page(e,cycle),entries))
        links=[];deferred=[]
        for feed in feeds:
            prior=state['feeds'].get(feed['url'],{})
            feed['changed']=feed.get('sha256')!=prior.get('sha256') or feed['status']!=prior.get('status')
            state['feeds'][feed['url']]={k:feed[k] for k in ('status','sha256') if k in feed}
            for url in feed['article_urls']:
                disposition=state['seen_articles'].get(url,{})
                if disposition.get('status') not in ('material_exists','rejected','duplicate','outside_window'):
                    retry_after=disposition.get('retry_after')
                    if retry_after:
                        try:
                            due=dt.datetime.fromisoformat(retry_after)
                            if due.tzinfo is None:due=due.replace(tzinfo=now.tzinfo)
                        except (TypeError,ValueError):due=now
                        if due>now:
                            deferred.append(dict(name=feed['name'],url=url,retry_after=retry_after))
                            continue
                    links.append(dict(name=feed['name'],url=url))
        groups=[[r for r in links if r['name']==entry['name']] for entry in entries]
        interleaved=[r for group in zip_longest(*groups) for r in group if r]
        deduped=list({r['url']:r for r in interleaved}.values())
        with ThreadPoolExecutor(max_workers=4) as pool:
            articles=list(pool.map(lambda e:collect_page(e,cycle),deduped[:config['max_articles_per_run']]))
        for row in articles:
            state['seen_articles'].setdefault(row['url'],{})
            state['seen_articles'][row['url']].update(last_collected_at=now.isoformat(),last_collection_status=row['status'],
                                                     last_archive=row.get('archive'))
        report=dict(cycle_id=cycle.name,started_at=now.isoformat(),state='discovery_complete_requires_editorial_selection',
                    feeds=feeds,articles=articles,pending_urls=deduped[len(articles):],deferred_pending=deferred,
                    existing_materials=len(existing),editorial_rules=config['editorial_rules'],
                    counts={'feeds_archived':sum(f['status']=='archived' for f in feeds),'feeds_failed':sum(f['status']=='failed' for f in feeds),
                            'articles_archived':sum(f['status']=='archived' for f in articles),'untriaged_article_urls':len(deduped)},
                    source_access_failures=[r for r in feeds+articles if r['status']=='failed'],
                    no_new_material_claimed=True,production_queue_writes=0)
        write_json(cycle/'collection.json',report)
        state['last_collection_at']=now.isoformat();state['last_collection']=str(cycle/'collection.json')
        write_json(state_path,state)
        write_json(monitor/'latest_collection.json',report)
        return dict(report=str(cycle/'collection.json'),counts=report['counts'],state=report['state'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default=str(ROOT/'monitor_config.json'))
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    print(json.dumps(run(args.config,args.limit),ensure_ascii=False))
