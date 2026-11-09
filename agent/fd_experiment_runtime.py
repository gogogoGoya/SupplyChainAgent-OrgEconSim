"""Input isolation and provenance for the opt-in FD follow-up experiments."""

import hashlib
import json
from pathlib import Path
from config.fd_ablation_experiment import VERSION as FD_ABLATION_VERSION

from config.fd_experiment import (
    policy_from_meta, config_digest,
    fd2_agent_policy, fd_completion_enabled, fd2_fact_audit_policy, fd2_physical_execution_policy,
    FD2_AGENT_PROMPT_VERSION, FD2_PARTIAL_DELIVERY_PROMPT_VERSION,
    FD4_WORKLOAD_REFERENCE_VERSIONS, FD4_ROLE_CONTRACT_VERSIONS, FD4_COMPLETION_VERSIONS, FD4_REFERENCE_VERSIONS,
    FD4_DECISION_CHECK_VERSIONS, FD4_EVIDENCE_VERSIONS, FD4_NET_NEED_VERSIONS,
    FD4_SUPPORTED_PROMPT_VERSIONS, FD4_AGENT_PROMPT_VERSION,
    FD4_LEGACY_AGENT_PROMPT_VERSION, FD4_CONSISTENCY_PROMPT_VERSION,
)
from core.fd_decision_evidence import describe_operating_evidence, workload_reference_candidates
from core.fd2_resource_evidence import describe_fd2_resources
from core.fd2_fact_audit import describe_fd2_coverage


def read_policy(workspace, round_id=None):
    path = Path(workspace) / "run_meta.json"
    if not path.exists():
        return {}
    return policy_from_meta(json.loads(path.read_text(encoding="utf-8")), round_id)


def input_access_prompt(prompt, workspace, round_id):
    """Use streaming input so SDK versions initialize Python tool hooks."""
    if not read_policy(workspace, round_id):
        return prompt

    async def messages():
        yield {"type": "user", "message": {"role": "user", "content": prompt},
               "parent_tool_use_id": None, "session_id": ""}

    return messages()


def record_handover(workspace, enterprise_name, config):
    """Freeze original evidence immediately before any controller takes over."""
    workspace = Path(workspace)
    if not policy_from_meta({"scenario_config": config}, 10):
        return
    enterprise = workspace / "enterprises" / enterprise_name
    paths = list((workspace / "public" / "observer_state" / "day9").rglob("*.json"))
    paths += list((enterprise / "department").glob("*/day9/*.json"))
    observation = enterprise / "observations" / "observation_day9.txt"
    if observation.is_file():
        paths.append(observation)
    manifest = {
        "config_sha256": config_digest(config), "handover_after_round": 9,
        "files": {str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(paths)},
        "comparison_note": "Compare economic fields in these snapshots; generated object identifiers may differ.",
    }
    target = enterprise / "fd_audit" / "handover_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def write_neutral_analysis(enterprise_dir, enterprise_name, round_id, departments):
    """A schema adapter, not a state-dependent replacement analyst."""
    payload = {
        "enterprise_name": enterprise_name, "round_id": round_id,
        "enterprise_summarys": "Use authorized local observations and execution feedback.",
        "department_targets": {
            department: {
                "target": "Evaluate feasible operations within your functional responsibility.",
                "evaluation": "Record decisions and their business outcomes.",
                "reason": "No enterprise-level diagnosis is supplied.",
            } for department in departments
        },
        "analysis_provenance": {"source": "neutral_interface", "model_generated": False},
    }
    target = Path(enterprise_dir) / "analysis.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def materialize_coordination_input(workspace, enterprise_name, role, round_id, blackboard_path):
    policy = read_policy(workspace, round_id)
    if policy.get("condition") != "no_shared_coordination":
        return Path(blackboard_path)
    target = Path(workspace) / "enterprises" / enterprise_name / "department" / role / f"day{round_id}" / "coordination_input.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "round_id": round_id, "departments": {},
        "coordination_available": False,
        "instruction": "Use local state, assigned analyst guidance and your own execution feedback.",
    }, indent=2), encoding="utf-8")
    return target


def compact_fd_input(payload, recent_items=4, *, preserve_active_records=False):
    """Bound archived histories without limiting any open decision records."""
    if not isinstance(payload, dict):
        return payload
    terminal = {"completed", "fulfilled", "received", "cancelled", "canceled"}
    result = {}
    # Keep operating fields before archived histories in paged Read results.
    def historical_key(key):
        return key in terminal or key.endswith("_history") or key == "supplier_selection_events"

    for key, value in sorted(payload.items(), key=lambda item: historical_key(item[0])):
        if isinstance(value, dict):
            result[key] = compact_fd_input(value, recent_items, preserve_active_records=preserve_active_records)
        elif isinstance(value, list):
            historical = historical_key(key)
            records = value[-recent_items:] if historical else value
            if historical and preserve_active_records and key == "recruitment_history":
                active = {"\u8fdb\u884c\u4e2d", "pending", "in_progress", "created"}
                records = [item for index, item in enumerate(value)
                           if index >= len(value) - recent_items or (isinstance(item, dict)
                           and str(item.get("status") or "").lower() in active)]
            result[key] = [compact_fd_input(item, recent_items, preserve_active_records=preserve_active_records)
                           for item in records]
            if historical:
                result[key + "_summary"] = {"total": len(value), "included": len(records)}
        else:
            result[key] = value
    return result


