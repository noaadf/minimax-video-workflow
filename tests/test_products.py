import unittest
from workflow.products import product_id,products
from workflow.dashboard import snapshot
class ProductTests(unittest.TestCase):
 def test_ancestry(self):
  self.assertEqual(product_id('new-repair',{'new-repair':{'parent_job_id':'original'}}),'original')
  self.assertEqual(product_id('pilot6_east_a_20261001-ref-abc',{}),'pilot6_east_a_20261001')
  self.assertNotEqual(product_id('pilot6_east_a_20261001-ref-abc',{}),product_id('pilot6_east_b_20261001-ref-abc',{}))
 def test_cycle_bounded(self):
  self.assertIn(product_id('a',{'a':{'parent_job_id':'b'},'b':{'parent_job_id':'a'}}),['a','b'])
