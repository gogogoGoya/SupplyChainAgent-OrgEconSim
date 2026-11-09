"""Isolated MA/SA experiment runner with shared retries, recording and dispatch.

Only dedicated firm_ablation presets enter here. Legacy SkillRunner behavior is unchanged.
No phase budget: each task has bounded feedback-driven attempts and a per-attempt timeout.
"""

import asyncio
from contextlib import aclosing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid

from claude_agent_sdk import query, HookMatcher, PermissionResultAllow, PermissionResultDeny, ResultMessage

from core.firm_tool_evidence import AttemptToolEvidence, EVIDENCE_VERSION
from core.firm_context import pack_context
from core.firm_action_contract import action_execution_contract
from core.firm_runtime_support import (
    AttemptInputStream, EXECUTION_ADAPTER_VERSION, ToolControlChannelError,
    runtime_diagnostics, tool_control_failure,
)
from config.firm_architecture_experiment import FD4_MA_VERSION, FD_ABLATION_VERSION

from core.firm_decision_protocol import (
    MODULES, DecisionRecorder, aggregate_quality, digest, read_json, validate_analysis,
    validate_bundle, validate_workflow, visible_facts, write_json,
)
from agent.fd_experiment_runtime import write_neutral_analysis
from core.model_failure import (
    FatalModelError, attach_model_failure, check_model_message, classify_model_failure,
)
from agent.multi_tenant_utils import MultiTenantUtils
from agent.static_utils import StaticUtils


def completion_feedback(task, output, evidence):
    """Repair missing protocol evidence without replacing an already verified decision.

    A missing Skill/Read receipt does not require regenerating valid business JSON.
    Receipt requirements themselves stay unchanged, including after a failed Write.
    """
    schema_errors = task["validator"](read_json(output))
    errors = list(dict.fromkeys([*schema_errors, *evidence.errors(task["inputs"])]))
    actions, instructions = [], []
    if not errors:
        return errors, actions, ""
    if not evidence.skill_invoked:
        actions.append("invoke_selected_skill")
        instructions.append(f"Invoke Skill(skill={json.dumps(task['skill'])}) successfully.")
    missing = [str(Path(p).resolve()) for p in task["inputs"] if str(Path(p).resolve()) not in evidence.reads]
    if missing:
        actions.append("read_missing_inputs")
        instructions.append("Read only these missing required inputs in full, without offset/limit: " + json.dumps(missing, ensure_ascii=False) + ".")
        instructions.append("Reassess the decision against newly read facts. If a substantive decision correction is needed, first Read the existing output, then Write the updated decision and Read it back.")
    actual_hash = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
    written = bool(actual_hash and actual_hash == evidence.write_hash)
    read_back = written and actual_hash == evidence.readback_hash
    if schema_errors or not written:
        if output.is_file():
            actions.append("read_output_before_write")
            instructions.append(f"First Read the existing output {output} in full before another Write; this also refreshes the CLI file-read state.")
        actions.extend(["write_current_output", "read_current_output"])
        instructions.append(f"Write a valid candidate to this exact output {output}, then Read the same content in full. Schema errors: " + json.dumps(schema_errors, ensure_ascii=False) + ".")
    elif not read_back:
        actions.append("read_current_output")
        instructions.append(f"The current file has a successful Write receipt and valid schema. Read {output} in full; no rewrite is required solely to repair the missing readback.")
    else:
        instructions.append("The current output schema and Write/Read receipts are already valid. Keep this file unchanged unless newly read facts require a substantive decision correction; otherwise complete only the missing Skill/input evidence above, then finish.")
    return errors, actions, " ".join(instructions)


