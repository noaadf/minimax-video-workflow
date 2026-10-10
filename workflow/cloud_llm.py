"""Durable cloud model calls: one request identity, two slots, no blind replay."""
import argparse,base64,fcntl,json,os,re,subprocess,sys,time,urllib.error
from pathlib import Path
from .core.io import digest,write_json

ERROR_DIAGNOSTIC_VERSION = 'cloud-http-diagnostic-v1'


def failure_diagnostic(error):
    """Preserve safe HTTP evidence without retaining URLs, bodies or credentials."""
    if getattr(error, 'diagnostic', None):
        return error.diagnostic
    if not isinstance(error, urllib.error.HTTPError):
        return None
    code = error.code
    categories = {400:'invalid_request', 401:'credentials_or_permission',
                  403:'credentials_or_permission', 404:'endpoint_not_found',
                  408:'provider_timeout', 413:'request_too_large',
                  422:'invalid_request', 429:'rate_limited', 504:'provider_timeout'}
    diagnostic = {'version': ERROR_DIAGNOSTIC_VERSION, 'code':'provider_http_error',
                  'http_status':code,
                  'category':categories.get(code, 'provider_unavailable' if code >= 500 else 'http_error'),
                  'automatic_replay':False}
    headers = error.headers or {}
    retry_after = headers.get('Retry-After', '')
    if isinstance(retry_after, str) and re.fullmatch(r'[0-9]{1,6}', retry_after):
        diagnostic['retry_after_seconds'] = int(retry_after)
    # Never serialize exception text or response bodies; providers may echo input.
    return diagnostic


def request_identity(request):
    if request.get('model') not in ('glm-5.3','glm-5.3-flash'):raise ValueError('Unsupported cloud model')
    if type(request.get('max_tokens')) is not int or not 1<=request['max_tokens']<=16000:raise ValueError('Output budget')
    if not isinstance(request.get('instruction'),str) or not isinstance(request.get('images'),list):raise ValueError('Request schema')
    if len(request['images'])>8:raise ValueError('Image budget')
    for image in request['images']:
        if image['suffix'] not in ('.png','.jpg','.jpeg','.webp'):raise ValueError('Image type')
        base64.b64decode(image['data'],validate=True)
    if 'thinking' in request and request['thinking']!={'type':'disabled'}:raise ValueError('Unsupported thinking option')
    return digest(request)


def submit(root, request, spawn=subprocess.Popen):
    root=Path(root);jid=request_identity(request);folder=root/'jobs'/jid;folder.mkdir(parents=True,exist_ok=True)
    with (folder/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        status=folder/'status.json'
        if status.exists():return json.loads(status.read_text(encoding="utf-8"))
        write_json(folder/'request.json',request)
        state={'id':jid,'state':'submitted','at':time.time()};write_json(status,state)
        # An uncertain launch remains submitted for reconciliation. Never spawn twice.
        with (folder/'worker.log').open('ab') as log:
            proc=spawn([sys.executable,'-m','workflow.cloud_llm','--root',str(root),'worker','--id',jid],
                       stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        state['pid']=proc.pid;write_json(status,state);return state


def status(root,jid):
    if not re.fullmatch('[a-f0-9]{64}',jid):raise ValueError('Invalid identity')
    folder=Path(root)/'jobs'/jid
    value=json.loads((folder/'status.json').read_text(encoding="utf-8"))
    if not isinstance(value,dict) or value.get('id')!=jid:
        raise ValueError('Cloud status belongs to another request')
    if (folder/'result.json').exists():
        result=json.loads((folder/'result.json').read_text(encoding="utf-8"))
        receipt=result.get('receipt') if isinstance(result,dict) else None
        if (not isinstance(result,dict) or 'answer' not in result or not isinstance(receipt,dict)
                or receipt.get('cloud_job_id')!=jid or receipt.get('execution')!='cloud'):
            raise ValueError('Incomplete or unbound committed cloud result')
        # Result commits before the final status. Recover that crash window without replay.
        recovered={'recovered_from_state':value.get('state')} if value.get('state')!='completed' else {}
        return {**value,'state':'completed','result':result,**recovered}
    return value


def worker(root,jid):
    from .anthropic_client import ask,ModelResultError
    root=Path(root);folder=root/'jobs'/jid;config=json.loads((root/'config.json').read_text(encoding="utf-8"))
    req=json.loads((folder/'request.json').read_text(encoding="utf-8"));assert request_identity(req)==jid
    with (folder/'lock').open('a') as joblock:
        fcntl.flock(joblock,fcntl.LOCK_EX)
        st=json.loads((folder/'status.json').read_text(encoding="utf-8"))
        if st['state']!='submitted':return
        st.update(state='waiting_slot',at=time.time());write_json(folder/'status.json',st)
    slot=None;deadline=time.monotonic()+600
    try:
        while slot is None and time.monotonic()<deadline:
            for i in range(2):
                handle=(root/f'slot{i}.lock').open('a')
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);slot=handle;break
                except BlockingIOError:handle.close()
            if slot is None:time.sleep(2)
        if slot is None:raise TimeoutError('Slot wait expired')
        from .cloud_dns import install
        install('open.bigmodel.cn',root/'dns_cache.json')
        os.environ[config['api_key_env']]=Path(config['credential_file']).read_text(encoding="utf-8").strip()
        images=[]
        for i,item in enumerate(req['images']):
            path=folder/('image'+str(i)+item['suffix']);path.write_bytes(base64.b64decode(item['data']));images.append(path)
        st.update(state='calling',at=time.time());write_json(folder/'status.json',st)
        if req.get('thinking')=={'type':'disabled'}:config={**config,'thinking':req['thinking']}
        config={**config,'response_audit_path':str(folder/'raw_response.json')}
        answer,receipt=ask(config,req['instruction'],req['data'],req['max_tokens'],images,req['model'])
        result={'answer':answer,'receipt':{**receipt,'execution':'cloud','cloud_job_id':jid}}
        write_json(folder/'result.json',result);st.update(state='completed',at=time.time())
    except Exception as e:
        st.update(state='failed',at=time.time(),error_type=type(e).__name__)
        diagnostic = failure_diagnostic(e)
        if diagnostic:st['diagnostic']=diagnostic
    finally:
        if slot:slot.close()
        write_json(folder/'status.json',st)


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('action',choices=['submit','status','worker']);p.add_argument('--id');a=p.parse_args()
    if a.action=='worker':worker(a.root,a.id);return
    if a.action=='submit':
        raw=sys.stdin.buffer.read(32*1024*1024+1)
        if len(raw)>32*1024*1024:raise ValueError('Request too large')
        value=submit(a.root,json.loads(raw))
    else:value=status(a.root,a.id)
    print(json.dumps(value,ensure_ascii=False))

if __name__=='__main__':main()
