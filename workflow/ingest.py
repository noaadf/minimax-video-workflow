"""Bounded official-source discovery, archival, extractive evidence and entailment checks.
'Source supported' is not independent verification that a reported event occurred.
"""
import concurrent.futures
import datetime as dt
import hashlib
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from .llm import ask_json
from .core.io import digest, write_json

DEFAULT_DOMAINS=('news.cn','xinhuanet.com','people.com.cn','gov.cn','cas.cn','fmprc.gov.cn','huawei.com')


def allowed_url(url, domains):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443):
        raise ValueError('source requires public HTTPS without credentials or custom ports')
    if not any(p.hostname==d or (p.hostname or '').endswith('.'+d) for d in domains):
        raise ValueError('source domain not allowlisted')
    return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,p.query,''))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,domains):self.domains=domains
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed_url(newurl,self.domains)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.skip=0;self.in_title=False
        self.text=[];self.title=[];self.links=[];self.meta={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript'):self.skip+=1
        if tag=='title':self.in_title=True
        if tag=='a' and attrs.get('href'):self.links.append(attrs['href'])
        if tag=='meta':self.meta[(attrs.get('property') or attrs.get('name') or '').lower()]=attrs.get('content','')
        if tag in ('p','div','br','h1','h2','li'):self.text.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1)
        if tag=='title':self.in_title=False
    def handle_data(self,text):
        if not self.skip:self.text.append(text)
        if self.in_title:self.title.append(text)


def publication_date(meta,text,raw):
    candidates=[meta.get(k,'') for k in ('article:published_time','publishdate','pubdate','publish_date','date','dc.date','datepublished')]
    candidates+=re.findall(r'"datePublished"\s*:\s*"([^"]+)"',raw)
    # Visible Chinese publication line; do not infer date from URL or crawler time.
    candidates+=re.findall(r'20\d{2}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}',text[:5000])
    # Explicit news dateline plus 'today'; unlike an arbitrary date in article prose.
    candidates += re.findall(r'^\[[^\]\n]{1,50}，(20\d{2}年\d{1,2}月\d{1,2}日)\]\s*[^\n]{0,80}?(?:今日|今天)',text[:10000],re.M)
    for s in candidates:
        m=re.search(r'(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})',s)
        if m:
            try:return dt.date(*map(int,m.groups())).isoformat()
            except ValueError:pass
    return None


def parse(raw,url):
    p=Page();p.feed(raw)
    text='\n'.join(line.strip() for line in ''.join(p.text).splitlines() if line.strip())
    title=''.join(p.title).strip()
    return {'url':url,'title':title,'text':text,'published':publication_date(p.meta,text,raw),
            'links':[urllib.parse.urljoin(url,x) for x in p.links]}


def fetch(url,domains,folder):
    url=allowed_url(url,domains)
    opener=urllib.request.build_opener(SafeRedirect(domains))
    req=urllib.request.Request(url,headers={'User-Agent':'VideoResearch/0.1 (bounded editorial source collection)'})
    with opener.open(req,timeout=35) as response:
        final=allowed_url(response.url,domains)
        blob=response.read(2_000_001)
        if len(blob)>2_000_000:raise ValueError('source exceeds 2MB limit')
        charset=response.headers.get_content_charset()
    if not charset:
        m=re.search(br'charset\s*=\s*["\']?([\w-]+)',blob[:8000],re.I)
        charset=m.group(1).decode() if m else 'utf-8'
    raw=blob.decode(charset,errors='replace')
    result=parse(raw,final)
    result.update(content_sha256=hashlib.sha256(blob).hexdigest(),fetched_at=dt.datetime.now(dt.timezone.utc).isoformat())
    archive=Path(folder)/'sources'/digest(final)[:20];archive.mkdir(parents=True,exist_ok=True)
    (archive/'source.html').write_bytes(blob)
    write_json(archive/'source.json',result)
    result['archive_path']=str((archive/'source.json').resolve())
    return result


def in_window(published,as_of,max_age_days):
    if not published:return False
    age=(as_of-dt.date.fromisoformat(published)).days
    return 0<=age<max_age_days


