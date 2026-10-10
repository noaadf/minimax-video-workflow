import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from workflow.repair_delivery import validate_product,NotDeliverable
class DeliveryHoldTest(unittest.TestCase):
 def test_known_visual_defect_blocks_before_database_or_export(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);folder=root/'user_repair30_20261002/delivery_holds';folder.mkdir(parents=True)
   (folder/'bad-version.json').write_text(json.dumps({'status':'blocked','reason':'confirmed temporal duplication'}))
   with self.assertRaisesRegex(NotDeliverable,'manual defect'):
    validate_product(SimpleNamespace(path=str(root/'queue.sqlite')),{'new_job_id':'bad-version'})
