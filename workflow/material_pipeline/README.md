# Material candidate intake

- Immutable intake under `research/material_pipeline/inbox/`. The owner explicitly enabled editorial production integration on 2026-10-03; video dispatch, approval and publication remain gated.
- `package.schema.json`: JSON Schema 2020-12. `core.validate()` additionally checks local hashes, exact evidence spans, source dates, timecodes and references.
- `content_id`: canonical entity/action/date/object tuple; title/source changes cannot create a new event. Fact fingerprints use subject/predicate/object/modality.
- Exact event/fact matches and source/text overlap require editorial review. Canonical tuples need editorial consistency; this is not unrestricted semantic deduplication.
- `collection_complete`: selected collection scope archived, pending items empty. `research_ready`: source-supported facts and dated evidence available. `production_ready`: also requires transcript/shot review, production asset plan and applicable rights evidence. Approval always remains false here.
- A research-only original never becomes commercially cleared because it was downloaded. Original footage and music are not default production assets.

## Reproduce

From the project root:

```sh
python3 -m unittest discover -s workflow/material_pipeline/tests -v
python3 -m workflow.material_pipeline.build_schema
python3 -m workflow.material_pipeline.build_sample
```

The sample builder consumes archived source HTML/text, media, frame indexes and transcript; reruns return the same immutable inbox receipt. Edit a story through an explicitly reviewed revision workflow; do not overwrite its event ID.

Optional CPU tools use the existing `.venv-research` and cached Whisper model:

```sh
python3 -m workflow.material_pipeline.download PUBLIC_HTTPS_URL research/material_pipeline/media/NEW_FILE.mp4
.venv-research/bin/python -m workflow.material_pipeline.media VIDEO_PATH INDEX_DIR --root research/material_pipeline --model LOCAL_WHISPER_MODEL
```

Scene detection creates candidate boundaries. ASR creates automatic, unreviewed text. Neither tool claims full visual/audio review. Keep official text as factual authority when ASR names or numbers are wrong.

## First publication batch

`research/material_pipeline/candidate_feed.json` is the material handoff entry point.
`batches/20261003_first/candidate_catalog.json` contains four new stories; the prior
Tianwen-2 package remains separately linked. The source pool consolidates twelve
accounts/official entries, prioritizing 新智元、机器之心、量子位、昇腾CANN.
Coverage records distinguish archived listing pages from collected dated articles;
no WeChat private push history, subscription or recurring monitor is claimed.

The four new packages include sixteen original 720×1280, 24fps, six-second silent
conceptual clips, four 24-second previews, exact factual evidence, and original
narration drafts. They supplement the production pipeline, rather than represent
completed films. The main production owner still reviews narrative, synthesizes
voice, aligns subtitles and approves motion/structure. Original asset `url` is its
factual basis, not a remote download location; provenance records clarify this.

```sh
.venv-research/bin/python -m workflow.material_pipeline.build_batch
```

The builder consumes archived pages, uses local Pillow/FFmpeg, and performs only
file inbox intake. Reruns preserve immutable packages and reuse existing clips.
`candidate_only` catalog status deliberately prevents unattended `batch.produce`
admission; the production owner must perform its separate admission review.

## Controller integration

`production_bridge.py` consumes the cumulative candidate feed into the **existing**
production database (`material_intakes`, `editorial_backlog`, related `editorial_items`).
It validates original package/receipt hashes, evidence and seven-day freshness,
reviews every factual claim through the configured model, invokes the existing
`batch.produce`/`plan.generate` compiler without video backends, and requires a
model narrative review of the final exact manifest. Keyframe requests are rebound
after all manifest metadata changes. Recent eligible candidates progress to
`awaiting_storyboard`; historical and insufficiently supported packets remain held.

`refresh_feed` invokes the enabled bridge even when the candidate IDs are unchanged,
processing at most two candidates per cycle (hard maximum three). Repeats do not
replan completed items. A single lock and durable external-attempt markers prevent
duplicate requests after interruption; interrupted/failed stages require audit
before an explicit retry. An empty or fake reviewer receipt cannot advance a packet.

An optional `production_drafts/CONTENT_ID/plan.json` contains `package_sha256`, a
truthful `author`, and a standard `manifest`. This lets the material editor compile
original narration with actual package-bound, original/licensed clips instead of
asking the model to invent a replacement storyboard. The adapter validates source
hashes/timecodes and the main manifest schema, and still requires the real final
narrative review. Drafts cannot carry production approval or preflight shortcuts.
Failed external calls may resume only through `--resume CONTENT_ID` with a
hash-verified `resume_audit.json` documenting every prior external terminal outcome.
Completed factual reviews are reused; a changed final manifest gets its own review.

```sh
.venv-workflow/bin/python -m workflow.material_pipeline.refresh_feed
.venv-workflow/bin/python -m workflow.material_pipeline.production_bridge --limit 0
.venv-workflow/bin/python -m unittest workflow.material_pipeline.tests.test_production_bridge
```

Status: `research/material_pipeline/production_status.json`.
Requests: `output/production/material_intake/CONTENT_ID/production_request.json`.
These are actual production compiler outputs, not completed films or GPU jobs.
The main owner must supply actual voice and start/end images, complete whole-board
review and animatic preflight, then admit work through the existing singleton.
The bridge does not alter its controller, existing repair plans, jobs/tasks or approvals.

Read `package.json` and `receipt.json`, validate referenced files, then perform editorial selection in the main chat. Never call `workflow.research_inbox.ResearchInbox.receive/process` from this material worker: both use the queue, and processing can dispatch production.

The existing collector uses a recent-publication window and a limited source-domain allowlist. This historical 2020–2025 story needs explicit historical context; do not falsify its date to pass freshness. CNSA/CCTV domains need review in the controller if that integration is later selected. No runtime allowlist or production control was changed here.

The user enabled automatic main production on 2026-10-03. The existing singleton
`run_user_repair30` owner now calls `workflow.material_production.tick`. Finite
preparation children produce whole-film speech with real word cues, real start/end
images and an actual animatic. They never submit video jobs. The owner verifies
all receipts, admits one immutable job per committed request, adopts it into
`production_items`, and hands its ID to the existing renderer. Prepared audio and
approved preflight evidence are verified and reused. No second scheduler,
automatic publication or silent model retries are added.

Live preparation/admission state is in
`output/production/material_intake/production/state.json`; collection status also
reports these stages and the actual admitted material job count. Failed source
windows, narrative reviews, image requests and uncertain submissions are explicit
attention states. A diagnosed continuation must preserve the old attempt and
prove completed provider outcomes before reusing assets or creating a newly
reviewed preparation edition.
