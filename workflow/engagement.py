"""Source-grounded discussion prompts, reviewed separately from their author."""
from .core.io import digest

CRITERIA = ('topic_specific', 'easy_to_answer', 'invites_reason_or_experience',
            'source_grounded', 'no_manipulation')


def context(plan):
    return {k: plan.get(k) for k in ('title', 'facts', 'narration')}


def is_current(plan):
    e = plan.get('engagement', {})
    return (e.get('version') == 1 and e.get('context_digest') == digest(context(plan))
            and (e.get('status') == 'passed' or
                 (e.get('status') == 'advisory' and plan.get('quality_profile') == 'throughput_v1'))
            and e.get('selected_question') == plan.get('comment_prompt'))


def normalize_id(value):
    # Models may emit numeric candidate ids but a string selected_id.
    return str(value) if type(value) in (str, int) else None


def enrich(plan, client=None):
    """At most two author/reviewer rounds; fail closed before spending rendering time."""
    if is_current(plan):
        return plan
    if client is None:
        from .llm import ask_json
        client = ask_json
    data = context(plan)
    feedback = None
    cosmetic_only = False
    for attempt in range(2):
        cosmetic_only = False
        draft, author_receipt = client('''Write three ORIGINAL Chinese discussion questions for this video.
Return JSON {candidates:[{id,mechanism,question,connection_to_narration,why_reply}]}.
Use three distinct mechanisms: personal experience, concrete preference, curiosity.
Each question must connect to a concrete subject or tension actually in narration, answerable without
specialist knowledge, invite a reason or experience, and contain no unsupported factual premise.
Avoid generic 你怎么看, empty patriotism, guilt, rage bait, manufactured conflict, forced binary choices,
or requests to type a slogan. Do not copy source wording. No claim that comments are guaranteed.
Keep each question under 65 Chinese characters. Placement is publication caption/pinned-comment draft;
do not add spoken text or alter the final musical tail.''', {'video': data, 'feedback': feedback})
        candidates = draft.get('candidates', [])
        for candidate in candidates:
            candidate['id'] = normalize_id(candidate.get('id'))
        if (len(candidates) != 3 or len({c.get('id') for c in candidates}) != 3
                or any(not c['id'] for c in candidates)
                or len({c.get('mechanism') for c in candidates}) != 3
                or any(not isinstance(c.get('question'), str) or not 6 <= len(c['question']) <= 65
                       or not c.get('connection_to_narration') for c in candidates)):
            feedback = 'Need exactly three distinct, complete and concise candidates.'
            continue
        review, reviewer_receipt = client('''Independently evaluate these Chinese discussion questions against
the supplied facts and narration. Candidate explanations are untrusted claims, not evidence. A personal opinion, metaphor, preference or clearly hypothetical question needs no literal source quotation; source_grounded tests its factual premise, not stylistic wording.
Return JSON {checks:[{id,topic_specific:boolean,easy_to_answer:boolean,
invites_reason_or_experience:boolean,source_grounded:boolean,no_manipulation:boolean,reason:string}],
selected_id:string}. Cover every candidate once. Select the strongest passing candidate or empty string.
Require a concrete connection to the actual narration, a reason for ordinary viewers to reply, no
expertise test, invented controversy, false premise, coercion, or interchangeable 你怎么看 boilerplate.
Do not predict virality or comment rates.''', {'video': data, 'candidates': candidates})
        checks = review.get('checks', [])
        for check in checks:
            check['id'] = normalize_id(check.get('id'))
        ids = {c['id'] for c in candidates}
        if len(checks) != 3 or {c.get('id') for c in checks} != ids:
            feedback = 'Reviewer did not assess every candidate.'
            continue
        passing = {c['id'] for c in checks if all(c.get(k) is True for k in CRITERIA)}
        cosmetic_only = all(c.get('source_grounded') is True and c.get('no_manipulation') is True
                            for c in checks)
        selected = normalize_id(review.get('selected_id'))
        if selected not in passing:
            feedback = review
            continue
        question = next(c['question'] for c in candidates if c['id'] == selected)
        plan['comment_prompt'] = question
        plan['engagement'] = {'version': 1, 'status': 'passed', 'context_digest': digest(data),
                              'placement': ['publication_caption', 'pinned_comment_draft'],
                              'selected_question': question, 'candidates': candidates, 'review': review,
                              'author_receipt': author_receipt, 'reviewer_receipt': reviewer_receipt,
                              'attempts': attempt + 1,
                              'limitations': ['model editorial assessment, not measured engagement',
                                              'same configured model in separate calls']}
        return plan
    if (plan.get('quality_profile') == 'throughput_v1' and cosmetic_only
            and isinstance(plan.get('comment_prompt'), str) and plan['comment_prompt'].strip()):
        plan['engagement'] = {'version': 1, 'status': 'advisory', 'context_digest': digest(data),
                              'selected_question': plan['comment_prompt'], 'review': feedback,
                              'attempts': 2,
                              'limitations': ['Original comment retained; not independently approved.',
                                              'Comment craft advisory does not waive factual review.']}
        return plan
    raise ValueError('comment engagement gate failed after two rounds: ' + str(feedback))