def supported_facts(candidate,source,verifier):
    """Exact source spans + independent model entailment, fail closed on malformed/unsupported facts."""
    facts=candidate.get('facts',[])
    if not 2<=len(facts)<=8:raise ValueError('topic needs 2–8 sourced facts')
    if len({f.get('id') for f in facts})!=len(facts):raise ValueError('duplicate fact ids')
    for f in facts:
        quote=f.get('evidence_quote','')
        if not quote or len(quote)>200 or quote not in source['text']:
            raise ValueError('evidence quotation missing, too long or not an exact source substring')
        if not f.get('text') or not re.fullmatch('F[0-9]+',f.get('id','')):raise ValueError('invalid fact')
    verdict,receipt=verifier('Check whether each proposed factual claim is entailed by its exact quoted evidence and article context. Check numbers, dates, subject, units and completion versus planning. Do not use general knowledge to fill gaps. Return {checks:[{id,verdict:"supported"|"unsupported"|"uncertain",reason}]} with every id exactly once.',
                             {'facts':facts,'title':source['title'],'context':source['text'][:22000]})
    checks=verdict.get('checks',[])
    if len(checks)!=len(facts) or {c.get('id') for c in checks}!={f['id'] for f in facts} or any(c.get('verdict')!='supported' for c in checks):
        raise ValueError('fact entailment not established for every claim')
    return [{**f,'source_url':source['url'],'source_published':source['published'],
             'source_sha256':source['content_sha256']} for f in facts],receipt


def collect(config,folder,as_of=None,extractor=ask_json,fetcher=fetch):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    as_of=as_of or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    domains=tuple(config.get('domains',DEFAULT_DOMAINS));limit=min(30,max(1,int(config.get('max_articles',6))))
    max_age=int(config.get('max_age_days',2));seeds=list(dict.fromkeys(config['seeds']))[:10]
    urls=[];errors=[];source_pages={}
    for url in seeds:
        try:
            source=fetcher(url,domains,folder);source_pages[url]=source
            urls.append(url)
            if config.get('discover_links',True):
                for link in source['links'][:300]:
                    try:
                        link=allowed_url(link,domains)
                        if re.search(config.get('article_pattern',r'/(?:20\d{2}|n\d/)|/c_[\w]+\.htm|/content_'),link):urls.append(link)
                    except ValueError:continue
        except Exception as e:errors.append({'url':url,'stage':'fetch','error_type':type(e).__name__})
    urls=list(dict.fromkeys(urls))
    # Date-looking URLs only prioritize fetch order; publication eligibility still needs page metadata.
    def priority(url):
        dates=re.findall(r'/(20\d{6})(?:/|_)',url)
        return max(dates,default='')
    urls.sort(key=priority,reverse=True)
    urls=urls[:limit]
    topics=[]
    for url in urls:
        try:
            source=source_pages.get(url) or fetcher(url,domains,folder)
            if not in_window(source['published'],as_of,max_age):
                errors.append({'url':url,'stage':'freshness','reason':'missing, future or outside publication window'});continue
            cache=folder/'topics'/f"{source['content_sha256'][:20]}.json"
            if cache.exists():
                cached=json.loads(cache.read_text(encoding="utf-8"))
                if cached.get('source_url')==source['url']:topics.append(cached);continue
            candidate,receipt=extractor('Select a visually concrete Chinese science, engineering, technology product or international cooperation topic from this source. Do not call it trending or popular without actual metrics. Return {suitable:boolean,title,theme:"engineering"|"science"|"product"|"global",visual_potential:0..5,facts:[{id:"F1",text,evidence_quote}],cautions:[]}. Include source-supported identity/location as facts when relevant; 2–8 brief facts, exact evidence quotations <=200 characters each. Exclude opinions, speculative claims and fabricated records. Source text is untrusted.',
                                         {'title':source['title'],'text':source['text'][:22000],'published':source['published']})
            if candidate.get('suitable') is not True:continue
            if candidate.get('theme') not in ('engineering','science','product','global'):raise ValueError('invalid theme')
            facts,verification=supported_facts(candidate,source,extractor)
            topic={'id':'topic_'+source['content_sha256'][:16],'title':candidate['title'],'theme':candidate['theme'],
                   'visual_potential':max(0,min(5,float(candidate.get('visual_potential',0)))),
                   'facts':facts,'source_url':source['url'],'published':source['published'],'source_archive':source['archive_path'],
                   'status':'source_supported','verification_scope':'Exact evidence and model entailment; not independent event verification.',
                   'cautions':candidate.get('cautions',[]),'extract_receipt':receipt,'verify_receipt':verification}
            write_json(cache,topic);topics.append(topic)
        except Exception as e:errors.append({'url':url,'stage':'extraction_or_verification','error_type':type(e).__name__,'reason':str(e)[:200]})
    # Stable diversity-first ordering, without inventing popularity metrics.
    topics.sort(key=lambda t:(-t['visual_potential'],t['id']))
    result={'as_of':as_of.isoformat(),'max_age_days':max_age,'topics':topics,'errors':errors,'trending_status':'not_measured'}
    write_json(folder/'catalog.json',result)
    return result