def materialize_fd_department_inputs(workspace, enterprise_name, role, round_id, state_path, blackboard_path):
    if not read_policy(workspace, round_id):
        return Path(state_path), Path(blackboard_path)
    root = Path(workspace) / "projections" / "fd_inputs" / f"day{round_id}" / enterprise_name / role
    paths = []
    for source, name in [(Path(state_path), f"{role}.json"), (Path(blackboard_path), "blackboard.json")]:
        source_bytes = source.read_bytes()
        payload = json.loads(source_bytes)
        if name == "blackboard.json":
            # Other roles' policies are redundant; their operating summaries remain visible.
            policies = payload.get("policy_context_by_department")
            if isinstance(policies, dict):
                payload["policy_context_by_department"] = {role: policies[role]} if role in policies else {}
        policy = read_policy(workspace, round_id)
        payload = project_fd4_decision_evidence(compact_fd_input(payload,
            preserve_active_records=(policy.get("agent_prompt_policy_version") in {FD4_AGENT_PROMPT_VERSION, FD_ABLATION_VERSION}
                                     or fd2_agent_policy(policy))),
            workspace, round_id)
        payload = project_fd2_decision_evidence(payload, workspace, round_id)
        payload["fd_input_projection"] = {
            "schema_version": "fd_bounded_input.v1", "recent_history_items": 4,
            "all_open_records_retained": True,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
        root.mkdir(parents=True, exist_ok=True)
        target = root / name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(target)
    return tuple(paths)


FD_INPUT_DENIAL_LIMIT = 6
FD_INPUT_ACCESS_VERSION = "fd_input_path_recovery_2026_10_11_v1"


class FDInputLoopError(RuntimeError):
    """Stop an attempt that ignores repeated input-path corrections."""


def fd_input_path_context(spec):
    inputs = []
    for attr in ("state_path", "blackboard_path", "analysis_path", "trade_decision_card_path",
                 "observation_path", "history_projection_path", "chart_export_path"):
        value = getattr(spec, attr, None)
        if value:
            inputs.append(f"- {attr}: {Path(value).resolve()}")
    return (
        "Authoritative paths for this role and round (override Skill example paths):\n"
        + "\n".join(inputs)
        + f"\n- output_file_path: {Path(spec.output_file_path).resolve()}\n"
        "Read only these supplied inputs and authorized own feedback. Do not derive paths "
        "under department/<role>/dayN when a projection was supplied. Do not retry a rejected "
        "path with another offset. Use Write for the output, then Read it back."
    )


def fd_input_retry_context(spec, attempt_state):
    failures = attempt_state.get("failures") or []
    if not failures:
        return ""
    return (
        "\n\nPrevious attempt input-access failures (diagnostic data, not business evidence):\n"
        + json.dumps(failures[-4:], ensure_ascii=False)
        + "\nCorrect these requests rather than repeating the failed path. "
        "Do not infer action_pass from a tool error; read the authorized operating evidence.\n"
        + fd_input_path_context(spec)
    )


def fd4_ablation_context(workspace, round_id):
    """Explain absent interfaces without adding operating facts or action hints."""
    policy = _fd4_agent_prompt_policy(workspace, round_id)
    if policy.get("agent_prompt_policy_version") != FD4_AGENT_PROMPT_VERSION:
        return ""
    if policy.get("condition") == "no_analyst":
        return (
            """
Input availability overrides generic analysis-reading instructions:
- analysis.json is a neutral interface without model diagnosis, dynamic targets or recommendations. Read it, then decide from authorized state, shared inputs and own feedback. Missing diagnosis is neither a file error nor proof that business stopped.
- Do not recover diagnosis from old analysis, other analysis files or raw enterprise observations. Preserve permissions and standard output, then actually Write and Read.
"""
        )
    if policy.get("condition") == "no_shared_coordination":
        return (
            """
Input availability overrides generic coordination/communication instructions:
- The supplied coordination/blackboard is intentionally empty. departments={} and coordination_available=false are valid. Read once; do not seek raw blackboards, merged messages or other departments.
- Do not create, read or write *_communication.json; no sidecar is required. Empty coordination alone is not grounds for pass. Use authorized department facts, own feedback and the analysis interface, then Write and Read.
- Analyst may still use enterprise observations, charts and history explicitly supplied to it. Departments gain no permission to those raw files.
"""
        )
    return ""


def fd4_hr_reference_context(workspace, round_id, spec):
    policy = _fd4_agent_prompt_policy(workspace, round_id)
    if spec.role.lower() != "hr" or policy.get("agent_prompt_policy_version") not in FD4_WORKLOAD_REFERENCE_VERSIONS:
        return ""
    sources = {}
    for name, attr in (("state", "state_path"), ("coordination", "blackboard_path"), ("analysis", "analysis_path")):
        path = getattr(spec, attr, None)
        if not path:
            continue
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            sources[name] = payload
    references = {}
    for department in ("PRODUCTION", "PROCUREMENT", "SALES", "INVENTORY", "HR", "FINANCE"):
        work = workload_reference_candidates(sources, department, round_id)
        if work:
            references[department] = work
    return ("""
Workload references already present in authorized inputs (a fact index, not a hiring recommendation):
"""
            + json.dumps(references, ensure_ascii=False)
            + """
Read the referenced authorized inputs and verify actual staffing coverage and timing. Prefer these existing object references; do not guess production fields. No listed workload evidence does not imply zero real work.
""")


def configure_input_access(options, spec, workspace, client_dir, enterprise_name, round_id,
                           attempt_state=None, output_validator=None, output_warning_provider=None):
    """Enforce the same local read boundary in all arms, with one BB exception."""
    policy = read_policy(workspace, round_id)
    if not policy:
        return options
    from claude_agent_sdk import HookMatcher

    enterprise = (Path(workspace) / "enterprises" / enterprise_name).resolve()
    role = spec.role.lower()
    skill_roots = [
        (Path(client_dir) / ".claude" / "skills" / spec.skill_name).resolve(),
        (enterprise / ".claude" / "skills" / spec.skill_name).resolve(),
    ]
    readable = {Path(spec.output_file_path).resolve()}
    writable = {Path(spec.output_file_path).resolve()}
    for attr in ("state_path", "blackboard_path", "analysis_path", "trade_decision_card_path",
                 "observation_path", "history_projection_path", "chart_export_path"):
        value = getattr(spec, attr, None)
        if value:
            readable.add(Path(value).resolve())
    sidecar = getattr(spec, "communication_file_path", None)
    if sidecar and policy["condition"] != "no_shared_coordination":
        readable.add(Path(sidecar).resolve())
        writable.add(Path(sidecar).resolve())
    audit_path = enterprise / "fd_audit" / f"day{round_id}" / f"{role}_access.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    input_reads = {}
    denied_counts = {}
    recorded_denials = set()
    attempt_state = attempt_state if attempt_state is not None else {}
    path_context = fd_input_path_context(spec) + fd4_ablation_context(workspace, round_id)
    completion_enabled = output_validator is not None and fd_completion_enabled(policy, role, round_id)

    def note_failure(tool, path, failure_type, tool_use_id):
        if tool_use_id and tool_use_id in recorded_denials:
            return
        if tool_use_id:
            recorded_denials.add(tool_use_id)
        key = (tool, str(path) if path else None)
        denied_counts[key] = denied_counts.get(key, 0) + 1
        failure = {"tool": tool, "path": key[1], "failure_type": failure_type,
                   "rejected_requests": denied_counts[key]}
        attempt_state.setdefault("failures", []).append(failure)
        if denied_counts[key] >= FD_INPUT_DENIAL_LIMIT:
            attempt_state["loop_reason"] = (
                f"{tool} failed {denied_counts[key]} times for {key[1]!r}; "
                f"failure_type={failure_type}. Use the supplied authorized paths."
            )

    async def guard(data, tool_use_id, context):
        tool = data.get("tool_name", "")
        args = data.get("tool_input") or {}
        if not isinstance(args, dict):
            args = {}
        path = None
        if tool == "Skill":
            allowed = args.get("skill") == spec.skill_name
        elif (tool in {"Read", "Write"} and isinstance(args.get("file_path"), str)
              and args["file_path"].strip()):
            raw = args["file_path"]
            path = Path(raw)
            if not path.is_absolute():
                path = Path(options.cwd) / path
            path = path.resolve()
            if tool == "Write":
                allowed = path in writable
            else:
                allowed = path in readable or any(path.is_relative_to(root) for root in skill_roots)
                # History is restricted to this role's states/actions/results, not
                # sibling departments, raw enterprise observations or run metadata.
                local_root = enterprise / "department" / role
                if path.is_relative_to(local_root):
                    suffix = path.relative_to(local_root).parts
                    if len(suffix) == 2 and suffix[0].startswith("day") and suffix[0][3:].isdigit():
                        day = int(suffix[0][3:])
                        allowed_names = {f"{role}.json", f"{role}_DayEnd.json", f"{role}_action.json",
                                         f"{role}_result.json", "trade_decision_card.json",
                                         f"pre_{role}_action.json", f"pre_{role}_result.json"}
                        if "/projections/fd_inputs/" in str(getattr(spec, "state_path", "")):
                            allowed_names -= {f"{role}.json", f"{role}_DayEnd.json"}
                        allowed = allowed or (max(0, round_id - 5) <= day <= round_id and path.name in allowed_names)
                        # Execution retries may inspect only their own current error.
                        allowed = allowed or (day == round_id and (path.name == f"{role}_error.json"
                                              or path.name.startswith(f"{role}_error_"))
                                              and path.suffix == ".json")
        else:
            allowed = False
        digest = None
        if allowed and tool == "Read" and path and path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        reason = "Use the supplied role-scoped inputs and output paths."
        failure_type = "input_boundary" if path else "invalid_tool_input"
        if allowed and tool == "Read" and digest and path not in writable:
            # Distinct pages and changed inputs remain readable; output readback is exempt.
            key = (str(path), args.get("offset"), args.get("limit"), digest)
            input_reads[key] = input_reads.get(key, 0) + 1
            if input_reads[key] > 2:
                allowed = False
                failure_type = "repeated_input_range"
                reason = (
                    "This unchanged input range has already been read twice in this attempt. "
                    "Use the returned evidence; if an unread section is necessary, specify offset/limit. "
                    f"Complete {Path(spec.output_file_path).name} using Write, then Read it back."
                )
        if not allowed:
            note_failure(tool, path, failure_type, tool_use_id)
            reason += (
                f"\nRejected {tool} path: {str(path)!r}; failure_type={failure_type}.\n"
                + path_context
            )
        with audit_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"tool": tool, "path": str(path) if path else None,
                                     "allowed": allowed, "sha256": digest,
                                     "condition": policy["condition"], "tool_use_id": tool_use_id,
                                     "input_access_version": FD_INPUT_ACCESS_VERSION,
                                     "reason": reason,
                                     "failure_type": failure_type if not allowed else None}) + "\n")
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow" if allowed else "deny",
            "permissionDecisionReason": reason,
        }}

    async def skill_context(data, tool_use_id, context):
        args = data.get("tool_input") or {}
        if data.get("tool_name") != "Skill" or not isinstance(args, dict) or args.get("skill") != spec.skill_name:
            return {}
        guidance = (fd4_decision_guidance(workspace, round_id, role)
                    if policy.get("agent_prompt_policy_version") in FD4_ROLE_CONTRACT_VERSIONS
                    else fd4_reference_contract(workspace, round_id, role))
        guidance += fd2_decision_guidance(workspace, round_id, role)
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": path_context + guidance
                                       + fd4_hr_reference_context(workspace, round_id, spec)}}

    async def read_failure_context(data, tool_use_id, context):
        if data.get("tool_name") != "Read":
            return {}
        args = data.get("tool_input") or {}
        raw = args.get("file_path") if isinstance(args, dict) else None
        path = None
        if isinstance(raw, str) and raw.strip():
            path = Path(raw)
            path = (path if path.is_absolute() else Path(options.cwd) / path).resolve()
        already_recorded = bool(tool_use_id and tool_use_id in recorded_denials)
        note_failure("Read", path, "read_tool_failed", tool_use_id)
        if not already_recorded:
            with audit_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps({
                    "tool": "Read", "path": str(path) if path else None,
                    "allowed": False, "sha256": None, "condition": policy["condition"],
                    "tool_use_id": tool_use_id, "event": "PostToolUseFailure",
                    "input_access_version": FD_INPUT_ACCESS_VERSION,
                    "failure_type": "read_tool_failed", "reason": path_context,
                }) + "\n")
        return {"hookSpecificOutput": {"hookEventName": "PostToolUseFailure",
                                       "additionalContext": path_context}}

    async def output_completed(data, tool_use_id, context):
        args = data.get("tool_input") or {}
        raw = args.get("file_path") if isinstance(args, dict) else None
        if not isinstance(raw, str):
            return {}
        path = Path(raw)
        path = (path if path.is_absolute() else Path(options.cwd) / path).resolve()
        target = Path(spec.output_file_path).resolve()
        if path != target or not path.is_file():
            return {}
        response = data.get("tool_response")
        if isinstance(response, dict) and response.get("is_error"):
            return {}
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if data.get("tool_name") == "Write":
            attempt_state["output_write_sha256"] = digest
            attempt_state.pop("output_readback_sha256", None)
        elif data.get("tool_name") == "Read":
            attempt_state["output_readback_sha256"] = digest
        if policy.get("agent_prompt_policy_version") in FD4_ROLE_CONTRACT_VERSIONS or fd2_agent_policy(policy):
            error = output_validator()
            readback_only = error and (error.startswith("Read back the latest Write")
                                      or error.startswith("Read has not successfully checked the latest Write"))
            count = attempt_state.get("write_validation_feedback_count", 0)
            if error and not readback_only and count < 2:
                attempt_state["write_validation_feedback_count"] = count + 1
                with audit_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"tool": data.get("tool_name"), "event": "OutputValidation",
                        "path": str(target), "condition": policy["condition"], "reason": error,
                        "feedback_count": count + 1}) + "\n")
                return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": (
                    'The file was written but has not passed controller validation: ' + error
                    + """
Recheck the conclusion against current facts, then Write the full correction and Read it. Changing reference syntax alone cannot fix unsupported business reasoning. Do not reinvoke an expanded Skill."""
                    + fd4_hr_reference_context(workspace, round_id, spec)
                )}}
        if ((policy.get("agent_prompt_policy_version") == FD4_AGENT_PROMPT_VERSION or fd2_fact_audit_policy(policy))
                and output_warning_provider is not None and data.get("tool_name") == "Read"
                and not attempt_state.get("semantic_warning_reported")):
            try:
                warnings = output_warning_provider()
            except (TypeError, ValueError, AttributeError, OverflowError) as exc:
                warnings = [f"Semantic audit could not inspect this proposal: {exc}"]
            if warnings:
                attempt_state["semantic_warning_reported"] = True
                with audit_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"tool": "Read", "event": "OutputWarning", "path": str(target),
                        "condition": policy["condition"], "warnings": warnings, "blocking": False}) + "\n")
                prefix = 'Nonblocking factual reminder: ' if fd2_fact_audit_policy(policy) else 'Nonblocking audit warning: '
                autonomy = ('Keep or adjust the conclusion using current evidence; the executor checks actual feasibility.'
                            if fd2_fact_audit_policy(policy) else
                            'Departments may keep or adjust legal actions using current state; the executor checks feasibility.')
                return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": (
                    prefix + "; ".join(warnings)
                    + """
This is not an execution error and requires neither rewriting, Analyst obedience nor pass."""
                    + autonomy
                )}}
        return {}

    async def completion_guard(data, tool_use_id, context):
        error = output_validator()
        if not error:
            return {}
        failures = attempt_state.setdefault("completion_failures", [])
        failures.append(error)
        blocked = len(failures) <= 2
        with audit_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"tool": "Stop", "event": "Stop", "allowed": not blocked,
                "path": str(Path(spec.output_file_path).resolve()), "condition": policy["condition"],
                "reason": error, "completion_blocks": min(len(failures), 2)}) + "\n")
        if not blocked:
            return {"systemMessage": "Output completion correction limit reached: " + error}
        return {"decision": "block", "reason": (
            'Task incomplete: explanations or printed JSON are not completion. Validation: ' + error
            + f'\nReuse current evidence and Write the full corrected file to {spec.output_file_path}, then Read it back. Partial snippets are insufficient; do not reinvoke an already expanded Skill.'
        )}

    hooks = dict(options.hooks or {})
    hooks["PreToolUse"] = list(hooks.get("PreToolUse", [])) + [HookMatcher(matcher=".*", hooks=[guard])]
    hooks["PostToolUse"] = list(hooks.get("PostToolUse", [])) + [HookMatcher(matcher="Skill", hooks=[skill_context])]
    hooks["PostToolUseFailure"] = list(hooks.get("PostToolUseFailure", [])) + [HookMatcher(matcher="Read", hooks=[read_failure_context])]
    if completion_enabled:
        hooks["PostToolUse"] += [HookMatcher(matcher="Write|Read", hooks=[output_completed])]
        hooks["Stop"] = list(hooks.get("Stop", [])) + [HookMatcher(hooks=[completion_guard])]
    options.hooks = hooks
    return options