class FirmDecisionRunner:
    def __init__(self, runtime, policy):
        self.runtime = runtime
        self.policy = policy
        self.workspace = runtime._workspace_root()
        self.enterprise = runtime.enterprise_spec.enterprise_name
        self.enterprise_id = runtime.enterprise_spec.enterprise_id
        self.root = self.workspace / "enterprises" / self.enterprise
        self.skills = runtime.client_dir / ".claude" / "skills"
        self.pending = {}

    @property
    def central(self):
        return self.policy["architecture"] == "centralized"

    @property
    def matched_fd(self):
        return self.policy.get("version") == FD_ABLATION_VERSION

    def _role_actions(self, payload):
        return payload.get("actions") if self.matched_fd and isinstance(payload, dict) else payload

    def _validate_role(self, payload, dept, functions):
        errors = validate_workflow(self._role_actions(payload), dept, self.enterprise_id,
                                   MultiTenantUtils._is_valid_action_payload)
        if self.matched_fd and isinstance(payload, dict):
            messages = payload.get("communications", [])
            if not isinstance(messages, list):
                return errors + ["communications must be an array"]
            if messages and not self.policy.get("shared_coordination_enabled"):
                errors.append("NC must not emit department communications")
            for message in messages:
                if (not isinstance(message, dict) or message.get("recipient") not in functions
                        or not isinstance(message.get("message"), str) or not message["message"].strip()):
                    errors.append("Each communication needs an authorized recipient and nonempty message")
        return errors

    def _sanitize(self, value):
        return visible_facts(value, peer_visible=self.policy["family"] != "herding_hidden")

    def _inputs(self, phase_root, name, payload):
        target = phase_root / "inputs" / f"{name}.json"
        write_json(target, self._sanitize(payload))
        return target

    def _pack_context(self, path):
        try:
            return pack_context(path)
        except (OSError, ValueError, TypeError) as cause:
            error = FatalModelError("agent_input_packaging_failed",
                "Authorized context could not be packaged and verified losslessly; simulation stopped before model decisions.")
            error.context.update(source_path=str(path), error_type=type(cause).__name__, detail=str(cause))
            raise error from cause

    def _department_specs(self, round_id, phase):
        return [self.runtime.skill_runner._build_skill_run_spec(dept, round_id)
                for dept in self.runtime.enterprise_spec.departments
                if dept.enabled and dept.type == "Agent" and (phase == "decision" or dept.need_trade)]

    async def prepare(self, round_id, phase):
        phase_root = self.root / "firm_agent" / f"day{round_id}" / phase
        phase_root.mkdir(parents=True, exist_ok=True)
        identity = {"run_id": read_json(self.workspace / "run_meta.json", {}).get("run_id"),
                    "enterprise_id": self.enterprise_id, "round_id": round_id, "phase": phase,
                    "architecture": self.policy["architecture"], "policy_version": self.policy["version"],
                    "baseline_group": self.policy.get("baseline_group"),
                    "baseline_run_id": (self.policy.get("baseline") or {}).get("source_run_id"),
                    "dispatch_protocol": self.policy["dispatch_protocol"]}
        identity["execution_evidence_version"] = EVIDENCE_VERSION
        identity["execution_adapter_version"] = EXECUTION_ADAPTER_VERSION
        recorder = DecisionRecorder(phase_root, identity)
        recorder.event("phase_started", policy_version=self.policy["version"])
        phase_state = {"schema_version": "firm_phase.v1", **identity,
                       "tasks": [], "business_rejections": 0, "unknown_execution_results": 0,
                       "normalization_changes": 0, "pre_execution_rejections": 0,
                       "business_repairs": 0, "unresolved_rejections": 0,
                       "decision_available": False, "technical_no_submission": False,
                       "started_at_monotonic": time.monotonic(), "phase_root": str(phase_root)}
        self.pending[phase] = {"state": phase_state, "recorder": recorder, "groups": [],
                               "auto_departments": [], "round_id": round_id}
        write_json(phase_root / "phase_started.json", {k: v for k, v in phase_state.items() if k != "started_at_monotonic"})
        try:
            await self._prepare(round_id, phase, phase_root, phase_state)
        except asyncio.CancelledError:
            recorder.event("phase_cancelled")
            self._finish(phase, unavailable_reason="cancelled")
            raise
        except FatalModelError as error:
            phase_state["fatal_model_error"] = error.to_dict()
            recorder.event("fatal_model_error", **error.to_dict())
            self._finish(phase, unavailable_reason=str(error))
            raise
        except Exception as error:
            # Decision/input errors are observable technical gaps; they do not invent business choices.
            recorder.event("phase_preparation_failed", error_type=type(error).__name__, error=str(error))
            phase_state["failure_reason"] = str(error)
            phase_state["technical_no_submission"] = True
        return []

    async def _prepare(self, round_id, phase, phase_root, state):
        departments = [d for d in self.runtime.enterprise_spec.departments if d.enabled]
        if phase == "decision":
            self.pending[phase]["auto_departments"] = [d for d in departments if d.type == "Auto"]
        if phase == "decision":
            write_neutral_analysis(self.root, self.enterprise, round_id, [d.dept_id for d in departments])
        MultiTenantUtils.handle_enterprises_observations(round_id, self.enterprise)
        if phase == "trade":
            MultiTenantUtils.archive_enterprise_trade_files(self.enterprise, round_id)
        specs = self._department_specs(round_id, phase)
        action_specs = [spec for spec in specs if spec.role.lower() != "finance"]
        state["expected_functions"] = [spec.role.lower() for spec in action_specs]
        if self.matched_fd:
            state["arm"] = self.policy["arm"]
            state["communications"] = []
        if not specs:
            state.update(decision_available=True, not_applicable=True)
            return
        sr = self.runtime.skill_runner
        analyst_spec = sr._build_analyst_run_spec(round_id)
        observation = read_json(analyst_spec.observation_path)
        if not isinstance(observation, dict):
            raise ValueError("Current enterprise observation is missing or invalid")
        observation_path = self._inputs(phase_root, "observation", observation)
        history_path = Path(analyst_spec.history_projection_path)
        history = read_json(history_path, {}) if history_path.exists() else {}
        history_path = self._inputs(phase_root, "history", history)
        chart = read_json(analyst_spec.chart_export_path, {}) if analyst_spec.chart_export_enabled else {}
        chart_path = self._inputs(phase_root, "charts", chart)
        # Declare the SAME functional knowledge sources for both architectures.
        skill_names = [self.runtime.enterprise_spec.analyst_skill_name] + [s.skill_name for s in specs]
        knowledge = {}
        for skill in dict.fromkeys(skill_names):
            source = self.skills / skill / "SKILL.md"
            knowledge[skill] = {"reference_only": True, "text": source.read_text(encoding="utf-8"),
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        knowledge_path = self._inputs(phase_root, "functional_knowledge", knowledge)
        facts = {}
        for spec in specs:
            dept = spec.role.lower()
            local = read_json(spec.state_path)
            shared = read_json(spec.blackboard_path)
            if not isinstance(local, dict) or not isinstance(shared, dict):
                raise ValueError(f"Missing current authorized state for {dept}")
            facts[dept] = {"state": local, "coordination": shared,
                           "trade_card": read_json(spec.trade_decision_card_path, {}) if spec.trade_decision_card_path else {}}
        snapshot_id = digest(self._sanitize({"observation": observation, "departments": facts}))
        state["snapshot_id"] = snapshot_id
        context = {"enterprise_id": self.enterprise_id, "round_id": round_id, "phase": phase,
                   "snapshot_id": snapshot_id, "observation": observation, "departments": facts,
                   "history": history, "charts": chart, "expected_functions": state["expected_functions"]}
        context_path = self._inputs(phase_root, "enterprise_context", context)
        context_package = self._pack_context(context_path) if self.central else None
        write_json(phase_root / "input_manifest.json", {
            "snapshot_id": snapshot_id, "source_scenario": self.policy["source"],
            "architecture": self.policy["architecture"], "knowledge_hashes": {k: v["sha256"] for k, v in knowledge.items()},
            "context_sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
            "policy_version": self.policy["version"], "dispatch_protocol": self.policy["dispatch_protocol"],
            "baseline_group": self.policy.get("baseline_group"), "baseline": self.policy.get("baseline"),
            "execution_evidence_version": EVIDENCE_VERSION,
            "execution_adapter_version": EXECUTION_ADAPTER_VERSION,
            "context_package": context_package["manifest"] if context_package else None,
        })
        if self.central:
            config = read_json(self.workspace / "run_meta.json", {}).get("scenario_config") or {}
            runtime_policy = config.get("runtime_injection") or {}
            operating_contract = {
                "schema_version": "firm_operating_contract.v2",
                "decision_regime": (config.get("experiment_design") or {}).get("decision_regime"),
                "baseline_group": self.policy.get("baseline_group"),
                "profit_objective_policy": runtime_policy.get("profit_objective_policy", {}),
                "analysis_profile": ((config.get("integration_profiles") or {}).get("capabilities") or {}).get("analysis", {}),
                "controller_settings": {k: v for k, v in runtime_policy.items() if k.startswith("bullwhip_")},
            }
            operating_path = self._inputs(phase_root, "operating_contract", operating_contract)
            if phase == "decision" and self.policy.get("baseline_group") == "C3":
                # C3 layouts start with Auto HR/inventory. In the original
                # two-slot department scheduler they execute before model actions.
                # Keep the same pre-action projections, apply those controllers
                # once before the unified model task, and do not replay at dispatch.
                auto = self.pending[phase]["auto_departments"]
                self.pending[phase]["auto_departments"] = []
                for dept in auto:
                    MultiTenantUtils.generate_dept_auto_action(dept.dept_id, self.enterprise, round_id)
                state["automatic_departments_timing"] = "before_model_decision"
                self.pending[phase]["recorder"].event("automatic_departments_executed", departments=[d.dept_id for d in auto])
            validator = lambda value: validate_bundle(
                value, enterprise_id=self.enterprise_id, round_id=round_id, phase=phase,
                snapshot_id=snapshot_id, departments=state["expected_functions"],
                valid_action=MultiTenantUtils._is_valid_action_payload, enterprise_name=self.enterprise)
            task = self._make_task(phase_root, state, "Enterprise", "enterprise_operator",
                                   [*context_package["required"], knowledge_path, operating_path], validator, "enterprise_decision.json")
            task["complete_read_inputs"] = list(context_package["required"])
            for skill in dict.fromkeys(skill_names):
                templates = self.skills / skill / "actions"
                if templates.exists():
                    shutil.copytree(templates, task["skill_root"] / "references" / skill / "actions")
            # Keep all original knowledge accessible, but do not force a full
            # reread of every independent role's obsolete execution protocol.
            index = {}
            for skill, reference in knowledge.items():
                reference_path = task["skill_root"] / "references" / skill / "BUSINESS_REFERENCE.md"
                reference_path.parent.mkdir(parents=True, exist_ok=True)
                reference_path.write_text(reference["text"], encoding="utf-8")
                index[skill] = {"business_reference": str(reference_path), "sha256": reference["sha256"],
                               "action_templates": [str(p) for p in sorted(reference_path.parent.glob("actions/*.json"))]}
            index_path = self._inputs(phase_root, "knowledge_index", {
                "references": index, "usage": "Read relevant business references and exact action templates. Role identities, old paths and output protocols are superseded by the unified contract."})
            task["inputs"].remove(knowledge_path.resolve())
            task["inputs"].append(index_path.resolve())
            task["optional_inputs"] = [knowledge_path.resolve(), *context_package["optional"]]
            contract_path = self._inputs(phase_root, "output_contract", self._output_contract(state))
            task["inputs"].append(contract_path.resolve())
            task["prompt"] = self._central_prompt(context_package["index_path"], knowledge_path, state)
            task["prompt"] += ("""
Enterprise facts are losslessly partitioned. Read the index, then every required shard in full without offset/limit. Each fits tool byte/line/long-line limits; history/charts are optional. Aliases refer to the same fact, not additional quantities. Department state, coordination and trade cards map to /departments/<function>/state, /coordination and /trade_card; enterprise observation maps to /observation. These are JSON pointers, not disk paths. Do not read the original large file in full; it remains a read-only source for verification.""")
            task["prompt"] += (f'\nRequired knowledge/action index: {index_path}. The original full knowledge bundle is optional. Read relevant rules and actual action templates through the index without repeated full reads.\nRequired output contract: {contract_path}. Its skeleton describes types, not business recommendations; do not copy placeholder contents.')
            task["prompt"] += (f'\nRequired operating-regime contract: {operating_path}. If profit_objective_policy.enabled=true, use its weights, planning horizon and candidate ranking. Weights guide tradeoffs, not guaranteed profit/optimality. Do not reproduce, strengthen or weaken a preset economic effect. The regime overrides generic mechanism advice; legal actions and hard constraints remain.')
            task["prompt"] += f'\nAction template directory: {task['skill_root'] / 'references'}, read by function Skill/actions.'
            await self._run_task(task)
            state["tasks"].append(task["summary"])
            if task["payload"] is not None:
                if phase == "decision":
                    write_json(self.root / "analysis.json", {**task["payload"]["analysis"],
                               "analysis_provenance": {"source": "single_firm_agent", "model_generated": True}})
                for spec in action_specs:
                    self.pending[phase]["groups"].append((spec, task["payload"]["decisions"][spec.role.lower()], task))
        else:
            # Analyst is a separate model task only in MA; all role candidates are deferred.
            analyst_ok = True
            if phase == "decision" and (not self.matched_fd or self.policy["analyst_enabled"]):
                validator = lambda value: validate_analysis(value, self.enterprise, round_id, state["expected_functions"])
                task = self._make_role_task(phase_root, state, "Analyst", analyst_spec.skill_name, {
                    'Current observation': observation_path, 'Historical projection': history_path,
                    'Chart evidence': chart_path}, validator, "analysis.json")
                await self._run_task(task)
                state["tasks"].append(task["summary"])
                analyst_ok = task["payload"] is not None
                if analyst_ok:
                    write_json(self.root / "analysis.json", {**task["payload"],
                               "analysis_provenance": {"source": "organizational_analyst", "model_generated": True}})
                MultiTenantUtils.handle_enterprises_observations(round_id, self.enterprise)
                specs = self._department_specs(round_id, phase)
            analysis_path = self._inputs(phase_root, "analysis", read_json(self.root / "analysis.json", {}))
            for spec in specs:
                dept = spec.role.lower()
                shared = read_json(spec.blackboard_path, {})
                if self.matched_fd and self.policy["shared_coordination_enabled"]:
                    previous = read_json(self.root / "firm_agent" / f"day{round_id-1}" / "decision" / "communications.json", [])
                    shared["department_messages"] = [m for m in [*previous, *state["communications"]]
                        if m.get("recipient") == dept]
                paths = {'Department state': self._inputs(phase_root, dept, read_json(spec.state_path, {})),
                         "blackboard": self._inputs(phase_root, dept + "_blackboard", shared),
                         "analysis": analysis_path}
                if spec.trade_decision_card_path:
                    paths["trade_decision_card"] = self._inputs(phase_root, dept + "_trade_card", read_json(spec.trade_decision_card_path, {}))
                validator = (lambda value: [] if isinstance(value, dict) else ["Finance advice must be an object"]) if dept == "finance" else (
                    lambda value, department=dept: self._validate_role(value, department, state["expected_functions"]))
                task = self._make_role_task(phase_root, state, spec.role, spec.skill_name,
                                            paths, validator, spec.output_file_name)
                await self._run_task(task)
                state["tasks"].append(task["summary"])
                if task["payload"] is not None:
                    if dept == "finance":
                        write_json(spec.output_file_path, task["payload"])
                    else:
                        if self.matched_fd and isinstance(task["payload"], dict):
                            state["communications"].extend({**m, "sender": dept, "round_id": round_id,
                                                            "kind": "model_message_not_execution_receipt"}
                                for m in task["payload"].get("communications", []))
                        self.pending[phase]["groups"].append((spec, self._role_actions(task["payload"]), task))
            if not analyst_ok:
                state["failure_reason"] = "Analyst unavailable after retries; neutral schema used only to continue observation generation"
        expected = set(state["expected_functions"])
        obtained = {spec.role.lower() for spec, _, _ in self.pending[phase]["groups"]}
        state["decision_available"] = obtained == expected and all(t["success"] for t in state["tasks"])
        state["technical_no_submission"] = not state["decision_available"]

    def _make_task(self, phase_root, state, role, skill, inputs, validator, output_name):
        task_id = uuid.uuid4().hex
        root = phase_root / (role.lower() + "_" + task_id)
        root.mkdir(parents=True)
        project = root / "agent_project"
        source = self.skills / skill
        destination = project / ".claude" / "skills" / skill
        shutil.copytree(source, destination)
        contract_path = root / "action_execution_contract.json"
        contract = action_execution_contract(state["expected_functions"] if role in {"Enterprise", "Analyst"} else [role.lower()])
        write_json(contract_path, contract)
        recorder = DecisionRecorder(root, {**{k: state.get(k) for k in ("run_id", "enterprise_id", "round_id", "phase", "architecture",
                                                                    "policy_version", "baseline_group", "baseline_run_id", "dispatch_protocol")},
                                           "task_id": task_id})
        summary = {"task_id": task_id, "role": role, "attempts": 0, "success": False,
                   "execution_evidence_version": EVIDENCE_VERSION,
                   "execution_adapter_version": EXECUTION_ADAPTER_VERSION, "control_channel_error_attempts": 0,
                   "action_execution_contract_version": contract["schema_version"],
                   "action_execution_contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                   "status": "planned",
                   "first_attempt_valid": False, "timeout_attempts": 0, "recovered": False,
                   "elapsed_seconds": 0, "events_path": str(root / "events.jsonl")}
        recorder.event("task_planned", task_id=task_id, skill=skill)
        write_json(root / "task_summary.json", summary)
        return {"root": root, "project": project, "skill_root": destination, "skill": skill,
                "inputs": [*(Path(p).resolve() for p in inputs), contract_path.resolve()], "validator": validator,
                "action_execution_contract_path": contract_path.resolve(),
                "output_name": output_name, "summary": summary, "recorder": recorder,
                "payload": None, "session_id": None, "prompt": "", "last_feedback": []}

    def _central_prompt(self, context, knowledge, state):
        return (f'Invoke Skill(skill="enterprise_operator", args="--round_id {state['round_id']} --enterprise_name {self.enterprise}").\nEnterprise facts: {context}\nBusiness knowledge reference: {knowledge}\nBundle identity: enterprise_id={self.enterprise_id}, round_id={state['round_id']}, phase={state['phase']}, snapshot_id={state['snapshot_id']}.\ndecisions must cover exactly {state['expected_functions']}. analysis uses enterprise_name={self.enterprise}.\nEach decision is a nonempty standard action array. Retain existing actions and constraints only. Diagnose internally; do not invoke Analyst.')

    def _make_role_task(self, phase_root, state, role, skill, paths, validator, output_name):
        """Give matched MA the same bounded-read and explicit-output safeguards as SA.

        Each role retains only its own authorized information. Legacy economic MA
        development presets keep their previous input/prompt protocol.
        """
        if self.policy.get("version") not in {FD4_MA_VERSION, FD_ABLATION_VERSION}:
            task = self._make_task(phase_root, state, role, skill, list(paths.values()), validator, output_name)
            task["prompt"] = self._role_prompt(task, paths, state["round_id"], state["phase"])
            return task
        dept = role.lower()
        facts = {"enterprise_id": self.enterprise_id, "round_id": state["round_id"],
                 "phase": state["phase"], "snapshot_id": state["snapshot_id"], "role": role,
                 "expected_functions": state["expected_functions"]}
        if role == "Analyst":
            facts.update(observation=read_json(paths['Current observation']),
                         history=read_json(paths['Historical projection']), charts=read_json(paths['Chart evidence']))
        else:
            facts.update(departments={dept: {"state": read_json(paths['Department state']),
                                             "coordination": read_json(paths["blackboard"]),
                                             "trade_card": read_json(paths["trade_decision_card"]) if paths.get("trade_decision_card") else {}}},
                         analysis=read_json(paths["analysis"]))
        context = self._inputs(phase_root, dept + "_context", facts)
        package = self._pack_context(context)
        common = self._output_contract(state)
        contract = {"schema_version": "firm_role_output_contract.v1", "role": role,
                    "enterprise_name": self.enterprise, "executor_id": self.enterprise_id,
                    "round_id": state["round_id"], "phase": state["phase"],
                    "rules": ["Write only the current attempt's exact output path, then Read the same content.",
                              "Use the exact output_shape type; do not emit an enterprise bundle or another role's output."],
                    "output_kind": "analysis_object" if role == "Analyst" else "action_array"}
        if role == "Analyst":
            contract["output_shape"] = common["analysis_shape"]
        elif dept == "finance":
            contract.update(output_kind="finance_advice_object", output_shape={"advice": "<current financial advice>"})
        else:
            contract["output_shape"] = [{**deepcopy(common["action_item_shape"]), "module_type": MODULES[dept]}]
            contract["rules"].extend(["Keep action_name and action_param under action and use exact legal templates.",
                                      "action_pass is an explicit model choice with a reason, never an automatic fallback."])
            if self.matched_fd:
                contract["optional_role_envelope"] = {"actions": contract["output_shape"], "communications": []}
                contract["rules"].append("A standard action array is valid. Alternatively return {actions: [...], communications: [{recipient: <authorized function>, message: <fact-grounded message>}]}. NC communications must be empty. Never write sidecar files.")
        contract_path = self._inputs(phase_root, dept + "_output_contract", contract)
        task = self._make_task(phase_root, state, role, skill, [*package["required"], contract_path], validator, output_name)
        task["complete_read_inputs"] = list(package["required"])
        task["optional_inputs"] = [*package["optional"], *(Path(p).resolve() for p in paths.values())]
        task["prompt"] = self._role_prompt(task, {'Role-fact index': package["index_path"], 'Output contract': contract_path},
                                             state["round_id"], state["phase"])
        task["prompt"] += (f"\nRole facts are losslessly partitioned. Read the index and every required shard in full without offset/limit. History/charts are optional; aliases repeat facts and must not be counted twice. State, blackboard and trade cards map to /departments/{dept}/state、/departments/{dept}/coordination、/departments/{dept}/trade_card; analysis to /analysis; Analyst observation/history/charts to /observation, /history and /charts. These are JSON pointers, not disk paths. Follow output_kind/output_shape, overriding old Skill paths/formats. Read required facts and output contract, then decide autonomously; do not read the raw large file in full or invoke other roles' Skills.")
        if self.matched_fd:
            task["prompt"] += """
Actual action-template index: """ + json.dumps(
                [str(p) for p in sorted((task["skill_root"] / "actions").glob("*.json"))], ensure_ascii=False)
        write_json(task["root"] / "input_manifest.json", {"context_package": package["manifest"],
                   "role": role, "output_contract": str(contract_path),
                   "execution_adapter_version": EXECUTION_ADAPTER_VERSION})
        return task

    def _output_contract(self, state):
        departments = state["expected_functions"]
        contract = {"schema_version": "firm_output_contract.v1", "identity": {
            "schema_version": "firm_decision.v1", "enterprise_id": self.enterprise_id,
            "round_id": state["round_id"], "phase": state["phase"], "snapshot_id": state["snapshot_id"]},
            "decision_functions": departments, "module_types": {d: MODULES[d] for d in departments},
            "action_item_shape": {"action": {"action_name": "<exact legal template action_name>",
                                                "action_param": "<parameters with the types/fields required by that template>"},
                                  "action_reason": "<reason grounded in current facts>", "module_type": "<module for this function>",
                                  "executor_id": self.enterprise_id},
            "analysis_required": state["phase"] == "decision",
            "analysis_shape": {"enterprise_name": self.enterprise, "round_id": state["round_id"],
                               "enterprise_summarys": "<one nonempty string, never an object or array>",
                               "department_targets": {d: {"target": "<goal>", "evaluation": "<observable criterion>",
                                                           "reason": "<evidence>"} for d in departments}},
            "rules": ["decisions is an object mapping each required function to a nonempty action array.",
                      "Do not flatten action_name/action_param; keep them under action. Do not invent action names.",
                      "action_pass is an explicit model choice with a reason, never an automatic fallback.",
                      "For business repair, the explicitly named repair scope overrides decision_functions and department_targets.",
                      "Write only the current attempt's exact output path; never create another attempt directory."]}
        contract["bundle_shape"] = {**contract["identity"], "decisions": {
            dept: [{**deepcopy(contract["action_item_shape"]), "module_type": MODULES[dept]}] for dept in departments}}
        if state["phase"] == "decision":
            contract["bundle_shape"]["analysis"] = deepcopy(contract["analysis_shape"])
        return contract

    def _role_prompt(self, task, paths, round_id, phase):
        role = task["summary"]["role"]
        output_rule = ('Analyst outputs enterprise_name, round_id, a nonempty enterprise_summarys string and a department_targets object, never action arrays.'
                       if role == "Analyst" else 'Finance outputs financial-advice JSON only.' if role.lower() == "finance" else
                       "Output only this department's standard action array. Use explicit pass when justified; do not output an Analyst object or enterprise decisions bundle.")
        if self.matched_fd and role not in {"Analyst", "Finance"}:
            output_rule = ('Output the department action array, or {actions: action array, communications: message array}. Messages contain recipient and message only; recipient must be an authorized function. NC communications must be empty. The runner routes messages; authorized prior/current messages appear in blackboard.department_messages. Messages state facts or requests, not invented successful execution. Use explicit action_pass when no action is necessary.')
        if self.matched_fd and self.policy.get("arm") == "na":
            output_rule += 'analysis is an intentionally neutral interface this round. Do not seek another diagnosis file.'
        if self.matched_fd and self.policy.get("arm") == "nc":
            output_rule += 'Empty coordination is intentional. Retain authorized department facts and Analyst reference; do not seek raw blackboards or message files.'
        return (f'Invoke Skill(skill="{task['skill']}", args="--round_id {round_id} --enterprise_name {self.enterprise}").\nCurrent enterprise={self.enterprise}, executor_id={self.enterprise_id}, round_id={round_id}, phase={phase}.\nAction template directory: {task['skill_root'] / 'actions'}\n'
                + "\n".join(f"{name}: {path}" for name, path in paths.items())
                + """
These authorized paths override Skill examples. Read no other files and write no communication sidecar. Decide from current visible facts; Analyst targets are advisory. Invent no actions. Reconcile net demand, stock, incoming supply and WIP; zero staffing or idle capacity alone is not an action command. In trade, prioritize actionable proposals.""" + output_rule)

    async def _run_task(self, task):
        summary, recorder = task["summary"], task["recorder"]
        started = time.monotonic()
        channel_failure = None
        while summary["attempts"] < self.policy["max_attempts"]:
            summary["attempts"] += 1
            number = summary["attempts"]
            summary["status"] = "running"
            write_json(task["root"] / "task_summary.json", summary)
            attempt_started = time.monotonic()
            directory = task["root"] / f"attempt_{number}"
            directory.mkdir()
            output = (directory / task["output_name"]).resolve()
            prompt = (task["prompt"] + f'\nOnly output path: {output}. Write it and Read it back.'
                      + (f'\nRequired action-execution semantics: {task['action_execution_contract_path']}. Account for request replacement invalidating proposals, per-order staffing occupancy, and replenishment success without a created request. Trust actual receipts.'
                         if task.get("action_execution_contract_path") else "")
                      + 'If the output exists, Read it fully before Write, then Read it back. On File has not been read, Read the current file before retrying Write. If feedback only requests a Skill invocation/input Read, supply that evidence without rewriting an already valid verified decision. Newly read substantive facts may justify revision; Read the existing candidate, Write the revision and Read it back.'
                      + ("""
Previous failure and repair requirements: """ + json.dumps(task["last_feedback"], ensure_ascii=False) if task["last_feedback"] else ""))
            optional_inputs = list(task.get("optional_inputs", []))
            previous = task.get("previous_candidate")
            if previous and Path(previous).is_file():
                optional_inputs.append(Path(previous).resolve())
                prompt += f'\nPrevious candidate, read-only and never executed: {previous}. Correct it using the current errors and output path.'
            write_json(directory / "request.json", {"prompt": prompt, "inputs": [str(p) for p in task["inputs"]],
                       "optional_inputs": [str(p) for p in optional_inputs], "execution_evidence_version": EVIDENCE_VERSION,
                       "execution_adapter_version": EXECUTION_ADAPTER_VERSION,
                       "session_policy": "fresh_per_attempt", "input_stream_policy": "keep_open_until_result_or_cleanup"})
            recorder.event("attempt_started", attempt=number, output=str(output))
            options = self.runtime.build_options(summary["role"])
            options.cwd = str(task["project"])
            options.tools = ["Read", "Write", "Skill"]
            options.allowed_tools = []
            options.permission_mode = "default"
            options.resume = None
            options.continue_conversation = False

            def record_evidence(event, **details):
                recorder.event(event, attempt=number, evidence_source="sdk_tool_receipt", **details)
                if event == "tool_requested" and details.get("tool") == "Write" and details.get("allowed"):
                    request = evidence.pending.get(details["tool_use_id"])
                    content = request[1].get("content") if request else None
                    if isinstance(content, str):
                        (directory / f"write_intent_{evidence.write_intents:03d}.raw").write_text(content, encoding="utf-8")
                if event == "candidate_written":
                    (directory / f"candidate_{evidence.writes:03d}.raw").write_text(evidence.write_content, encoding="utf-8")
            evidence = AttemptToolEvidence(cwd=options.cwd, output=output,
                inputs=[*task["inputs"], *optional_inputs], skill_root=task["skill_root"],
                skill=task["skill"], on_event=record_evidence, complete_reads=task.get("complete_read_inputs", []))

            async def permit(tool, args, context):
                allowed = evidence.allowed(tool, args)
                recorder.event("permission_checked", attempt=number, tool=tool,
                               path=str(evidence.path(args)) if evidence.path(args) else None, allowed=allowed)
                return PermissionResultAllow() if allowed else PermissionResultDeny(
                    message="Use only this attempt's exact output path, authorized read paths and selected Skill.")
            options.can_use_tool = permit

            async def before(data, tool_use_id, context):
                tool, args = data.get("tool_name"), data.get("tool_input") or {}
                path = evidence.path(args)
                allowed = evidence.allowed(tool, args)
                recorder.event("tool_access", attempt=number, tool=tool, path=str(path) if path else None, allowed=allowed)
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" if allowed else "deny",
                        "permissionDecisionReason": "Use the exact current authorized input/output paths and selected Skill only."}}

            schema_feedback_count = 0

            async def after(data, tool_use_id, context):
                nonlocal schema_feedback_count
                args = data.get("tool_input") or {}
                raw = args.get("file_path")
                path = Path(raw) if isinstance(raw, str) else None
                if path is not None and not path.is_absolute():
                    path = Path(options.cwd) / path
                if path is not None and path.resolve() == output and output.is_file():
                    errors = task["validator"](read_json(output))
                    schema_feedback_count += bool(errors)
                    recorder.event("candidate_schema_feedback", attempt=number, errors=errors)
                    feedback = ("Candidate schema errors: " + json.dumps(errors, ensure_ascii=False)
                        + "; Read this existing file before rewriting it, then Write the correction and Read it again.") if errors else (
                        "Candidate schema valid. Read this same file in full; do not rewrite valid JSON just to repair missing Skill/input receipts."
                        if data.get("tool_name") == "Write" else
                        "Candidate schema valid. No rewrite is needed solely for missing protocol evidence; complete selected Skill/required reads. If newly read facts require a substantive decision change, Read before rewriting and Read the updated output. The runner verifies the actual receipts.")
                    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": feedback}}
                return {}

            stop_repairs = 0

            async def stop(data, tool_use_id, context):
                nonlocal stop_repairs
                errors, actions, feedback = completion_feedback(task, output, evidence)
                if errors and stop_repairs < 2:
                    stop_repairs += 1
                    recorder.event("stop_protocol_feedback", attempt=number, correction=stop_repairs, errors=errors,
                                   correction_actions=actions)
                    return {"decision": "block", "reason": feedback}
                return {}

            options.hooks = {"PreToolUse": [HookMatcher(matcher=".*", hooks=[before])],
                             "PostToolUse": [HookMatcher(matcher="Read|Write", hooks=[after])],
                             "Stop": [HookMatcher(hooks=[stop])]}
            diagnostics = runtime_diagnostics(options)
            write_json(directory / "runtime_diagnostics.json", diagnostics)
            transport = "finished"
            failure = []
            fatal = None
            channel_failure = None
            error_context = dict(workspace=self.workspace, options=options,
                                 enterprise_id=self.enterprise_id, role=summary["role"], round_id=task["recorder"].identity["round_id"])

            channel = AttemptInputStream(prompt, on_event=lambda event, **details: recorder.event(event, attempt=number, **details))

            async def consume():
                messages = channel.messages()
                try:
                    async with aclosing(query(prompt=messages, options=options)) as stream:
                        try:
                            async for message in stream:
                                record = {"type": type(message).__name__, "content": str(message)}
                                with (directory / "messages.jsonl").open("a", encoding="utf-8") as log:
                                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                                check_model_message(message, **error_context)
                                evidence.consume(message)
                                data = getattr(message, "data", None)
                                if isinstance(data, dict) and data.get("session_id"):
                                    task["session_id"] = data["session_id"]
                                    diagnostics.update(cli_version=data.get("claude_code_version"), session_id=data["session_id"])
                                    write_json(directory / "runtime_diagnostics.json", diagnostics)
                                error = tool_control_failure(message)
                                if error:
                                    raise error
                                usage = getattr(message, "usage", None)
                                if isinstance(usage, dict) and any(usage.values()):
                                    write_json(directory / "usage.json", {"source": "sdk_result", "usage": usage})
                                if isinstance(message, ResultMessage):
                                    channel.release("sdk_result")
                        finally:
                            # Release before query.aclose(): teardown must never
                            # wait for an input generator whose lifetime we own.
                            channel.release("attempt_cleanup")
                finally:
                    await channel.finish(messages)

            try:
                await asyncio.wait_for(consume(), timeout=self.policy["attempt_timeout_seconds"])
            except asyncio.TimeoutError:
                transport = "timeout"
                summary["timeout_attempts"] += 1
                task["session_id"] = None
                recorder.event("attempt_deadline", attempt=number)
            except asyncio.CancelledError:
                recorder.event("attempt_cancelled", attempt=number)
                write_json(directory / "tool_evidence.json", evidence.summary())
                diagnostics.update(transport="cancelled", input_stream_release_reason=channel.reason)
                write_json(directory / "runtime_diagnostics.json", diagnostics)
                summary.update(status="cancelled", elapsed_seconds=summary["elapsed_seconds"] + time.monotonic() - started)
                write_json(task["root"] / "task_summary.json", summary)
                write_json(directory / "outcome.json", {"transport": "cancelled", "protocol_valid": False,
                           "failure_codes": ["cancelled"], "elapsed_seconds": time.monotonic() - attempt_started})
                raise
            except ToolControlChannelError as error:
                channel_failure = error
                transport = "tool_control_error"
                summary["control_channel_error_attempts"] = summary.get("control_channel_error_attempts", 0) + 1
                task["session_id"] = None
                recorder.event("tool_control_channel_failed", attempt=number, **error.to_dict())
            except FatalModelError as error:
                fatal = error
                transport = "fatal_model_error"
                task["session_id"] = None
            except Exception as error:
                fatal = classify_model_failure(str(error))
                if fatal:
                    fatal = attach_model_failure(fatal, **error_context)
                    transport = "fatal_model_error"
                else:
                    transport = "query_error"
                    failure.append(f"{type(error).__name__}: {error}")
                task["session_id"] = None
                recorder.event("query_error", attempt=number, error_type=type(error).__name__, error=str(fatal or error))
            payload = read_json(output)
            diagnostics.update(transport=transport, input_stream_release_reason=channel.reason,
                               elapsed_seconds=time.monotonic() - attempt_started)
            write_json(directory / "runtime_diagnostics.json", diagnostics)
            write_json(directory / "tool_evidence.json", evidence.summary())
            if output.exists():
                # Keep partial/invalid output even when the model did not complete Write/Read.
                (directory / "final_candidate.raw").write_bytes(output.read_bytes())
            if fatal:
                outcome = {"transport": transport, "errors": [str(fatal)], "protocol_valid": False,
                           "output_sha256": None, "failure_codes": [fatal.code],
                           "elapsed_seconds": time.monotonic() - attempt_started}
                write_json(directory / "outcome.json", outcome)
                summary.update(status="failed", fatal_model_error=fatal.to_dict(),
                               elapsed_seconds=summary["elapsed_seconds"] + time.monotonic() - started)
                write_json(task["root"] / "task_summary.json", summary)
                recorder.event("attempt_finished", attempt=number, **outcome)
                recorder.event("task_failed", **fatal.to_dict())
                raise fatal
            errors = task["validator"](payload)
            errors.extend(evidence.errors(task["inputs"]))
            actual_hash = hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else None
            if transport == "query_error":
                errors.extend(failure)
            if channel_failure:
                errors.append(str(channel_failure))
            write_json(directory / "outcome.json", {"transport": transport, "errors": errors,
                        "protocol_valid": not errors, "output_sha256": actual_hash,
                        "failure_codes": failure_codes(output, errors, transport),
                        "schema_feedback_count": schema_feedback_count, "stop_correction_count": stop_repairs,
                        "candidate_write_count": evidence.writes,
                        "elapsed_seconds": time.monotonic() - attempt_started})
            recorder.event("attempt_finished", attempt=number, transport=transport, protocol_valid=not errors, errors=errors)
            if not errors:
                task["payload"] = payload
                summary.update(success=True, recovered=summary["recovered"] or number > 1)
                if number == 1:
                    summary["first_attempt_valid"] = True
                break
            task["last_feedback"] = errors + ["Fix the listed fields/paths using the supplied evidence; do not repeat the unchanged invalid output."]
            if any("reads missing" in error for error in errors):
                task["last_feedback"].append('Read fact shards completely without offset/limit. Partial/truncated content does not satisfy required evidence.')
            previous = directory / "final_candidate.raw"
            intents = sorted(directory.glob("write_intent_*.raw"))
            task["previous_candidate"] = str(previous if previous.is_file() or not intents else intents[-1])
            if channel_failure:
                task["last_feedback"].append('The previous tool-permission channel closed. A fresh independent session/input stream is now available. Reread facts and submit to the current path; the prior write intent was not executed.')
            elif transport == "timeout":
                task["last_feedback"].append('The previous attempt exceeded its deadline. Read mandatory facts first, shorten reasoning/action lists, and complete the smallest valid output plus readback. Do not repeat a long workflow.')
            elif transport == "query_error":
                task["last_feedback"].append('Model/transport failure: reread listed facts in the fresh session, inspect any valid artifact, and submit at the new output path.')
        if not summary.get("success") and channel_failure:
            error = FatalModelError("agent_tool_control_unavailable",
                "SDK/CLI tool permission channel remained unavailable after bounded fresh-session retries; simulation stopped before the next round.")
            error.context.update(enterprise_id=self.enterprise_id, role=summary["role"],
                                 round_id=task["recorder"].identity["round_id"],
                                 control_channel_error_attempts=summary.get("control_channel_error_attempts", 0),
                                 runtime_diagnostics_file=str(directory / "runtime_diagnostics.json"),
                                 last_tool_error=channel_failure.to_dict())
            summary.update(status="failed", fatal_model_error=error.to_dict(), elapsed_seconds=summary["elapsed_seconds"] + time.monotonic() - started)
            write_json(task["root"] / "task_summary.json", summary)
            recorder.event("task_failed", **error.to_dict())
            raise error
        summary["elapsed_seconds"] += time.monotonic() - started
        summary["status"] = "finished"
        write_json(task["root"] / "task_summary.json", summary)
        recorder.event("task_finished", **summary)

    async def dispatch(self, round_id, phase):
        pending = self.pending.get(phase)
        if not pending:
            return
        state = pending["state"]
        if state["decision_available"]:
            for spec, workflow, task in pending["groups"]:
                try:
                    await self._dispatch_group(spec, workflow, task, state)
                except FatalModelError as error:
                    state["fatal_model_error"] = error.to_dict()
                    self._finish(phase, unavailable_reason=str(error))
                    raise
                except Exception as error:
                    state["unknown_execution_results"] += 1
                    pending["recorder"].event("dispatch_error", department=spec.role.lower(), error=str(error))
        else:
            pending["recorder"].event("technical_no_submission", reason=state.get("failure_reason", "Incomplete model decisions after retries"))
            # Compatibility records describe unavailable decisions, never fictitious model choices.
            for dept in state.get("expected_functions", []):
                target = self.root / "department" / dept / f"day{round_id}"
                write_json(target / f"{dept}_result.json", {"status": "decision_unavailable", "model_generated": False,
                           "result": {"success": [], "error": []}, "technical_no_submission": True})
        for dept in pending["auto_departments"]:
            MultiTenantUtils.generate_dept_auto_action(dept.dept_id, self.enterprise, round_id)
        self._finish(phase)

    async def _dispatch_group(self, spec, workflow, task, state):
        dept = spec.role.lower()
        actions = deepcopy(workflow)
        successful, failed = [], []
        result_records = []
        blocked = task.setdefault("already_dispatched", set())
        records = state.setdefault("dispatch_records", {}).setdefault(dept, [])
        proposed = state.setdefault("proposed_actions", {}).setdefault(dept, [])
        proposed.extend(actions)
        for index, action in enumerate(actions):
            action_key = action_identity(action)
            if action_key in blocked:
                task["recorder"].event("duplicate_dispatch_blocked", department=dept, action=action)
                continue
            acknowledged = {}
            action_id = f"{task['summary']['task_id']}:{dept}:{uuid.uuid4().hex}"
            lineage = ({"task_id": task["summary"]["task_id"], "attempt_number": task["summary"]["attempts"],
                        "parent_action_ids": list(task.get("repair_parent_action_ids", {}).get(dept, []))} if self.matched_fd else {})
            if action["action"]["action_name"] == "action_pass":
                # A genuine model choice needs no economic POST and is distinct from unavailable output.
                stored = {"result": {"success": [{"action_type": "action_pass", "success": True,
                          "module_type": action["module_type"], "params": action["action"]["action_param"],
                          "message": action["action_reason"], "model_generated": True}]}}
                result_records.append(stored)
                records.append({**lineage, "action_id": action_id, "proposed": action, "status": "success",
                                "normalized": [action], "response": stored})
                blocked.add(action_key)
                task["recorder"].event("model_action_pass", action_id=action_id, department=dept, action=action)
                continue

            def observe(payload, response):
                acknowledged.update(payload=payload, response=response)

            output = Path(spec.output_file_path)
            # One action at a time: an acknowledged success is never replayed by repair.
            write_json(output, [action])
            task["recorder"].event("action_dispatch_started", action_id=action_id, department=dept, action=action)
            response = StaticUtils.execute_action(
                output, output.with_name(f"{dept}_dispatch_{action_id.split(':')[-1]}_result.json"), "run",
                output.with_name(f"{dept}_dispatch_{action_id.split(':')[-1]}_error.json"), dept,
                state["round_id"], 0, self.enterprise, Path(spec.blackboard_path), execution_observer=observe)
            stored = acknowledged.get("response") or read_json(output.with_name(f"{dept}_dispatch_{action_id.split(':')[-1]}_result.json"), {})
            result_records.append(stored)
            status = classify_execution(stored, response)
            normalized = acknowledged.get("payload")
            if normalized is not None and digest(normalized) != digest([action]):
                state["normalization_changes"] += 1
            leaves = execution_leaves(stored)
            if status == "success" and leaves and all(item.get("action_type") == "action_pass" for item in leaves):
                # The legacy guard may turn an impossible proposal into a pass. This is
                # a refused proposal, not a successful model decision or a model pass.
                status = "normalized_noop"
                state["pre_execution_rejections"] += 1
            records.append({**lineage, "action_id": action_id, "proposed": action, "status": status,
                            "normalized": acknowledged.get("payload"), "response": stored})
            task["recorder"].event("action_result", action_id=action_id, department=dept,
                                   action=action, status=status, normalized=acknowledged.get("payload"), response=stored)
            if status in {"rejected", "partial", "normalized_noop"}:
                state["business_rejections"] += 1
            if status in {"rejected", "normalized_noop"}:
                failed.append(action)
            elif status == "unknown":
                state["unknown_execution_results"] += 1
                blocked.add(action_key)
            else:
                successful.append(action)
                blocked.add(action_key)
                if status == "partial":
                    state["unresolved_rejections"] += 1
        if failed:
            state["unresolved_rejections"] += len(failed)
        if failed and task["summary"]["attempts"] < self.policy["max_attempts"]:
            if self.matched_fd:
                task.setdefault("repair_parent_action_ids", {})[dept] = [r["action_id"] for r in records if r["proposed"] in failed]
            # Model changes choices based on concrete result feedback; only rejected actions may be repaired.
            retry_task = task
            original_prompt, original_validator = task["prompt"], task["validator"]
            retry_task["payload"] = None
            # Output validity and business recovery are different measurements.
            initial_valid = task["summary"]["success"]
            task["summary"]["success"] = False
            if self.central:
                task["validator"] = lambda value: validate_bundle(
                    value, enterprise_id=self.enterprise_id, round_id=state["round_id"], phase=state["phase"],
                    snapshot_id=state["snapshot_id"], departments=[dept], valid_action=MultiTenantUtils._is_valid_action_payload,
                    enterprise_name=self.enterprise)
            else:
                task["validator"] = lambda value: validate_workflow(value, dept, self.enterprise_id, MultiTenantUtils._is_valid_action_payload)
            repair_validator = task["validator"]
            rejected_keys = {action_identity(item) for item in failed}
            def validate_repair(value):
                errors = repair_validator(value)
                if not errors:
                    candidates = value["decisions"][dept] if self.central else value
                    if any(action_identity(item) in rejected_keys or action_identity(item) in blocked for item in candidates):
                        errors.append("Repair repeats a rejected, successful or unknown action unchanged; change its parameters/choice or explicitly pass.")
                return errors
            task["validator"] = validate_repair
            repair_scope = (f'The decision set contains only this function.' if self.central else
                            'Output only the corrected department action array, not an enterprise decisions object.')
            task["prompt"] = original_prompt + (f'\nBusiness repair overrides the earlier full-function requirement: repair only {dept}，{repair_scope}These actions succeeded or have unknown receipts; do not resubmit: ' + json.dumps([a for a in actions if a not in failed], ensure_ascii=False))
            task["last_feedback"] = ["Business validation rejected the listed actions. Adjust quantities/choice or explicitly pass.",
                                     {"rejected": failed, "actual_execution_results": result_records}]
            # Refresh authorized state after prior actions, so repair reasons can actually change decisions.
            MultiTenantUtils.save_enterprise_observations(self.enterprise)
            MultiTenantUtils.handle_enterprises_observations(state["round_id"], self.enterprise)
            current = self.runtime.skill_runner._build_skill_run_spec(
                next(d for d in self.runtime.enterprise_spec.departments if d.dept_id == dept), state["round_id"])
            feedback_path = self._inputs(Path(task["root"]), f"execution_feedback_{task['summary']['attempts']}", {
                "state": read_json(current.state_path, {}), "coordination": read_json(current.blackboard_path, {}),
                "trade_card": read_json(current.trade_decision_card_path, {}) if current.trade_decision_card_path else {},
                "execution_results": result_records, "rejected": failed,
            })
            if self.central or self.policy.get("version") in {FD4_MA_VERSION, FD_ABLATION_VERSION}:
                feedback_package = self._pack_context(feedback_path)
                task["inputs"].extend(feedback_package["required"])
                task.setdefault("complete_read_inputs", []).extend(feedback_package["required"])
                task.setdefault("optional_inputs", []).extend(feedback_package["optional"])
                feedback_path = feedback_package["index_path"]
            else:
                task["inputs"].append(feedback_path.resolve())
            task["prompt"] += f'\nRead latest execution state and failure evidence: {feedback_path}. If sharded, read all required shards. Latest facts override the original snapshot.'
            await self._run_task(task)
            repair_valid = task["payload"] is not None
            task["summary"]["business_repair_output_valid"] = repair_valid
            task["summary"]["success"] = initial_valid
            write_json(task["root"] / "task_summary.json", task["summary"])
            task["prompt"], task["validator"] = original_prompt, original_validator
            if task["payload"] is not None:
                repaired = task["payload"]["decisions"][dept] if self.central else task["payload"]
                if repaired:
                    state["business_repairs"] += 1
                    # Replaced refusals are resolved only if the replacement really executes.
                    state["unresolved_rejections"] -= len(failed)
                    await self._dispatch_group(spec, repaired, task, state)
        write_json(spec.output_file_path, proposed)
        leaf_success, leaf_errors = [], []
        for record in records:
            leaves = execution_leaves(record["response"])
            leaf_success.extend(item for item in leaves if item.get("success") is True)
            leaf_errors.extend(item for item in leaves if item.get("success") is False)
        write_json(Path(spec.output_file_path).with_name(f"{dept}_result.json"), {
            "result": {"success": leaf_success, "error": leaf_errors}, "dispatch_records": records,
            "decision_source": self.policy["architecture"], "task_id": task["summary"]["task_id"],
        })

    def _finish(self, phase, unavailable_reason=None):
        pending = self.pending.pop(phase, None)
        if not pending:
            return
        state = pending["state"]
        if unavailable_reason:
            state.update(decision_available=False, technical_no_submission=True, failure_reason=unavailable_reason)
        state["elapsed_seconds"] = time.monotonic() - state.pop("started_at_monotonic")
        phase_root = Path(state.pop("phase_root"))
        if self.matched_fd:
            write_json(phase_root / "communications.json", state.get("communications", []) if state["decision_available"] else [])
        write_json(phase_root / "phase_summary.json", state)
        pending["recorder"].event("phase_finished", decision_available=state["decision_available"],
                                   technical_no_submission=state["technical_no_submission"])
        write_json(self.workspace / "firm_architecture_quality.json", aggregate_quality(self.workspace))


