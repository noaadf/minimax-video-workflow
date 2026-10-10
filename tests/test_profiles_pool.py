import copy
import json
import tempfile
import unittest
from pathlib import Path
from workflow.backends import assignments, ready, rebalance_unstarted


class ReferenceRoutingTests(unittest.TestCase):
    def test_reference_shots_require_explicit_verified_support(self):
        backend={'type':'ascend_ssh','expires_at':'2099-01-01T00:00:00+00:00','ready':True,'capability':'plain'}
        shot={'id':'reference','duration_seconds':4,'reference_images':['first.png']}
        with self.assertRaisesRegex(ValueError,'reference images'):
            assignments(backend,[shot])
        backend['supports_reference_images']=True
        self.assertEqual(assignments(backend,[shot])['reference']['capability'],'plain')

    def test_mixed_pool_keeps_reference_shots_off_text_only_groups(self):
        groups=[{'type':'ascend_ssh','expires_at':'2099-01-01T00:00:00+00:00','ready':True,'capability':name,'host':'host',
                 'port':port,'resource_id':name,'supports_reference_images':supports}
                for name,port,supports in [('fast',1,False),('conditioned',2,True)]]
        shots=[{'id':str(i),'duration_seconds':4,'reference_images':['first.png']}
               for i in range(3)]
        result=assignments({'type':'pool','groups':groups},shots)
        self.assertEqual({g['capability'] for g in result.values()},{'conditioned'})

    def test_two_frame_acceptance_does_not_certify_other_reference_counts(self):
        backend={'type':'ascend_ssh','expires_at':'2099-01-01T00:00:00+00:00','ready':True,'capability':'conditioned',
                 'supports_reference_images':True,'validated_reference_image_counts':[2]}
        for count in (1,3):
            with self.assertRaisesRegex(ValueError,'reference images'):
                assignments(backend,[{'id':'shot','duration_seconds':4,'reference_images':['frame.png']*count}])
        self.assertIn('shot',assignments(backend,[{'id':'shot','duration_seconds':4,'reference_images':['first.png','last.png']}]))
from workflow.profiles import select, apply, publication_text
from workflow.queue import Queue


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.pool={'type':'pool','groups':[
            {'type':'ascend_ssh','expires_at':'2099-01-01T00:00:00+00:00','host':'same-gateway','port':1,'resource_id':'notebook1','capability':'g1','ready':True},
            {'type':'ascend_ssh','expires_at':'2099-01-01T00:00:00+00:00','host':'same-gateway','port':2,'resource_id':'notebook2','capability':'g2','ready':True}]}
        self.shots=[{'id':str(i),'duration_seconds':4} for i in range(8)]

    def test_balanced_groups_claim_concurrently_but_each_stays_exclusive(self):
        allocated=assignments(self.pool,self.shots)
        self.assertEqual([allocated[str(i)]['capability'] for i in range(8)],['g1','g2']*4)
        with tempfile.TemporaryDirectory() as directory:
            q=Queue(Path(directory)/'queue.sqlite')
            q.submit('j','d',{},[{'id':s['id'],'stage':'shot','payload':{},'deps':[], 'capability':allocated[s['id']]['capability']} for s in self.shots])
            self.assertEqual(q.claim(['g1'])['id'],'0')
            self.assertEqual(q.claim(['g2'])['id'],'1')
            self.assertIsNone(q.claim(['g1','g2']))

    def test_unready_group_never_receives_work(self):
        self.pool['groups'][1]['ready']=False
        self.assertEqual({g['capability'] for g in assignments(self.pool,self.shots).values()},{'g1'})
        self.pool['groups'][0]['ready']=False
        self.assertFalse(ready(self.pool))

    def test_aliases_cannot_fake_additional_capacity(self):
        for key in ('resource_id','port','capability'):
            p=copy.deepcopy(self.pool);p['groups'][1][key]=p['groups'][0][key]
            with self.assertRaises(ValueError):assignments(p,self.shots)

    def test_reused_shots_do_not_consume_accelerator(self):
        self.shots[0]['source_path']='already.mp4'
        self.assertNotIn('0',assignments(self.pool,self.shots))

    def test_live_rebalance_never_moves_claimed_or_uncertain_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            q=Queue(Path(directory)/'queue.sqlite')
            p={'backend':self.pool['groups'][0]}
            q.submit('j','frozen',p,[{'id':s['id'],'stage':'shot','payload':{'plan':p,'data':s},'deps':[],'capability':'g1'} for s in self.shots])
            first=q.claim(['g1'])
            moved=rebalance_unstarted(q,'j',self.pool)
            self.assertNotIn(first['id'],[t['id'] for t in moved])
            second=q.claim(['g2']);self.assertIsNotNone(second)
            q.finish(second,error='unknown remote result',uncertain=True)
            again=rebalance_unstarted(q,'j',self.pool)
            self.assertNotIn(second['id'],[t['id'] for t in again])
            with q.connect() as c:self.assertEqual(c.execute('SELECT digest FROM jobs').fetchone()[0],'frozen')


class ProfileTests(unittest.TestCase):
    def test_deterministic_selection_and_preserved_credit(self):
        p={'id':'one','templates':['science_reveal'],'voice':{'name':'generic'},'music':{'credit':'Author CC BY','source_url':'https://music','license_url':'https://license','changes':'excerpt'}}
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'profiles.json';file.write_text(json.dumps({'profiles':[p]}))
            a=select(file,'source','science_reveal');b=select(file,'source','science_reveal')
            self.assertEqual(a,b)
            brief=apply({},a)
            text=publication_text({**brief,'title':'title','comment_prompt':'question','facts':[{'source_url':'https://fact'}]})
            self.assertIn('Author CC BY',text);self.assertIn('https://license',text);self.assertIn('excerpt',text)

if __name__=='__main__':unittest.main()
