"""T4 experimental request compiler. Never dispatches or changes production routing.

A: reviewed KF -> FL2VA. B: device masters -> Context-IR -> Ref2VA.
C: the same masters plus A's reviewed KF as composition guidance -> Ref2VA.
Readiness is evidence-based; a syntactically valid request is not a passed experiment.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from .film_ir import fl2va_problem, native_frames, FPS

VERSION = "asset_v3_adapter.v1"
ROOT = Path(__file__).resolve().parents[1]
REF_FIELDS = ("subject_definitions", "summary", "retention_analysis",
              "detailed_description", "overall_soundscape", "non_diegetic_music")
LABEL = re.compile(r"<(Subject|Picture|Video|Audio)\s+(\d+)>")
CAMERA_PATTERNS = {
    "hold": r"\b(?:static|stationary|locked|fixed|motionless|remains still)\b",
    "push": r"\b(?:push(?:es|ing)?[ -]in|doll(?:y|ies|ying) in|moves? (?:slowly )?closer)\b",
    "pull": r"\b(?:pull(?:s|ing)?[ -]out|doll(?:y|ies|ying) out|moves? (?:slowly )?away)\b",
    "pan": r"\bpan(?:s|ning)?\b", "orbit": r"\b(?:orbit(?:s|ing)?|circles?)\b",
    "tilt": r"\btilt(?:s|ing)?\b", "lateral": r"\b(?:truck(?:s|ing)?|lateral(?:ly)?|sideways)\b",
}


class AssetV3Error(ValueError):
    """An invalid or ambiguous experimental input; never silently falls back."""


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _require(ok, message):
    if not ok:
        raise AssetV3Error(message)


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), f"{name} must be nonempty text")
    return value


def native_profile(partition, weights_revision, evidence):
    """Declare historical native parameters; live backend proof remains separate."""
    _require(partition in ("fl2va", "ref2va"), "invalid partition")
    return {"name": f"h3_{partition}_native50", "engine": "sglang", "partition": partition,
            "model": "MiniMaxAI/MiniMax-H3", "weights_revision": weights_revision,
            "evidence": evidence, "api_num_inference_steps": 50, "num_inference_steps": 50, "short_edge": 768,
            "flow_shift": 12.0, "audio_flow_shift": 3.0, "lora": None}


def _profile(profile, route):
    _require(isinstance(profile, dict), "model_profile is required")
    expected = "fl2va" if route == "A" else "ref2va"
    _require(profile.get("partition") == expected, "model partition does not match route")
    _require(profile.get("engine") == "sglang", "first round requires native SGLang profile")
    _require(profile.get("model") == "MiniMaxAI/MiniMax-H3", "first round requires H3 base")
    _require(profile.get("api_num_inference_steps") == 50 and profile.get("short_edge") == 768,
             "native profile requires 50 sigma points and short_edge=768")
    _require(profile.get("num_inference_steps", 50) == 50, "conflicting native step aliases")
    _require(profile.get("flow_shift") == 12.0 and profile.get("audio_flow_shift") == 3.0,
             "native profile requires flow shifts 12/3")
    _require("lora" in profile and profile["lora"] is None and
             not any(profile.get(k) for k in ("source_adapters", "adapter_path", "trigger_word")) and
             not re.search(r"turbo|fal|flash", str(profile.get("name", "")), re.I),
             "Turbo/FAL/LoRA profiles are excluded from the first round")
    _text(profile.get("weights_revision"), "weights_revision")
    _text(profile.get("evidence"), "profile evidence")


def _reference(raw, purpose, index, blockers):
    ref = copy.deepcopy(raw)
    path = Path(_text(ref.get("path"), "reference path"))
    path = (path if path.is_absolute() else ROOT / path).resolve()
    _require(path.is_file() and path.stat().st_size > 0, f"missing/empty reference: {path}")
    _require(path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"), "unsupported image suffix")
    sha = file_hash(path)
    _require(ref.get("sha256") == sha, f"reference hash mismatch: {path}")
    uri = urlparse(_text(ref.get("server_uri"), "server_uri"))
    _require(uri.scheme == "file" and not uri.netloc and not uri.query and not uri.fragment
             and Path(unquote(uri.path)).is_absolute(), "use absolute server-local file:// URI")
    _require(not unquote(uri.path).startswith("/Users/"), "laptop path is not server-visible")
    review = ref.get("review") or {}
    required = ("identity", "framing") if purpose in ("first_kf", "last_kf", "composition") else ("identity",)
    if purpose == "environment":
        required = ("content",)
    if not (review.get("sha256") == sha and review.get("evidence") and
            all(review.get(k) == "pass" for k in required)):
        blockers.append(f"{purpose}_review_pending:{ref.get('id', index)}")
    ref.update(path=str(path), sha256=sha, purpose=purpose, picture_index=index,
               hosted_role="reference_image", native_role="keyframe" if purpose in
               ("first_kf", "last_kf") else "reference")
    return ref


def prepare(spec):
    """Freeze input order, source bytes, intent and profile; no network calls."""
    spec = copy.deepcopy(spec)
    _require(spec.get("schema_version") == VERSION, f"schema_version must be {VERSION}")
    route = spec.get("route")
    _require(route in ("A", "B", "C"), "route must be A/B/C")
    _require(not any(spec.get(k) for k in ("first_frame", "last_frame", "reference_images",
                                          "conditions", "continue_from")),
             "use explicit asset/keyframes only; mixed modes and continuation are excluded")
    _text(spec.get("experiment_id"), "experiment_id")
    _text(spec.get("shot_id"), "shot_id")
    _text(spec.get("prompt"), "authored prompt")
    _profile(spec.get("model_profile"), route)
    duration = spec.get("duration_seconds")
    _require(isinstance(duration, (int, float)) and not isinstance(duration, bool) and
             4 <= duration <= 15, "native duration_seconds must be 4–15")
    _require(spec.get("aspect_ratio") in ("9:16", "16:9", "1:1"), "unsupported aspect_ratio")
    _require(isinstance(spec.get("seed"), int) and not isinstance(spec["seed"], bool), "integer seed required")
    intent = spec.get("intent") or {}
    _require(isinstance(intent.get("people_count"), int) and not isinstance(intent["people_count"], bool)
             and intent["people_count"] >= 0, "explicit people_count required")
    camera = intent.get("camera") or {}
    _require(camera.get("type") in CAMERA_PATTERNS, "explicit supported camera type required")
    _require(camera.get("rate") in ("slow", "normal", "fast"), "camera rate required")
    _text(intent.get("framing"), "framing")
    asset = spec.get("asset") or {}
    _require(asset.get("type") == "device", "asset identity must be a device; register environment separately")
    for key in ("id", "version", "feature_sheet"):
        _text(asset.get(key), f"asset.{key}")
    _require(isinstance(asset.get("masters"), list) and asset["masters"], "asset masters required")
    blockers = []
    # Hash the device even in A, where it is provenance and not conditioning.
    masters = [_reference(r, "asset", i + 1, blockers) for i, r in enumerate(asset["masters"])]
    _require(len({r["sha256"] for r in masters}) == len(masters), "duplicate master bytes are not multi-view")
    asset_sha = content_hash({"id": asset["id"], "type": asset["type"], "version": asset["version"],
                              "feature_sheet": asset["feature_sheet"],
                              "masters": [r["sha256"] for r in masters]})
    consistency = asset.get("self_consistency") or {}
    if not (consistency.get("status") == "pass" and consistency.get("asset_sha256") == asset_sha
            and consistency.get("evidence")):
        blockers.append("asset_self_consistency_pending")
    if route != "A" and consistency.get("independent_multiview_verified") is not True:
        blockers.append("asset_independent_multiview_pending")
    refs = []
    environments = spec.get("environments") or []
    for env in environments:
        _require(env.get("type") == "environment", "environment must have its own registry identity")
        _text(env.get("id"), "environment.id")
        _text(env.get("description"), "environment.description")
        _require(isinstance(env.get("references", []), list), "environment.references must be a list")
    _require(len({env["id"] for env in environments}) == len(environments), "duplicate environment identities")
    if route == "A":
        _require(not any(e.get("references") for e in environments), "A conditions contain reviewed KF only")
        kfs = spec.get("keyframes") or []
        _require(1 <= len(kfs) <= 2, "A requires exactly one or two reviewed KFs")
        refs = [_reference(r, "first_kf" if i == 0 else "last_kf", i + 1, blockers)
                for i, r in enumerate(kfs)]
        problem = fl2va_problem(spec["prompt"], "first_last" if len(refs) == 2 else "first_only",
                                native_frames(duration) / FPS, len(refs) == 2)
        _require(problem is None, f"A h3_fl2va_v1 prompt invalid: {problem}")
    else:
        _require(len(masters) >= 2, "B/C requires at least two distinct device views")
        refs = masters.copy()
        for env in environments:
            for raw in env.get("references", []):
                ref = _reference(raw, "environment", len(refs) + 1, blockers)
                ref["environment_id"] = env["id"]
                refs.append(ref)
        if route == "C":
            _require(len(spec.get("keyframes") or []) == 1, "C requires A's reviewed first KF")
            ref = _reference(spec["keyframes"][0], "composition", len(refs) + 1, blockers)
            _require(spec.get("paired_a_kf_sha256") == ref["sha256"], "C KF must match paired A KF hash")
            refs.append(ref)
        else:
            _require(not spec.get("keyframes"), "B cannot silently consume a composition KF")
    _require(len(refs) <= 4, "current independent Ref2VA worker accepts at most four conditions")
    binding = {"route": route, "prompt": spec["prompt"], "intent": intent,
               "asset_sha256": asset_sha, "references": [
                   {k: r[k] for k in ("sha256", "purpose", "picture_index")} for r in refs],
               "environments": [{"id": e["id"], "description": e["description"]} for e in environments],
               "duration_seconds": duration, "aspect_ratio": spec["aspect_ratio"]}
    return {"spec": spec, "masters": masters, "references": refs, "blockers": blockers,
            "asset_sha256": asset_sha, "context_binding_sha256": content_hash(binding)}


def context_request(prepared):
    """Hosted expansion uses reference_image exclusively; originals ship again to H3."""
    spec, refs = prepared["spec"], prepared["references"]
    if spec["route"] == "A":
        return None
    asset_refs = ", ".join(f"<Picture {r['picture_index']}>" for r in refs if r["purpose"] == "asset")
    lines = [spec["prompt"], f"Define <Subject 1> as ONE device from {asset_refs}.",
             "Device invariants: " + spec["asset"]["feature_sheet"],
             "Exclude the source background from the device definition. Register environments separately."]
    subject_index = 2
    for env in spec.get("environments") or []:
        pictures = ", ".join(f"<Picture {r['picture_index']}>" for r in refs
                             if r.get("environment_id") == env["id"])
        if pictures:
            lines.append(f"Define <Subject {subject_index}> as environment {env['id']} from {pictures}: {env['description']}.")
            subject_index += 1
        else:
            lines.append(f"Target environment {env['id']} (text only): {env['description']}.")
    for ref in refs:
        if ref["purpose"] == "composition":
            lines.append(f"<Picture {ref['picture_index']}> is the [Shot 1] composition and semantic frame "
                         "anchor only. Device identity comes from <Subject 1>. This is not a hard first frame.")
    lines += ["Target intent (do not add people, cuts or camera changes): " +
              json.dumps(spec["intent"], ensure_ascii=False, sort_keys=True),
              "Write the six official Ref2VA sections; keep the numbered image order unchanged."]
    content = [{"type": "text", "text": "\n".join(lines)}]
    for ref in refs:
        mime = mimetypes.guess_type(ref["path"])[0]
        raw = Path(ref["path"]).read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == ref["sha256"], "input changed after preparation")
        uri = f"data:{mime};base64," + base64.b64encode(raw).decode()
        content.append({"type": "image_url", "image_url": {"url": uri}, "role": "reference_image"})
    body = {"model": "MiniMax-H3", "content": content, "duration": spec["duration_seconds"],
            "ratio": spec["aspect_ratio"]}
    _require(len(json.dumps(body).encode()) <= 64 * 1024 * 1024, "hosted request exceeds 64 MiB")
    return body


def _sections(prompt):
    headers = list(re.finditer(r"^([a-z_]+):", prompt, re.M))
    if [h.group(1) for h in headers] != list(REF_FIELDS):
        return None
    return {h.group(1): prompt[h.end():headers[i + 1].start() if i + 1 < len(headers)
                              else len(prompt)].strip() for i, h in enumerate(headers)}


def audit_ir(prepared, prompt):
    """Conservative lint. Passing lint still requires a hash-bound semantic review.

    Regex findings are not VLM judgments or calibrated probabilities. Ambiguity
    remains blocked for explicit review; no automatic rewriting is performed.
    """
    _text(prompt, "expanded prompt")
    issues = []
    sections = _sections(prompt)
    if not sections or not all(sections.values()):
        return {"issues": ["six_sections_missing_empty_or_out_of_order"], "semantic_verified": False}
    refs = prepared["references"]
    labels = [(kind, int(n)) for kind, n in LABEL.findall(prompt)]
    image_environments = [e for e in prepared["spec"].get("environments") or [] if e.get("references")]
    limit = 1 + len(image_environments)
    for kind, index in labels:
        if kind in ("Video", "Audio") or (kind == "Picture" and not 1 <= index <= len(refs)) or (
                kind == "Subject" and not 1 <= index <= limit):
            issues.append(f"unbound_reference:{kind}:{index}")
    definitions = sections["subject_definitions"]
    subject_lines = {}
    for line in definitions.splitlines():
        match = re.match(r"<Subject (\d+)>\s+(.+)", line)
        if match:
            subject_lines.setdefault(int(match[1]), []).append(match[2])
    if any(len(subject_lines.get(i, [])) != 1 for i in range(1, limit + 1)):
        issues.append("subject_definitions_missing_or_duplicated")
    device = " ".join(subject_lines.get(1, []))
    if re.search(r"\b(?:also establishes|also defines|environment|setting|laboratory background)\b", device, re.I):
        issues.append("device_environment_conflation_review_required")
    device_sources = {int(n) for n in re.findall(r"<Picture (\d+)>", device)}
    expected_sources = {r["picture_index"] for r in refs if r["purpose"] == "asset"}
    if device_sources != expected_sources:
        issues.append("device_sources_not_exact_master_set")
    if not any(line.startswith("<Subject 1>") and "fully_preserved" in line
               for line in sections["retention_analysis"].splitlines()):
        issues.append("device_retention_changed_review_required")
    for i, env in enumerate(image_environments, 2):
        sources = {int(n) for n in re.findall(r"<Picture (\d+)>", " ".join(subject_lines.get(i, [])))}
        if sources != {r["picture_index"] for r in refs if r.get("environment_id") == env["id"]}:
            issues.append(f"environment_sources_mismatch:{env['id']}")
    for ref in refs:
        if ref["purpose"] == "composition":
            label = f"<Picture {ref['picture_index']}>"
            if not any(line.startswith(label) and re.search(r"composition|frame|storyboard", line, re.I)
                       for line in definitions.splitlines()):
                issues.append("composition_picture_definition_missing")
            if not all(label in sections[f] for f in ("retention_analysis", "detailed_description")):
                issues.append("composition_picture_not_consumed")
    shots = re.findall(r"\[Shot (\d+)\]", sections["detailed_description"])
    if not shots or set(shots) != {"1"}:
        issues.append("unexpected_shot_or_cut")
    detail = sections["detailed_description"]
    # Remove common explicit negatives; residual human words with people_count=0
    # are held for review rather than automatically interpreted as a person.
    positive = re.sub(r"\b(?:no|without|zero)\s+(?:visible\s+)?(?:people|persons?|humans?|researchers?|scientists?|workers?)\b",
                      "", detail, flags=re.I)
    people = prepared["spec"]["intent"]["people_count"]
    if people == 0 and re.search(r"\b(?:people|person|human|researcher|scientist|worker|man|woman)\b", positive, re.I):
        issues.append("introduced_people_review_required")
    if people > 0 and re.search(r"\b(?:no|zero) (?:visible )?(?:people|persons?|researchers?)\b", detail, re.I):
        issues.append("people_removed_review_required")
    count_words = {"one": 1, "single": 1, "two": 2, "three": 3}
    stated_counts = [count_words.get(n.lower(), int(n) if n.isdigit() else -1) for n in re.findall(
        r"\b(one|single|two|three|\d+)\s+(?:adult\s+|white-coated\s+)?(?:people|persons?|researchers?|scientists?|workers?|men|women)\b",
        positive, re.I)]
    if any(count != people for count in stated_counts) or (people == 1 and re.search(
            r"\b(?:another|second|additional) (?:researcher|person|scientist|worker|man|woman)\b", positive, re.I)):
        issues.append("people_count_changed_review_required")
    camera_text = " ".join(s for s in re.split(r"[.!?\n]", detail) if re.search(r"\bcamera\b", s, re.I))
    found = {k for k, pattern in CAMERA_PATTERNS.items() if re.search(pattern, camera_text, re.I)}
    wanted = prepared["spec"]["intent"]["camera"]
    if wanted["type"] not in found:
        issues.append("requested_camera_not_explicit")
    if found - {wanted["type"]}:
        issues.append("camera_change_or_contradiction_review_required")
    direction = wanted.get("direction")
    if direction and direction not in ("none", "forward", "backward"):
        direction_pattern = {"physical_right": r"physical\s+right", "physical_left": r"physical\s+left"}.get(
            direction, re.escape(direction))
        if not re.search(r"\b" + direction_pattern + r"\b", camera_text, re.I):
            issues.append("camera_direction_missing")
    if wanted["type"] != "hold" and wanted["rate"] in ("slow", "fast"):
        if not re.search(r"\b" + wanted["rate"] + r"(?:ly)?\b", camera_text, re.I):
            issues.append("camera_rate_missing")
    return {"issues": sorted(set(issues)), "semantic_verified": False,
            "ir_sha256": text_hash(prompt), "context_binding_sha256": prepared["context_binding_sha256"],
            "method": "deterministic_lint_plus_required_hash_bound_review"}


def extract_ir(result):
    task = (result.get("final") or {}).get("task") or {}
    _require(task.get("status") in ("succeeded", "completed", "success"), "Context-IR not succeeded")
    return _text((task.get("content") or {}).get("prompt"), "Context-IR output")


def _t0_ready(spec, status):
    """Consume the common T0 receipt, including actual raw-byte artifact hashes."""
    if not status or status.get("task_id") != "T0" or status.get("state") not in ("ready", "complete"):
        return False
    ready = {r.get("id"): r.get("met") for r in status.get("readiness", [])}
    if not all(ready.get(k) is True for k in ("spec_frozen", "manifest_valid")):
        return False
    protocol = spec.get("protocol_ref") or {}
    if not re.fullmatch(r"[0-9a-f]{64}", str(status.get("spec_sha256", ""))) or (
            protocol.get("sha256") != status.get("spec_sha256")):
        return False
    try:
        if file_hash(protocol["path"]) != protocol["sha256"]:
            return False
        artifacts = status.get("artifacts") or []
        if not artifacts or not all(file_hash(r["path"]) == r["sha256"] for r in artifacts):
            return False
    except (KeyError, OSError, TypeError):
        return False
    return any(r["sha256"] == protocol["sha256"] for r in artifacts)


def _live_ir_evidence(context, prompt, evidence):
    """A text/mock rewrite never becomes official success via a review flag alone."""
    if not evidence or evidence.get("kind") != "live_context_ir" or evidence.get("synthetic") is not False:
        return False
    try:
        values = []
        for key in ("request_ref", "response_ref", "call_receipt_ref"):
            ref = evidence[key]
            if not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256", ""))) or file_hash(ref["path"]) != ref["sha256"]:
                return False
            path = Path(ref["path"])
            values.append(json.loads((path if path.is_absolute() else ROOT / path).read_text(encoding="utf-8")))
        request, raw, call = values
        if any(value.get(k) is True for value in (request, raw, call)
               for k in ("mock", "test_only", "synthetic", "simulated")):
            return False
        created_id = (raw.get("create") or {}).get("task_id")
        task = ((raw.get("final") or {}).get("task") or {})
        returned_id = task.get("id")
        return (bool(created_id) and str(created_id) == str(evidence.get("server_task_id"))
                and str(returned_id) == str(created_id) and extract_ir(raw) == prompt
                and task.get("model") == "MiniMax-H3" and task.get("task_type") == "h3_context_ir"
                and call.get("command") == "expand-context" and call.get("context_ir_submitted") is True
                and call.get("status") == "context_ir_expanded_pending_semantic_review"
                and str(call.get("request_id")) == str(created_id) and call.get("started_at") and call.get("finished_at")
                and content_hash(request) == content_hash(context))
    except (KeyError, TypeError, OSError, ValueError):
        return False


def compile_requests(spec, *, expanded_prompt=None, ir_review=None, visibility=None,
                     backend=None, t0_status=None, ir_evidence=None):
    """Return reviewable candidates and honest blockers, never submit a video."""
    p = prepare(spec)
    spec, refs, blockers = p["spec"], p["references"], p["blockers"].copy()
    context = context_request(p)
    prompt = spec["prompt"] if spec["route"] == "A" else expanded_prompt
    audit = None
    ir_evidence = (ir_review or {}).get("source_evidence") or ir_evidence
    live_ir = False
    semantic_reviewed = False
    if spec["route"] != "A":
        if prompt is None:
            blockers.append("context_ir_expansion_pending")
        else:
            audit = audit_ir(p, prompt)
            blockers.extend("ir:" + issue for issue in audit["issues"])
            review = ir_review or {}
            semantic_reviewed = (review.get("status") == "pass" and review.get("reviewer") and
                    review.get("ir_sha256") == text_hash(prompt) and
                    review.get("context_binding_sha256") == p["context_binding_sha256"] and
                    all(review.get(k) == "pass" for k in ("people", "camera", "references", "framing", "identity", "action")))
            if not semantic_reviewed:
                blockers.append("ir_semantic_review_pending")
            live_ir = _live_ir_evidence(context, prompt, ir_evidence)
            if not live_ir:
                blockers.append("context_ir_live_evidence_pending")
    if not _t0_ready(spec, t0_status):
        blockers.append("T0_frozen_protocol_pending")
    # Receipts come from the service host and bind its identity to every staged byte.
    visibility = visibility or {}
    backend = backend or {}
    if not (backend.get("native_profile_verified") is True and backend.get("checked_at") and
            backend.get("service_identity") and backend.get("profile_sha256") == content_hash(spec["model_profile"]) and
            backend.get("active_loras") == []):
        blockers.append("current_native_backend_attestation_pending")
    if not (backend.get("task") == spec["model_profile"]["partition"] and
            len(refs) in (backend.get("validated_image_condition_counts") or []) and
            spec["duration_seconds"] in (backend.get("validated_duration_seconds") or []) and
            backend.get("capability_evidence")):
        blockers.append("native_image_condition_capability_pending")
    visible = visibility.get("inputs") or []
    if not (visibility.get("checked_at") and visibility.get("service_identity") and
            visibility.get("service_identity") == backend.get("service_identity") and
            {(r.get("uri"), r.get("sha256")) for r in visible} ==
            {(r["server_uri"], r["sha256"]) for r in refs}):
        blockers.append("server_input_visibility_pending")
    conditions = []
    for i, ref in enumerate(refs):
        item = {"type": "image", "role": ref["native_role"], "uri": ref["server_uri"]}
        if spec["route"] == "A":
            item["frame_index"] = 0 if i == 0 else -1
        conditions.append(item)
    video = worker = None
    if prompt:
        video = {"model": spec["model_profile"]["model"], "task": spec["model_profile"]["partition"],
                 "prompt": prompt, "conditions": conditions,
                 "target": {"short_edge": 768, "aspect_ratio": spec["aspect_ratio"],
                            "duration_seconds": spec["duration_seconds"]}, "seconds": spec["duration_seconds"],
                 "num_outputs_per_prompt": 1, "num_inference_steps": 50, "flow_shift": 12.0,
                 "audio_flow_shift": 3.0, "seed": spec["seed"]}
        output_dir = spec.get("server_output_dir")
        _require(isinstance(output_dir, str) and output_dir.startswith("/") and
                 not output_dir.startswith("/Users/"), "server_output_dir must be an absolute server path")
        identity = content_hash({"video_request": video, "binding": p["context_binding_sha256"],
                                 "profile": spec["model_profile"]})
        worker = {"schema_version": 1, "task_id": "asset-v3-" + identity[:24], "prompt": prompt,
                  "duration_seconds": spec["duration_seconds"], "aspect_ratio": spec["aspect_ratio"],
                  "seed": spec["seed"], "generation": {"steps": 50, "short_edge": 768}, "output_dir": output_dir}
        if spec["route"] == "A":
            worker["reference_images"] = [unquote(urlparse(r["server_uri"]).path) for r in refs]
        else:
            worker.update(task="ref2va", conditions=conditions)
    provenance = {"adapter_version": VERSION, "input_spec_sha256": content_hash(spec),
                  "asset_id": spec["asset"]["id"],
                  "asset_sha256": p["asset_sha256"], "asset_version": spec["asset"]["version"],
                  "context_binding_sha256": p["context_binding_sha256"],
                  "context_request_sha256": content_hash(context) if context else None,
                  "ir_sha256": text_hash(expanded_prompt) if expanded_prompt else None,
                  "kf_sha256": [r["sha256"] for r in refs if r["purpose"] in ("first_kf", "last_kf", "composition")],
                  "profile": spec["model_profile"], "seed": spec["seed"], "input_roles_in_order": refs,
                  "experiment_id": spec["experiment_id"], "shot_id": spec["shot_id"],
                  "video_request_sha256": content_hash(video) if video else None,
                  "ir_review": ir_review, "ir_evidence": ir_evidence,
                  "visibility": visibility, "backend": backend, "t0_status": t0_status}
    return {"schema_version": VERSION, "route": spec["route"], "context_ir_request": context,
            "video_request": video, "worker_request": worker, "ir_audit": audit,
            "provenance": provenance, "blockers": sorted(set(blockers)),
            "evidence_states": {"mock_ir": bool(expanded_prompt) and spec["route"] != "A" and not live_ir,
                                "context_ir_success": live_ir,
                                "context_ir_success_semantic_reviewed": bool(live_ir and semantic_reviewed and
                                    audit is not None and not audit["issues"]), "video_acceptance_pass": False},
            "readiness": {"construction_complete": True, "video_request_ready": not blockers,
                          "video_submitted": False, "video_generation_verified": False}}


def export_bundle(bundle, bundle_ref):
    """Stable T4->T5 interchange; T5 owns transport conversion/staging.

    local_request is suitable for T5's *local* lock, server_request is the native
    service candidate. This never changes the saved bundle or clears blockers.
    """
    _require(bundle.get("schema_version") == VERSION, "not a T4 bundle")
    _require(file_hash(bundle_ref["path"]) == bundle_ref.get("sha256"), "saved bundle bytes changed")
    saved_path = Path(bundle_ref["path"])
    saved = json.loads((saved_path if saved_path.is_absolute() else ROOT / saved_path).read_text(encoding="utf-8"))
    _require(saved == bundle, "bundle object differs from the saved artifact")
    _require(bundle.get("video_request") is not None, "expanded/authored video candidate missing")
    native = copy.deepcopy(bundle["video_request"])
    local = copy.deepcopy(native)
    refs = bundle["provenance"]["input_roles_in_order"]
    _require(len(refs) == len(native["conditions"]), "condition/reference count mismatch")
    mapped = []
    purposes = {"asset": "identity", "first_kf": "keyframe", "last_kf": "keyframe",
                "composition": "composition", "environment": "environment"}
    for order, (r, c) in enumerate(zip(refs, native["conditions"])):
        _require(r["picture_index"] == order + 1 and c["uri"] == r["server_uri"] and
                 c["role"] == r["native_role"], "T4 role/order/URI mapping mismatch")
        _require(file_hash(r["path"]) == r["sha256"], "local reference changed after compilation")
        local_uri = Path(r["path"]).as_uri()
        local["conditions"][order]["uri"] = local_uri
        mapped.append({"path": r["path"], "sha256": r["sha256"], "image_id": r["id"],
                       "asset_id": r.get("environment_id", bundle["provenance"].get("asset_id")),
                       "order": order, "picture_index": r["picture_index"], "purpose": purposes[r["purpose"]],
                       "t4_purpose": r["purpose"], "local_uri": local_uri, "server_uri": r["server_uri"],
                       "hosted_role": r["hosted_role"], "native_role": r["native_role"],
                       **({"frame_index": c["frame_index"]} if "frame_index" in c else {})})
    states = copy.deepcopy(bundle.get("evidence_states") or {
        "mock_ir": bundle["route"] != "A", "context_ir_success": False,
        "context_ir_success_semantic_reviewed": False, "video_acceptance_pass": False})
    # Compilation/export cannot certify video acceptance.
    states["video_acceptance_pass"] = False
    return {"schema_version": "asset_v3_export.v1", "source_bundle": bundle_ref,
            "route": bundle["route"], "shot_id": bundle["provenance"]["shot_id"],
            "local_request": local, "server_request": native, "references": mapped,
            "profile": bundle["provenance"]["profile"],
            "hashes": {"bundle_content_sha256": content_hash(bundle), "bundle_file_sha256": bundle_ref["sha256"],
                       "local_request_sha256": content_hash(local), "server_request_sha256": content_hash(native),
                       "profile_sha256": content_hash(bundle["provenance"]["profile"]),
                       **{k: bundle["provenance"].get(k) for k in ("asset_sha256", "context_binding_sha256",
                                                                "context_request_sha256", "ir_sha256")}},
            "evidence_states": states, "blockers": bundle["blockers"], "readiness": bundle["readiness"],
            "transport_contract": "T5 locks local_request against references.path.as_uri(), stages unchanged bytes, "
                                  "then records the new server URIs and submitted payload hash; no silent role/order edits"}


def verify_server_inputs(bundle, service_identity):
    """Run on the service host. Recheck the bytes that the native API will read."""
    from datetime import datetime, timezone
    inputs = []
    for ref in bundle["provenance"]["input_roles_in_order"]:
        path = Path(unquote(urlparse(ref["server_uri"]).path))
        _require(path.is_file() and file_hash(path) == ref["sha256"], f"server file missing/changed: {path}")
        inputs.append({"uri": ref["server_uri"], "sha256": ref["sha256"]})
    return {"service_identity": _text(service_identity, "service_identity"),
            "checked_at": datetime.now(timezone.utc).isoformat(), "inputs": inputs}