def accessed_inputs(workspace, enterprise_name, role, round_id):
    """Return existing inputs admitted by the tool hook, not prompt mentions."""
    path = Path(workspace) / "enterprises" / enterprise_name / "fd_audit" / f"day{round_id}" / f"{role.lower()}_access.jsonl"
    if not path.exists():
        return set()
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    failed_ids = {entry["tool_use_id"] for entry in entries
                  if entry.get("event") == "PostToolUseFailure" and entry.get("tool_use_id")}
    return {entry["path"] for entry in entries
            if entry.get("tool") == "Read" and entry.get("allowed") and entry.get("sha256")
            and entry.get("tool_use_id") not in failed_ids}


def output_access_evidence(workspace, enterprise_name, role, round_id, output_path):
    """Admission evidence is not proof a Write completed successfully."""
    path = Path(workspace) / "enterprises" / enterprise_name / "fd_audit" / f"day{round_id}" / f"{role.lower()}_access.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
    target = str(Path(output_path).resolve())
    failed = {row["tool_use_id"] for row in entries
              if row.get("event") == "PostToolUseFailure" and row.get("tool_use_id")}
    return {
        # Diagnostic events share this log but are not tool admissions.
        "admitted_write_requests": sum(1 for row in entries
                                       if row.get("tool") == "Write" and row.get("allowed") is True
                                       and row.get("path") == target),
        "admitted_readback_requests": sum(1 for row in entries
                                          if row.get("tool") == "Read" and row.get("allowed") is True
                                          and row.get("path") == target and row.get("sha256")
                                          and row.get("tool_use_id") not in failed),
        "admission_does_not_prove_tool_success": True,
    }


