import copy
import datetime as dt
import unittest
from unittest.mock import Mock,patch
from workflow.backends import ready,members,assignments,unavailable_reasons
from workflow.runner import ssh_generate,submit,StageError


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,2,3,40,tzinfo=dt.timezone.utc)
        self.group={'type':'ascend_ssh','ready':True,'capability':'npu','resource_id':'one',
                    'host':'worker','port':22,'expires_at':'2026-10-02T12:00:00+08:00'}
        self.shot={'id':'S1','duration_seconds':4}

    def test_historical_ready_cannot_override_expiry_or_missing_metadata(self):
        for expiry in ['2026-10-02T05:22:47+08:00',None,'bad-date','2026-10-02T12:00:00',123]:
            g={**self.group,'expires_at':expiry}
            self.assertFalse(ready(g,self.now));self.assertEqual(members(g,self.now),[])
            with self.assertRaises(ValueError):assignments(g,[self.shot],now=self.now)

    def test_ten_minute_margin_and_explicit_unavailable(self):
        g={**self.group,'expires_at':'2026-10-02T03:49:59+00:00'}
        self.assertFalse(ready(g,self.now))
        g['expires_at']='2026-10-02T03:50:00+00:00'
        self.assertTrue(ready(g,self.now))
        g['ready_for_dispatch']=False
        self.assertFalse(ready(g,self.now))
        self.assertIn('resource explicitly unavailable for dispatch',unavailable_reasons(g,self.now))

    def test_mixed_pool_routes_only_to_unexpired_group(self):
        expired={**self.group,'resource_id':'two','capability':'expired','port':23,'expires_at':'2026-10-01T00:00:00Z'}
        pool={'type':'pool','groups':[expired,self.group]};before=copy.deepcopy(pool)
        self.assertEqual(assignments(pool,[self.shot],self.now)['S1']['resource_id'],'one')
        self.assertEqual(pool,before)

    def test_frozen_worker_lease_expiry_is_checked_before_network(self):
        g={**self.group,'expires_at':'2000-01-01T00:00:00Z'}
        with patch('workflow.runner.media.run') as run:
            with self.assertRaises(StageError):ssh_generate({'backend':g},self.shot,{},None)
            run.assert_not_called()

    def test_expired_backend_never_enters_queue(self):
        g={**self.group,'expires_at':'2000-01-01T00:00:00Z'};q=Mock()
        plan={'shots':[self.shot]}
        with patch('workflow.runner.Path.read_text',return_value='{}'),patch('workflow.runner.validate',return_value=plan),patch('workflow.runner.freeze_assets',return_value=plan):
            with self.assertRaisesRegex(ValueError,'awaiting_backend'):submit(q,'unused.json','unused',g)
        q.submit.assert_not_called()

    def test_assets_only_work_does_not_require_a_notebook(self):
        self.assertTrue(ready({'type':'assets'},self.now))
        self.assertEqual(assignments({'type':'assets'},[{**self.shot,'source_path':'existing.mp4'}],self.now),{})
