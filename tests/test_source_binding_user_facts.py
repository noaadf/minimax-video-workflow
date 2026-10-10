"""User-provenance facts: accepted directly, never checked against the archive.

Owner directive 2026-10-05: 但凡是用户添加的信息直接接受. Source-verified facts
keep the full verbatim-quote discipline; user facts skip it and stay marked."""
import copy, json, tempfile, unittest
from pathlib import Path
from workflow.source_binding import bind
from workflow.plan import validate


def _packet(tmp):
    archive = tmp / 'meta.json'
    source_html = tmp / 'source.html'
    meta = {'url': 'https://example.org/a', 'content_sha256': 'x' * 64, 'text': '文章正文引用句原样出现在这里。'}
    from workflow.plan import sha256
    html = '<html><body>文章正文引用句原样出现在这里。</body></html>'
    source_html.write_text(html, encoding='utf-8')
    meta['content_sha256'] = sha256(source_html)
    archive.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return {'archive': str(archive), 'source_url': meta['url']}


class UserFactsTest(unittest.TestCase):
    def test_bind_skips_user_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _packet(Path(d))
            facts = [{'id': 'F1', 'text': '源文事实', 'evidence_quote': '文章正文引用句原样出现在这里。',
                      'source_url': 'https://example.org/a'},
                     {'id': 'F5', 'provenance': 'user',
                      'text': '用户授权创作事实：高温热泵技术长期由国外厂商主导',
                      'evidence_quote': '用户口头指令'}]
            out = bind(facts, packet)
            user = next(f for f in out if f['id'] == 'F5')
            self.assertEqual(user['provenance'], 'user')
            self.assertNotIn('source_sha256', user)  # never stamped with archive provenance

    def test_source_facts_still_verified(self):
        with tempfile.TemporaryDirectory() as d:
            packet = _packet(Path(d))
            bad = [{'id': 'F9', 'text': '伪造', 'evidence_quote': '这句不在源文里', 'source_url': 'x'}]
            with self.assertRaises(ValueError):
                bind(bad, packet)

    def test_validate_accepts_user_fact_without_https(self):
        plan = {'schema_version': 1, 'id': 'p1', 'title': 't', 'duration_seconds': 10.0,
                'aspect_ratio': '9:16',
                'facts': [{'id': 'F1', 'text': 'a', 'source_url': 'https://e.co/a'},
                          {'id': 'F5', 'provenance': 'user', 'text': '用户事实'}]}
        try:
            validate(plan)
        except ValueError as exc:
            if 'facts' in str(exc) or 'source_url' in str(exc) or 'source facts' in str(exc):
                raise AssertionError(f'validate rejected the user fact: {exc}')
            # Other required-field complaints are irrelevant to fact citation.


if __name__ == '__main__':
    unittest.main()
