import importlib.util,json
from pathlib import Path
from unittest.mock import patch
SPEC=importlib.util.spec_from_file_location('deadline',Path(__file__).parents[1]/'scripts/run_deadline30.py')
d=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(d)

def test_catalog_deduplicates_url_and_story_and_rejects_unverified(tmp_path):
    base={'status':'source_supported','source_url':'https://a','facts':[{'text':'fact'}],'title':'story'}
    p=tmp_path/'a.json';p.write_text(json.dumps({'topics':[base,{**base,'title':'renamed'},{**base,'source_url':'https://b'},{**base,'source_url':'https://c','title':'new','status':'unverified'},{**base,'source_url':'https://d','title':'different'}]}))
    assert len(d.candidates([p,tmp_path/'missing']))==2

def test_export_is_not_delivery_or_old_count():
    rows=[{'job_id':'new','stage':'export','status':'succeeded'}, {'job_id':'old','stage':'export','status':'succeeded'}, {'job_id':'new','stage':'quality','status':'succeeded','result':json.dumps({'delivery_policy':{'ready_for_delivery':False}})}]
    assert d.counts(rows,['new'])['ready_count']==0
    rows[-1]['result']=json.dumps({'delivery_policy':{'ready_for_delivery':True}})
    assert d.counts(rows,['new'])['ready_count']==1

def test_process_environment_does_not_mutate_parent(tmp_path):
    import os
    before=dict(os.environ)
    with patch.object(d.subprocess,'Popen') as popen:
        d.launch(['--plan','x'],'relay.json',tmp_path/'log')
        assert popen.call_args.kwargs['env']['WORKFLOW_LLM_CONFIG'].endswith('/relay.json')
        assert dict(os.environ)==before

def test_controller_lock_is_exclusive(tmp_path):
    import fcntl
    p=tmp_path/'controller.lock'
    with p.open('a') as one,p.open('a') as two:
        fcntl.flock(one,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:fcntl.flock(two,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:raise AssertionError('second producer acquired controller lock')

def test_current_affairs_first_then_catalogs_alternate(tmp_path):
    base=tmp_path/'research/deadline30_20261002';base.mkdir(parents=True)
    for group,number in [('a',3),('b',2),('c',1)]:
        topics=[{'id':f'{group}{i}','title':f'{group}{i}','source_url':f'https://{group}/{i}','facts':[{'text':'fact'}],'status':'source_supported'} for i in range(number)]
        (base/f'catalog_{group}.json').write_text(json.dumps({'topics':topics}))
    with patch.object(d,'ROOT',tmp_path):assert [t['id'] for t in d.ordered_catalogs()]==['c0','a0','b0','a1','b1','a2']

def test_child_uses_venv_path_without_resolving_symlink(tmp_path):
    with patch.object(d.subprocess,'Popen') as popen:
        d.launch(['--render','x'],'planning.json',tmp_path/'log')
        assert popen.call_args.args[0][0]==str(d.ROOT/'.venv-workflow/bin/python')
        assert '/.venv-workflow/' in popen.call_args.args[0][0]
