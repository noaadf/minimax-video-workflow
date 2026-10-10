"""Single source of truth for fact-provenance gating.

Owner directive 2026-10-05: user-added information is accepted directly. Before
this module the policy lived in five places (plan validation, source binding,
staging-time revised-sources, admission, reviewer prompts) and every new gate
re-implemented it from memory — the r7 staging failure (fourth gate missed for
~50 minutes) is exactly that shotgun surgery. Every fact consumer must ask
THIS module; a new gate that inlines its own provenance check is a bug.
"""
USER_PROVENANCE = 'user'


def is_user_fact(fact: dict) -> bool:
    return (fact or {}).get('provenance') == USER_PROVENANCE


def requires_archive_binding(fact: dict) -> bool:
    """User facts are owner-authorized editorial claims: no archive entry, no
    verbatim-quote discipline, no https source_url. Source-verified facts keep
    the full chain (unchanged). Reviewer PROMPTS still mention user facts, but
    they are instructions, not policy — the logic lives here."""
    return not is_user_fact(fact)