def execution_leaves(stored):
    outcomes = []
    def walk(value):
        if isinstance(value, dict):
            if value.get("action_type") and value.get("action_type") != "batch_execute" and isinstance(value.get("success"), bool):
                outcomes.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(stored)
    return outcomes


def classify_execution(stored, outer):
    """Never infer an economic success merely from HTTP/wrapper status=success."""
    outcomes = [item["success"] for item in execution_leaves(stored)]
    if outcomes:
        return "partial" if any(outcomes) and not all(outcomes) else "rejected" if not any(outcomes) else "success"
    if isinstance(stored, dict) and isinstance(stored.get("result"), dict):
        result = stored["result"]
        if result.get("failed") or result.get("error"):
            return "rejected"
    return "unknown"


def failure_codes(output, errors, transport):
    codes = []
    if transport != "finished":
        codes.append("attempt_timeout" if transport == "timeout" else "tool_control_channel_closed" if transport == "tool_control_error" else "model_call_error")
    if not Path(output).exists():
        codes.append("missing_output")
    elif read_json(output) is None:
        codes.append("invalid_json")
    if any("Skill was not invoked" in error for error in errors):
        codes.append("skill_not_invoked")
    if any("reads missing" in error for error in errors):
        codes.append("required_read_missing")
    if any("Write followed by Read" in error for error in errors):
        codes.append("readback_missing_or_stale")
    if any("Unauthorized tool executed" in error for error in errors):
        codes.append("unauthorized_tool_execution")
    if errors and not any(code in codes for code in ("invalid_json", "missing_output")):
        codes.append("protocol_validation_failed")
    return codes


def action_identity(action):
    """Changing an explanation does not create a new economic operation."""
    return digest({k: action.get(k) for k in ("action", "module_type", "executor_id")})
