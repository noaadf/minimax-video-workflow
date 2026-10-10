"""SSH transport to the persistent cloud model worker; no provider key leaves it."""
import base64,json,mimetypes,shlex,subprocess,time
from pathlib import Path
from .cloud_llm import request_identity
from .core.failures import WorkflowFailure, is_recoverable_provider_failure


def recovery_request(request, prior):
    """One deterministic transport-only attempt; never reroll a model verdict.

    The existing remote worker hashes the entire request but forwards only its
    explicit instruction/data/model/images/token fields to the provider. This
    metadata therefore changes the durable job identity, not review semantics.
    """
    base=request_identity(request)
    if 'transport_attempt' in request:raise ValueError('Recovery attempts cannot chain')
    if prior.get('id')!=base or prior.get('state')!='failed' or prior.get('result') is not None:
        raise ValueError('Confirmed original terminal provider failure required')
    diagnostic=prior.get('diagnostic') or {}
    code=diagnostic.get('code')
    allowed=code in ('provider_stream_error','provider_error')
    if code=='provider_http_error':
        status=diagnostic.get('http_status')
        allowed=type(status) is int and (status in (408,429) or 500<=status<=599)
    if not allowed:raise ValueError('Failure is not a confirmed recoverable provider error')
    return {**request,'transport_attempt':{'version':1,'attempt':1,'prior_job_id':base,
                                         'reason':'confirmed_terminal_provider_error'}}


def truncation_recovery_request(request, prior):
    """Explicit once-only recovery of exhausted reasoning, never a content reroll.

    The original request must use provider-default reasoning; the sole provider
    change is disabling thinking, with identical content and token limit.
    """
    base=request_identity(request)
    if 'transport_attempt' in request or 'thinking' in request:
        raise ValueError('Truncation recovery cannot chain or change existing thinking')
    d=prior.get('diagnostic') or {};r=d.get('receipt') or {}
    if (prior.get('id')!=base or prior.get('state')!='failed'
            or prior.get('result') is not None or d.get('code')!='incomplete_output'
            or r.get('stop_reason')!='max_tokens'):
        raise ValueError('Confirmed terminal max_tokens truncation without verdict required')
    return {**request,'thinking':{'type':'disabled'},'transport_attempt':{
        'version':1,'attempt':1,'prior_job_id':base,
        'reason':'confirmed_output_truncation_disable_thinking'}}


def ask(config,instruction,data,max_tokens,image_paths=None,model=None,*,technical_recovery_path=None,disable_thinking_recovery_path=None):
    if technical_recovery_path is not None and disable_thinking_recovery_path is not None:
        raise ValueError('Choose one explicit recovery mode')
    remote=config['cloud_worker'];images=[]
    for path in image_paths or []:
        p=Path(path);images.append({'suffix':p.suffix.lower(),'data':base64.b64encode(p.read_bytes()).decode()})
    req={'instruction':instruction,'data':data,'max_tokens':max(max_tokens,config.get('cloud_min_output_tokens',0)),
         'model':model or config['model'],'images':images}
    if config.get('thinking')=={'type':'disabled'}:req['thinking']={'type':'disabled'}
    jid=request_identity(req)
    ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
         '-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2','-p',str(remote['port']),'-i',str(Path(remote['identity_file']).expanduser()),remote['host']]
    def call(action,payload=None,job_id=None):
        command='cd '+shlex.quote(remote['code_root'])+' && '+shlex.join([remote.get('python','python3'),'-m','workflow.cloud_llm','--root',remote['state_root'],action,'--id',job_id or jid])
        result=subprocess.run(ssh+[command],input=payload,text=True,capture_output=True,timeout=40)
        if result.returncode:raise WorkflowFailure('transport_network', 'Cloud transport failed; reconcile job '+jid, evidence={'cloud_job_id': jid, 'action': action})
        return json.loads(result.stdout)
    if technical_recovery_path is not None or disable_thinking_recovery_path is not None:
        # status() recovers a committed result before this check, so a valid
        # review (including a rejection) can never be replayed as a failure.
        from .core.io import write_json
        evidence=Path(technical_recovery_path or disable_thinking_recovery_path)
        prior=call('status');original_jid=jid
        try:req=(truncation_recovery_request if disable_thinking_recovery_path is not None else recovery_request)(req,prior)
        except ValueError:
            rejected=evidence.with_name(evidence.stem+'.rejected.json')
            if not rejected.exists():
                rejected.parent.mkdir(parents=True,exist_ok=True)
                write_json(rejected,{'original_job_id':original_jid,'original_terminal_status':prior,
                                     'recovery_submitted':False,'reason':'ineligible_provider_failure'})
            raise
        jid=request_identity(req)
        record={'version':1,'attempt':1,'original_job_id':original_jid,
                'recovery_job_id':jid,'original_terminal_status':prior,
                'provider_payload_unchanged':disable_thinking_recovery_path is None,'remote_service_modified':False}
        if disable_thinking_recovery_path is not None:
            record.update(review_semantics_unchanged=True,old_thinking='provider_default',
                          new_thinking={'type':'disabled'},max_tokens_unchanged=req['max_tokens'])
        if evidence.exists():
            old=json.loads(evidence.read_text(encoding="utf-8"))
            if old.get('original_job_id')!=original_jid or old.get('recovery_job_id')!=jid:
                raise ValueError('Technical recovery evidence belongs to another request')
        else:
            evidence.parent.mkdir(parents=True,exist_ok=True);write_json(evidence,record)
    state=call('submit',json.dumps(req,ensure_ascii=False));deadline=time.monotonic()+remote.get('wait_seconds',960)
    while state['state'] not in ('completed','failed'):
        if time.monotonic()>deadline:raise WorkflowFailure('cloud_job_pending', 'Cloud call still pending; reconcile job '+jid, evidence={'cloud_job_id': jid})
        time.sleep(3);state=call('status')
    if state['state']=='failed':
        raise WorkflowFailure('cloud_job_failed', 'Cloud call failed; reconcile job '+jid,
                              evidence={'cloud_job_id': jid, 'diagnostic': state.get('diagnostic'),
                                        'error_type': state.get('error_type'),
                                        'recoverable': is_recoverable_provider_failure(state.get('diagnostic') or {})})
    if not state.get('result'):state=call('status')
    return state['result']['answer'],state['result']['receipt']