def procurement_guidance():
    return """

State-dependent shortage response:
- Derive uncovered requirements from accepted orders, recipes, usable stock,
  remaining production plans and genuinely open inbound purchases; do not double count.
- Compare supplier processing plus transit time AND production/fulfillment delay
  against visible deadlines. Fast delivery is valuable only if it can unlock useful output.
- Compare landed cost and urgency; consider a small urgent tranche and economical
  subsequent supply when justified, without prescribing a supplier, quantity or round.
- Match material purchases to usable and pending capacity, staffing, cash and demand;
  discuss a separate capacity bottleneck with production rather than accumulating inputs.
- Recheck actual arrivals, consumption and delivery before ordering again. Explain
  the evidence and trade-off in the existing reason fields; do not invent future demand.
"""


def project_fd2_decision_evidence(payload, workspace, round_id):
    """Retain facts and constraints; remove generated quantity commands for FD2 AG."""
    policy = read_policy(workspace, round_id)
    if not fd2_agent_policy(policy) or not isinstance(payload, dict):
        return payload
    autonomous = policy.get("agent_prompt_policy_version") == FD2_AGENT_PROMPT_VERSION
    partial_delivery = autonomous or policy.get("agent_prompt_policy_version") == FD2_PARTIAL_DELIVERY_PROMPT_VERSION
    removed = []
    hints = {"recommended_plan_quantity", "recommended_daily_capacity", "should_recover_now",
             "should_recover_any", "has_service_recovery_candidate", "suggested_quantity_band",
             "recommended_urgency"}
    if partial_delivery:
        hints |= {"recovery_target_material_quantity", "recipe_daily_demand_equivalent",
                  "shortage_to_recovery", "coverage_gap_quantity", "open_coverage_sufficient",
                  "recovery_stage", "landed_cost_for_suggested_quantity"}

    def project(value, path=()):
        if isinstance(value, list):
            return [project(item, path + (str(index),)) for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            generated = any(part in {"recovery_guard", "cash_guard", "margin_guard",
                "procurement_recovery_signal", "recipe_recovery_signal_by_material"} for part in path)
            recovery_signal = any(part in {"procurement_recovery_signal", "recipe_recovery_signal_by_material"}
                                  for part in path)
            expansion_signal = "capacity_expansion_signal" in path
            if autonomous and (
                    ("market_growth_signal" in path and key in {"should_develop_market", "recommended_action", "recommended_actions"})
                    or (key == "preferred_actions" and path == ("agent_decision_brief",))
                    or (key == "capacity_budget" and "recovery_guard" in path)):
                removed.append(".".join(path + (key,)))
                continue
            if partial_delivery and (
                    (key == "fast_path_rule" and path == ("agent_decision_brief",))
                    or (recovery_signal and key == "enabled")
                    or (expansion_signal and key in {"should_prioritize_build_line_review",
                        "recommended_action_when_feasible", "reason", "committed_order_quantity",
                        "total_downstream_demand"})):
                removed.append(".".join(path + (key,)))
                continue
            if ((generated and key in hints) or (key == "must_respect_recovery_guard" and path
                    and path[-1] in {"action_constraints", "allowed_action_constraints"})):
                removed.append(".".join(path + (key,)))
            else:
                result[key] = project(item, path + (key,))
                if autonomous and key == "market_growth_signal" and isinstance(result[key], dict):
                    result[key]["recommendation_mode"] = "evidence_only"
        return result

    result = project(payload)
    if partial_delivery:
        # Rebuilding an already projected input must not restore its stale index.
        for key in ("fd_operating_evidence", "fd_resource_evidence", "fd_evidence_contract", "fd_coverage_evidence"):
            result.pop(key, None)
        additions = {"fd_coverage_evidence": describe_fd2_coverage(result, round_id)} if autonomous else {}
        return {"fd_evidence_contract": {"schema_version": "fd2_delivery_coverage.v3" if autonomous else "fd2_delivery_coverage.v2",
                    "agent_prompt_policy_version": policy["agent_prompt_policy_version"],
                    "scope": "supplied_input_only", "action_recommendations": False,
                    "analyst_targets_are_advisory": True,
                    "recovery_guard_use": "feasibility_only_not_action_or_quantity_command",
                    "raw_operating_facts_retained": True,
                    "removed_prescriptive_fields": removed},
                **additions, "fd_resource_evidence": describe_fd2_resources(result, round_id),
                "fd_operating_evidence": describe_operating_evidence(result, round_id), **result}
    return {"fd_operating_evidence": describe_operating_evidence(result, round_id),
            "fd_evidence_contract": {"schema_version": "fd2_delivery_coverage.v1",
                "scope": "supplied_input_only", "action_recommendations": False,
                "recovery_guard_use": "feasibility_only_not_action_or_quantity_command",
                "raw_operating_facts_retained": True, "removed_prescriptive_fields": removed}, **result}


def fd2_decision_guidance(workspace, round_id, role):
    policy = read_policy(workspace, round_id)
    if not fd2_agent_policy(policy) or round_id > int(policy.get("formal_end", 19)):
        return ""
    role = str(role).lower()
    if role not in {"analyst", "procurement", "production", "sales", "hr"}:
        return ""
    if policy.get("agent_prompt_policy_version") in {FD2_AGENT_PROMPT_VERSION, FD2_PARTIAL_DELIVERY_PROMPT_VERSION}:
        text = _fd2_partial_delivery_guidance(role, int(policy.get("formal_end", 19)))
        if policy.get("agent_prompt_policy_version") == FD2_AGENT_PROMPT_VERSION:
            text = text.replace("summary.available_capacity/capacity_budget", "summary.available_capacity")
            text += _fd2_autonomous_guidance(role, fd2_physical_execution_policy(policy))
        return text
    text = """
Fulfillment coverage and autonomous decisions override generic recovery recommendations, without adding an action gate:
- Use authorized inputs and own feedback only. Analyst targets, recovery signals and recommended quantities are advisory; correct them against facts. Disagreement does not block legal execution. No decision_check or extra references are required.
- Deduplicate demand by status/deadline, then subtract available finished goods and timely remaining WIP once. confirmed/stale/demand may overlap. Irremediable breached orders create no new procurement/production requirement.
- Convert the net production gap through actual recipes and subtract on-hand materials and timely open purchases. Received purchases are not new in-transit supply; pending=0 does not prove shortage. Recovery targets are explanatory, not minimum orders.
- Compare supplier handling + transport + production/delivery time with each deadline. Assess the delivery value of earlier arrival before landed cost; fastest is not always best. If only one purchase is allowed, select the most necessary one rather than assuming split-order permission.
- Use actual supplier candidates, materials, prices, MOQ and available supply. For a gap below MOQ, compare the smallest legal batch with waiting; do not order below MOQ or invent suppliers.
- Establish procurement, production and expansion necessity separately. A new idle line does not remove material/staffing constraints. Neither forbid nor require expansion by default; with no gap, pass is valid.
- Keep reason concise: effective demand, coverage, residual gap and arrival/completion time. Preserve unknowns, do not guess absent fields, then actually Write the full standard JSON and Read it back. Do not add reports or reinvoke an expanded Skill.
"""
    if role == "analyst":
        text += """
- Check conditional completion times of visible purchases, plans and staffing in observation.fd_forward_evidence against source state and orders. Expected events are not future observations or currently available resources.
- Give trigger/stop conditions for net procurement, production resources/deadlines, sales commitments and persistent HR gaps. Do not prescribe a unique quantity/supplier or nonexistent manual staff allocation. Purchase completion and plan submission are not delivery; stop new commitments once covered.
"""
    elif role == "procurement":
        text += """
- When material stock and timely incoming supply cover net need, recovery signals, empty pipelines or procurement targets do not justify another order. Assess emergency batches and later economic replenishment separately.
"""
    elif role == "production":
        text += """
- Check current materials, workers, lines, cash and completion time, limiting output to the real gap. Executor reductions do not replace feasibility reasoning; do not produce all historical backlog mechanically.
"""
    elif role == "sales":
        text += """
- Separate old commitments from new orders. Decrement uncommitted stock and reliable timely WIP per order; margin or future-purchase advice does not justify infeasible acceptance.
"""
    else:
        text += """
- Hiring requires persistent work and available-staff gaps. Zero headcount alone is not failure, hiring needs no existing HR worker, and work actions allocate staff.
"""
    return text


def _fd2_partial_delivery_guidance(role, formal_end):
    common = """
Fulfillment coverage and autonomous decisions override recovery, fastest-first and fixed-target guidance without adding an action gate:
- Analyst/target/raw_target/agent_decision_brief.target are advisory. Correct them against current evidence. No decision_check or extra references are required; disagreement does not block legal execution.
- fd_resource_evidence indexes existing inventory, recipes and feasibility caps. Caps are not demand or batch commands. Retain real cash, material, staffing, line, state and execution-budget constraints; do not rely on automatic quantity reduction.
- Deduplicate valid commitments by product/order ID and deadline. confirmed/demand/stale/recovery summaries and suborders may overlap. Irremediable breached/expired/completed orders require no new purchases or plans.
- Keep finished-good and material units separate. G=max(0,effective demand+grounded buffer-available finished goods-timely remaining WIP). Convert G through recipes; subtract materials and timely open purchases once. inventory_position already subtracts backlog; do not subtract again or infer reserved finished goods solely from a negative position. Preserve unknowns.
- Insufficient resources for all demand do not imply no useful production. Compare feasible partial production now with waiting, identifying real deliveries improved. If passing, explain why the feasible part has no delivery value or which actual constraint blocks it.
- Plan submission, purchase placement, active_plans>0 and material arrival are not completed delivery. New plans may not produce this round; use production durations, settlement feedback and deadlines without inventing action parameters.
- Explain aggregate versus net gaps using authorized coordination/analysis. Do not invent missing orders or read unauthorized files. State the main coverage/timing tradeoff briefly, then Write and Read complete standard JSON without reinvoking Skills or adding reports.
"""
    roles = {
        "analyst": """
Analysis order:
- Deduplicate current orders; separate valid, completed and irremediable demand. Cover using current finished goods and remaining WIP, not total backlog or original plan quantities.
- Check visible open purchases, completions and staff release in fd_forward_evidence. Expected events are conditional, not current resources; count each arrival/WIP once across containers.
- Distinguish feasible partial production now from the residual after replenishment. With available staff and some feasible output, do not repeatedly defer the first plan until all backlog has materials.
- Give bottlenecks and trigger/stop conditions for four departments, without unique batches/suppliers/minimum output. Derive material needs after finished-good coverage; stop replenishment targets and update the summary when covered. Evaluate delivery and gap closure, not full utilization or plan existence.
- Expansion requires unmet timely capacity and a line that becomes available in time. Low past utilization may reflect materials or diagnosis errors and proves neither expansion nor its absence. Available staff require no HR allocation or hiring.
""",
        "production": """
Production order:
- Read materials, finished goods and capacity from recovery_guard.raw_material_snapshots, material_feasible_quantity, summary.available_capacity/capacity_budget and recipes. Missing top-level raw_materials does not prove no materials.
- Calculate coverage, then limit quantity and daily_capacity by actual materials, available workers, occupied lines, cash and execution budget. Partial coverage can still justify starting; do not double-count orders to invent a shortage.
- Check whether partial output meets the earliest uncovered deadlines, using current durations and settlement feedback. Do not assume unlimited concurrent plans on one line.
- Evaluate expansion separately from current production using actual capacity bottlenecks, construction time and delivery benefits. Expansion cannot resolve material shortage. Stop duplicate plans after coverage and do not mechanically produce all breached backlog.
""",
        "procurement": """
Procurement order:
- Check linked_products finished-good coverage, net production need and recipes. Subtract material stock and timely open purchases once. Recovery targets, backlog_signal, pending=0 or Analyst quantities alone do not prove necessity.
- With sufficient materials, pass is valid; no incoming purchases does not justify buying more. Received is not in transit. Explain staffing/capacity and material constraints separately.
- Inspect every legal supplier_candidates/materials_suppliers_matrix option. Registered suppliers are not necessarily the only legal options when the interface allows candidates at placement. supplier_option_ranking is not a command.
- First assess handling+transport+production/delivery against real deadlines; then compute landed cost=quantity*price+base_fee+quantity*unit_fee for a chosen legal quantity, checking MOQ and supply. Avoid Fast/air premiums without delivery benefit. A single legal purchase opportunity does not authorize split orders.
""",
        "sales": """
Sales order:
- Explain existing uncovered commitments and new orders competing for deadlines. Decrement uncommitted stock/timely WIP per order; do not reuse coverage or assume another department passing guarantees production.
- Same-day procurement arrival does not guarantee same-day output/delivery. Accept only reliably covered orders or a grounded feasible response; extra acceptance does not offset old breaches.
""",
        "hr": """
Staffing order:
- Assess actual executable work, available/allocated staffing, pending arrivals and releases for a persistent gap. Zero staff or full allocation does not automatically justify recruitment. Hiring needs no existing HR worker.
- Hiring cannot fix a material/planning error. Available workers are assigned by legal work actions, not an invented manual-allocation action.
""",
    }
    return common + roles[role] + (
        f'\n- Formal observation ends after R{formal_end}. Real unexpired demand beyond this window may still be served, but future purchases/plans are not recovered output in this window. Do not invent later demand or use late arrivals to rescue expired deadlines.\n'
    )


def _fd2_autonomous_guidance(role, physical_execution):
    text = """
Current evidence and local corrections:
- fd_coverage_evidence indexes separate coverage views; do not add them. uncovered_committed_quantity excludes unaccepted orders, arbitrary buffers and expansion benefits and is not a production answer. Preserve unknowns.
- Nonblocking warnings are returned at most once per attempt readback. They do not require rewriting, Analyst obedience or pass. Keep or adjust the decision using current facts. Mention stale analysis briefly in reason/coordination rather than initiating a whole-round conference.
"""
    if physical_execution:
        text += """- The shared execution contract no longer converts stock-uncovered acceptance into rejection or crops plans to recovery recommendations. Business modules still validate order status, staffing, materials, lines and cash. Assess deadlines and quantities yourself.
"""
    if role == "analyst":
        text += """
- State current valid demand, available finished goods, timely remaining WIP and unknowns before diagnosis. Optional top-level coverage_review may map each actual product_id to demand, finished_goods, remaining_wip, buffer, net_gap (numbers/null) and production_required/procurement_required (booleans). Omission is legal and adds no reference format.
- Negative chart coverage is conditional on existing stock/submitted plans, not proof of material shortage. If materials permit output, assess production rather than automatic emergency buying. Distinguish positive material coverage from unfinished goods; update stale negative-gap claims when current charts/observations improve.
"""
    elif role == "procurement":
        text += """- In existing reason, compare no purchase, waiting for valid incoming supply, emergency batches and economic replenishment by actual delivery/cost. Total orders, falling stock or unproduced goods alone do not establish material need. No fixed supplier switch or purchase count is required.
"""
    elif role == "sales":
        text += """
- market_growth_signal=evidence_only supplies no market-development answer. Decide from actual order access, uncommitted goods, development cost/time, staffing and return evidence. Low coverage alone is insufficient.
- Separate current recovery from long-term market investment. Legal investment may extend beyond evaluation, but explain visible returns and unrealized risk. Cash alone does not justify investment or invented future demand.
"""
    elif role == "production":
        text += """- With demand covered by goods/timely WIP, explain any new plan through a real buffer or other benefit. idle/allow_service_recovery means feasible, not necessary.
"""
    return text


def _fd4_agent_prompt_policy(workspace, round_id):
    if round_id is None:
        return {}
    policy = read_policy(workspace, round_id)
    if (policy.get("scenario") != "FD4"
            or policy.get("condition") not in {"full", "no_analyst", "no_shared_coordination"}
            or int(round_id) > int(policy.get("formal_end", 19))
            or policy.get("agent_prompt_policy_version") not in FD4_SUPPORTED_PROMPT_VERSIONS):
        return {}
    return policy


def project_fd4_decision_evidence(payload, workspace, round_id):
    """Remove prescriptive recovery hints, not facts or execution constraints."""
    policy = _fd4_agent_prompt_policy(workspace, round_id)
    if (policy.get("agent_prompt_policy_version") not in FD4_NET_NEED_VERSIONS
            or not isinstance(payload, dict)):
        return payload
    removed = []
    suggestions = {
        "recommended_plan_quantity", "recommended_daily_capacity",
        "should_recover_now", "should_recover_any", "has_service_recovery_candidate",
    }

    def project(value, path=()):
        if isinstance(value, list):
            return [project(item, path + (str(index),)) for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            in_guard = any(part in {"recovery_guard", "cash_guard", "margin_guard"} for part in path)
            if ((in_guard and key in suggestions)
                    or (key == "must_respect_recovery_guard" and path
                        and path[-1] in {"action_constraints", "allowed_action_constraints"})):
                removed.append(".".join(path + (key,)))
                continue
            result[key] = project(item, path + (key,))
        return result

    result = project(payload)
    result["fd_evidence_contract"] = {
        "schema_version": "fd4_net_need_evidence.v2",
        "agent_prompt_policy_version": policy["agent_prompt_policy_version"],
        "recovery_guard_use": "feasibility_only_not_action_or_quantity_command",
        "raw_operating_facts_retained": True,
        "cash_staff_material_capacity_constraints_retained": True,
        "removed_prescriptive_fields": removed,
    }
    if policy["agent_prompt_policy_version"] in FD4_EVIDENCE_VERSIONS:
        evidence = describe_operating_evidence(result, round_id)
        result = {"fd_evidence_contract": result.pop("fd_evidence_contract"),
                  "fd_operating_evidence": evidence, **result}
    return result


def fd4_reference_contract(workspace, round_id, role):
    """Repeat the runtime schema after Skill expansion without supplying actions."""
    policy = _fd4_agent_prompt_policy(workspace, round_id)
    if policy.get("agent_prompt_policy_version") not in FD4_REFERENCE_VERSIONS:
        return ""
    if policy.get("agent_prompt_policy_version") == FD4_AGENT_PROMPT_VERSION:
        return """
Semantic references and decision_check are optional audit information, not execution gates. Repair only actual file, permission, output-structure or execution errors.
"""
    role = str(role).strip().lower()
    if role == "analyst":
        text = """
Output-validation reminder, not a business-quantity answer:
- department_targets.production.decision_check must be an object containing demand, finished_goods, remaining_wip, buffer, net_gap, basis_refs, new_production_required, trigger and stop_condition.
- basis_refs is an array of objects, e.g. {\"input\":\"observation\",\"pointer\":\"/sales/sales_orders/accepted\"} and an inventory reference. Verify actual nonempty paths rather than copying examples.
- Cite only supplied, successfully read observation/charts/history. charts maps to chart_export_path and history to history_projection_path.
- A charts reference uses {\"input\":\"charts\",\"pointer\":\"/charts/actual_key\"}; neither a bare pointer string nor an input containing the pipe-separated list is legal. Pointers start inside the JSON, not at an alias or disk path.
- Align target/evaluation/reason with validation. Stop conditions concern demand coverage/delivery rather than plan or employee existence. Retain four department targets, then Write and Read.
- HR hires; Production describes feasible output after staffing. Infer deadlines from actual arrivals/production times. A two/three-round strategic horizon does not justify postponing a current persistent blockage.
"""
        if policy["agent_prompt_policy_version"] in FD4_COMPLETION_VERSIONS:
            text += (
                """
- Actual empty production_plans is the no-plan exception and permits remaining_wip=0; missing/null/other empty summaries remain invalid. If completion validation blocks exit, correct and Write the full file then Read it; describing intent is not completion.
"""
            )
        return text
    if role in {"production", "hr"}:
        text = """
Output-validation reminder:
- For production/hiring, put decision_check on the action entry beside action and action_reason, not inside action_param.
- basis_refs is an object array; staffing_ref/workload_ref are objects such as {\"input\":\"state\",\"pointer\":\"/self_state/actual_field\"}, never bare strings.
- Production references state/coordination; HR may also reference analysis. Cite only supplied and read inputs and verified paths inside their JSON.
- Reuse evidence to repair format rather than rereading all inputs. Pass needs no decision_check; do not replace the business conclusion merely because its format failed.
"""
        if policy["agent_prompt_policy_version"] in FD4_ROLE_CONTRACT_VERSIONS:
            text += """
- state is the department file; coordination is the supplied blackboard.json. The blackboard alias grants no extra access.
- Every handle_recruitment provides department, required_workers, available_workers, timely_pending_workers, timely_releasing_workers, uncovered_workers, staffing_ref and workload_ref. Prove persistent work rather than hiring for zero counts.
- staffing_ref cites actual staffing; workload_ref separately proves work or a staffing failure. Zero-staff lists, counts and Analyst prose are not workload.
- Positive production gaps establish production work, not procurement/inventory/finance staffing needs. Do not hide the same unsupported conclusion behind different references.
- available>0 with allocated=0 permits task allocation, not nonexistent HR allocation/transfer actions or more hiring. After rejection, reassess the conclusion, then correct full JSON.
"""
        if policy["agent_prompt_policy_version"] in FD4_WORKLOAD_REFERENCE_VERSIONS and role == "hr":
            text += """
- Prefer complete workload objects: analysis /department_targets/production/decision_check with a positive quantified production gap, or coordination /departments/sales with explicit confirmed>stale. Verify paths first.
- Their net_gap/confirmed_order_backlog_quantity leaves are allowed; the controller checks coverage/stale in the same supplied object. Arbitrary positive counts, recovery_needed, recovery_candidate_count, cash, headcount or empty cobweb summaries are not work.
"""
        return text
    return ""


def fd4_decision_guidance(workspace, round_id, role):
    """Versioned evidence checks, shared by all FD4 Agent and ablation arms."""
    policy = _fd4_agent_prompt_policy(workspace, round_id)
    if not policy:
        return ""
    if policy["agent_prompt_policy_version"] == FD4_AGENT_PROMPT_VERSION:
        if str(role).strip().lower() not in {"analyst", "production", "hr", "sales", "procurement"}:
            return ""
        text = """
Operating evidence and autonomy; these are semantic guidance, not execution gates:
- Analyst is advisory; current state and executor constraints prevail. Correct diagnosis, change direction or pass without terminating simulation. Legal actions execute and outcomes are recorded.
- decision_check/references may be retained for audit but are optional. Missing objects, arithmetic or workload concerns yield warnings, not forced rewrites or blocked completion. Actually Write full standard JSON and Read it back.
- count/allocated/available/pending differ. available>0 with allocated=0 permits task allocation. Production assigns its workers; HR has no prerequisite allocation action. Hiring does not require existing HR staff.
- Check serviceable orders, stock, remaining WIP, timely incoming supply, recruitment and deadlines once each; future resources cannot bypass current staff/material/cash checks.
- Historical breached/expired/completed orders require an actual available remedy, not an assumed new action.
- Fixed targets and recovery recommendations are hypotheses; recompute after recovery instead of repeating stale bottlenecks. Necessity warnings are not business failures and do not mechanically require pass.
"""
        if str(role).strip().lower() == "analyst":
            text += """
Analyze the next two or three rounds:
- Reconcile fd_forward_evidence staffing, pending hires, remaining plans, expected completion/release, material arrival and order deadlines with current facts and sealed charts. Do not add historical hires to current staff or count finished plans as WIP/future release.
- Future numbers are conditional on disclosed ongoing actions finishing on time, not future observations. Unknown/null or overdue unconfirmed events are neither completed nor zero. Never read future files for forecasts.
- Distinguish temporary occupancy covered by known actions from persistent gaps. Compare release/arrival+production+delivery with deadlines and give conditional rather than unique staffing/batch targets.
- Hiring needs no existing HR staff; available production workers need no HR allocation. Update summary and targets consistently and do not describe successful recruitment as impossible.
- Explain near-term bottleneck changes, serviceable demand and review timing in reason/evaluation. Judge delivery/cost rather than acceptance or submission; no new mandatory JSON fields are added.
"""
        return text + fd4_ablation_context(workspace, round_id)
    common = """
Operating evidence overrides fixed quantities or default expansion:
- Use authorized role inputs only. Explain missing cross-department evidence; do not seek raw files.
- Distinguish total demand from net gaps, total from available workers, and idle from executable capacity.
- Irremediable breached/expired/cancelled/completed orders are historical/risk evidence unless a legal remedy is explicitly available. Disappearance from state does not prove fulfillment.
- Recovery signals, backlog, recommendations and Analyst targets are hypotheses to check against inventory, incoming supply, WIP, staffing and deadlines.
- Briefly state demand, coverage, remaining gap and hard constraints in reason. When evidence suffices, Write rather than adding reports or repeated reads.
"""
    roles = {
        "analyst": """
Additional analysis order:
- Reconcile real order status/deadlines and serviceable demand, then count finished goods and timely remaining WIP once. Total backlog is not the net gap; original plan quantities are not remaining WIP.
- Derive procurement from net production need through recipes, subtracting available materials and timely purchases. Do not resubtract reservations already reflected in position or double-count summaries/incoming/pending.
- Assess count/allocated/available/pending hires, completion times and execution errors. Zero availability is neither proof of healthy staffing nor a permanent shortage; compare blockage with release/arrival times.
- Idle lines alone do not justify expansion. Assess HR for staffing constraints and net replenishment for material constraints; use existing feasible resources first.
- Update all four goals after arrivals, releases, delivery and coverage. Once gaps close, use grounded rolling needs rather than a repeated batch. Do not invent equally sized future orders.
- Preserve department autonomy through conditions and verification metrics, not unique quantities, suppliers or hiring counts. Do not infer bottlenecks from experiment labels.
""",
        "procurement": """
Procurement checks:
- Subtract available materials and timely purchases from net recipe needs; a positive recovery signal alone does not justify another order.
- Pass when executable production plus a grounded buffer is covered; buying materials cannot fix a staffing blockage without material shortage.
- Pay Fast/air premiums only when earlier arrival changes feasible delivery. Otherwise compare economic logistics rather than assuming fastest is optimal.
""",
        "production": """
Production checks:
- Base new plans on serviceable demand and grounded buffers minus finished goods and timely remaining WIP, without duplicates. Zero gap means idle capacity or stale backlog alone does not justify output.
- Respect current workers, lines, materials, cash and completion times. Do not rely on the executor to turn infeasible actions into pass or reduce quantities.
- If staff are unavailable, explain release/pending hires versus deadlines using authorized facts. An idle line cannot run without workers. Stop repeating batches after coverage.
""",
        "hr": """
Hiring checks override generic zero-availability hiring advice:
- Inspect count/allocated/available, pending hires, visible release times and actual workload for each blocked department. Timely temporary release need not trigger hiring; nonzero total headcount must not hide persistent unavailability.
- Recruit to the real workload gap only when staffing persistently blocks executable demand and releases/pending hires cannot cover it in time. Do not use a fixed default.
- Do not generally staff HR/finance without a verified execution bottleneck. Stop duplicate hiring after recovery and explain pass from actual state.
""",
        "sales": """
Order-acceptance checks:
- For actionable unexpired orders, decrement uncommitted finished goods and reliable timely WIP per deadline. Do not reuse stock or rely on staff-blocked production.
- Separate irremediable old orders from new demand. Do not produce breached quantities again or accept infeasible orders to inflate count-based completion.
""",
    }
    detail = roles.get(str(role).strip().lower())
    if not detail:
        return ""
    if policy["agent_prompt_policy_version"] == FD4_LEGACY_AGENT_PROMPT_VERSION:
        return common + detail
    decision_order = """
Net-demand priority overrides direct action from recovery quantities or idle capacity:
- fd_evidence_contract defines recovery-signal semantics. Generic policy wording cannot restore removed action commands. All actual execution constraints remain.
- recovery_guard/cash_guard/margin_guard assess feasibility, not necessity. Precise recovery commands were removed; do not guess them or reopen raw files. Respect cash, staffing, material and capacity limits.
- Assess real delivery or grounded inventory benefit before resource availability. No WIP, idle staff or idle lines do not prove operational failure.
- Quantify only the net gap supported by authorized facts; identify unknown coverage rather than inventing precision.
- Compare pass with a new commitment. With zero gap and no verifiable benefit, pass; otherwise assess deadlines, contribution and resources. Buffers require authorized safety-stock/recent-demand evidence, not invented future orders.
- A new plan need not finish this round. allocated=0 after settlement may reflect completed work and resource release. Recompute G after hiring or arrivals rather than repeating the previous action.
"""
    role_key = str(role).strip().lower()
    if role_key in {"analyst", "production", "sales"}:
        decision_order += """
- In one reason line, give actionable demand D, available finished goods F, timely remaining WIP W, grounded buffer B and G=max(0,D+B-F-W). Reconcile overlapping backlog/demand; if stale is inside confirmed, subtract once. Check deadlines and exclude expired deliveries from D.
"""
    elif role_key == "procurement":
        decision_order += """
- Quantify net material need and stock/timely incoming coverage in reason. Do not resubtract reservations already included in inventory position. Separate total recipe demand, net purchases and production staffing blockage.
"""
    elif role_key == "hr":
        decision_order += """
- Quantify workload-based staffing gaps and timely releases/arrivals in reason. Nonzero quantities in other departments alone do not establish recruitment work.
"""
    if role_key == "analyst":
        decision_order += """
- Four targets describe current bottlenecks/net gaps and trigger/stop conditions. With zero gap, Production controls new commitments rather than activating capacity. HR has no manual-allocation action or goal that every employee be allocated.
- Hiring addresses persistent work blockage, not mere inventory/order fields or count=0. Distinguish completed production from currently available workers; output or active_plans>=1 alone is not success.
"""
    if policy["agent_prompt_policy_version"] in FD4_EVIDENCE_VERSIONS:
        decision_order += """
Evidence semantics override fixed targets, recovery candidates and generic zero-staff hiring:
- fd_operating_evidence indexes disclosed facts, not action answers. confirmed/demand/stale summaries may overlap; do not add without order evidence.
- Backlog does not prove stock reserved or unavailable. Reduce F from on_hand only with reservation/damage/commitment evidence; do not set F=0 without it or reuse stock for multiple orders.
- Convert net production gaps to materials; finished goods are not new material demand. Preserve unknown quantities.
- No active plan may mean yesterday's plan finished, not failure to start. Evaluate real delivery, uncovered demand and spending rather than plan count/output alone.
- Zero staff is not failure evidence. Identify the legal blocked task and whether release/arrival covers it. Do not claim a task must fail if it already succeeded without department staff.
- With unavailable workers, idle lines are not immediately executable. Compare waiting/recruitment against deadlines; do not assume permanent hiring. Unknown release times remain unknown.
- Analyst gives triggers/stop conditions, not unique/minimum batches or active_plans>=1 targets. Correct stale targets instead of inventing unavailable inventory or demand.
- Once evidence suffices, give a concise reason and invoke Write then Read; printed JSON or long derivations are not completion.
"""
    if policy["agent_prompt_policy_version"] in FD4_DECISION_CHECK_VERSIONS:
        decision_order += """
Decision-consistency contract, leaving action fields/parameters unchanged:
- References use {\"input\":\"state\",\"pointer\":\"/field/subfield\"} with a single authorized input name: state, coordination, analysis or observation. Pointers are real JSON paths, not disk paths.
- Assess work necessity before resource feasibility. Recovery follows real gaps/persistent blockage, hiring, arrival, net planning, delivery and stopping new commitments; never fixed rounds or minimum batches.
- Unknowns are null, not zero. Repair the validation object and related conclusion using existing reads, without restarting reads or adding reports.
"""
        if role_key in {"analyst", "production"}:
            decision_order += """
- coverage fields are demand, finished_goods, remaining_wip, buffer, net_gap and basis_refs. Quantities are finite nonnegative numbers or null. G=max(0,demand+buffer-finished_goods-remaining_wip); unknown coverage forbids a precise G. Positive buffer requires buffer_refs to actual safety-stock policy, not idle capacity or abundant cash.
"""
        if role_key == "analyst":
            basis_rule = (
                'basis_refs must cite observation only'
                if policy["agent_prompt_policy_version"] == FD4_CONSISTENCY_PROMPT_VERSION
                else 'basis_refs must cite supplied observation/charts/history only'
            )
            decision_order += f'\n- Add department_targets.production.decision_check with coverage fields plus new_production_required (boolean), trigger and stop_condition.{basis_rule}; with net_gap=0, new_production_required=false. Targets evaluate gap closure/delivery, not inconsistent minimum output or active_plans>0.\n- HR goals describe blocked work and timely hires/releases, not every zero-headcount department. Assess timely hiring for serviceable demand persistently blocked by zero staff; caution is not grounds to ignore blockage. Do not invent transfers/outsourcing/allocation.\n'
        elif role_key == "production":
            decision_order += """
- Each create_production_plan adds top-level decision_check with coverage fields, citing state/coordination rather than Analyst targets. Total new same-product quantity must not exceed declared net_gap. Zero gap forbids a new plan. Verify immediate workers; do not invent a future start to bypass current zero staffing. Pass requires no extra check.
"""
        elif role_key == "hr":
            decision_order += """
- Every handle_recruitment adds top-level decision_check with department, required_workers, available_workers, timely_pending_workers, timely_releasing_workers, uncovered_workers, staffing_ref and workload_ref.
- uncovered_workers=max(0,required_workers-available_workers-timely_pending_workers-timely_releasing_workers); same-department hires must not exceed the gap. required_workers follows actual work, not a default minimum. staffing_ref cites staffing; workload_ref cites valid orders, confirmed>stale work, a positive production decision_check or a specific staffing failure, not zero counts/cash/inventory totals.
- Positive timely pending/releasing counts require corresponding record references. Without timing evidence, do not claim timely coverage. Explain workload deadlines versus arrival/release. A task that succeeded with zero department staff cannot be claimed necessarily blocked. Pass needs no extra check.
"""
    if policy["agent_prompt_policy_version"] in FD4_COMPLETION_VERSIONS:
        decision_order += """
- Actual production_plans={} or [] can prove no WIP with remaining_wip=0; missing/null/arbitrary empty summaries cannot.
- After rejection, Write the full corrected file and Read it. Explanations, reference snippets or claimed completion are not delivery. Do not reinvoke an already expanded Skill within this session.
"""
    if policy["agent_prompt_policy_version"] in FD4_ROLE_CONTRACT_VERSIONS:
        decision_order += """
- Current state overrides old charts/narrative. available>0 and allocated=0 is not shortage; work actions allocate staff. Update all targets and summary after arrivals rather than claiming zero production staff.
- Do not hire procurement merely for zero headcount if materials cover demand and there is no work. A silent procurement goal must not conflict with HR demanding replenishment hiring. Base production on the net gap, not total orders already covered by goods.
"""
    if policy["agent_prompt_policy_version"] in FD4_WORKLOAD_REFERENCE_VERSIONS:
        decision_order += """
- Compare hiring arrival + production + delivery with each deadline. Late hires cannot rescue already overdue orders; separate later serviceable demand. Evaluate delivery, not retained accepted status or submitted plans.
- Justify staffing from visible worker capacity and deadlines rather than assuming at least one hire. Do not invent delivery_deadline parameters, outsourcing, transfers or manual allocation actions.
"""
    return common + detail + decision_order + fd4_reference_contract(workspace, round_id, role)
