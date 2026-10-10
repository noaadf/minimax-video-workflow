import unittest
from workflow.ingest import publication_date
class DatelineTests(unittest.TestCase):
 def test_explicit_today_dateline(self):
  self.assertEqual(publication_date({},'[中国，上海，2026年9月17日] 今日，发布了新产品。',''),'2026-09-17')
  self.assertEqual(publication_date({},'[中国，上海，2026年9月10日] 2026合作伙伴大会今日在沪举行。',''),'2026-09-10')
 def test_past_event_is_not_publication_date(self):
  self.assertIsNone(publication_date({},'去年，2025年9月17日，公司发布产品。',''))
  self.assertIsNone(publication_date({},'[中国，上海，2025年9月17日] 当时发布了产品。',''))
 def test_metadata_takes_precedence(self):
  self.assertEqual(publication_date({'article:published_time':'2026-10-02'},'[中国，上海，2026年9月17日] 今日，发布产品。',''),'2026-10-02')
