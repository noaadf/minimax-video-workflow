"""fact_gate is the single source of truth for provenance policy: every consumer
must delegate here, and the three logic call sites must not inline their own
provenance checks (regression guard for the five-gate shotgun surgery of r7)."""
import re
import unittest
from pathlib import Path
from workflow.fact_gate import is_user_fact, requires_archive_binding


class FactGateTest(unittest.TestCase):
    def test_policy(self):
        user = {'id': 'F5', 'provenance': 'user', 'text': 'x'}
        source = {'id': 'F1', 'source_url': 'https://a.co/x', 'text': 'y'}
        self.assertTrue(is_user_fact(user))
        self.assertFalse(is_user_fact(source))
        self.assertFalse(is_user_fact(None))
        self.assertFalse(requires_archive_binding(user))
        self.assertTrue(requires_archive_binding(source))

    def test_no_inline_provenance_policy_outside_fact_gate(self):
        """Logic call sites must delegate; only fact_gate may compare the literal."""
        offenders = []
        for name in ('plan.py', 'source_binding.py', 'revised_sources.py',
                     'narrative_gate.py', 'storyboard.py', 'review.py'):
            text = Path('workflow') .joinpath(name).read_text()
            for m in re.finditer(r"get\(['\"]provenance['\"]\)\s*==\s*['\"]user['\"]", text):
                offenders.append(f'{name}: inline provenance comparison')
        self.assertEqual(offenders, [], 'policy inlined instead of delegating: %r' % offenders)


if __name__ == '__main__':
    unittest.main()
