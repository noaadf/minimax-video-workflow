import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.review_shot_candidate import main


class ReviewShotCliTests(unittest.TestCase):
    def run_review(self, result=None, error=None):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            spec=root/'spec.json'
            spec.write_text(json.dumps({'video':'clip.mp4','shot':{'duration_seconds':4}}))
            with patch('scripts.review_shot_candidate.review_shot',return_value=result,side_effect=error),contextlib.redirect_stdout(io.StringIO()):
                code=main(['--spec',str(spec),'--output',str(root/'review')])
            return code,json.loads((root/'review/review.json').read_text())

    def test_api_failure_returns_nonzero_with_saved_failure(self):
        code,record=self.run_review({'status':'review_required','error_type':'RuntimeError'})
        self.assertEqual(code,2)
        self.assertEqual(record['review_execution_status'],'failed')

    def test_content_concern_is_a_completed_review(self):
        code,record=self.run_review({'status':'review_required','review':{'findings':[]}})
        self.assertEqual(code,0)
        self.assertEqual(record['review_execution_status'],'completed')

    def test_preparation_failure_is_saved_without_exception_text(self):
        code,record=self.run_review(error=ValueError('sensitive configuration value'))
        self.assertEqual(code,2)
        self.assertEqual(record['error_type'],'ValueError')
        self.assertNotIn('sensitive',json.dumps(record))
