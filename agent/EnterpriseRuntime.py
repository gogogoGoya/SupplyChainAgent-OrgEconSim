from struct import pack
from agent.data_config import DepartmentSpec, EnterpriseSpec
from agent.SessionRegistry import SessionRegistry
import anyio
import json
import time
import copy
import asyncio
import inspect
from pathlib import Path
from typing import Dict, List, Callable, Any, Optional
import os
import sys
from datetime import datetime
from urllib.parse import urlparse

from agent.static_utils import StaticUtils
from agent.multi_tenant_utils import MultiTenantUtils
from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    AssistantMessage,
    TextBlock,
    ResultMessage
)
from agent.data_config import DepartmentSpec, EnterpriseSpec
from agent.GlobalDepartmentLockManager import GlobalDepartmentLockManager
from agent.skill_runner import SkillRunner
from agent.scripted_rule_runner import ScriptedRuleRunner

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from coordination import EnterpriseCommunicationCoordinator
from config.environment_config import EnvironmentConfig
from config.simulation_preset_config import get_runtime_injection_config
from config.integration_profiles import resolve_integration_profiles
from agent.fd_experiment_runtime import read_policy, write_neutral_analysis
from core.model_failure import gather_model_tasks

# ============================================================

# ============================================================

class EnterpriseRuntime:
    '\n    single-enterpriseRun-time\n    It\'s for your existing Enterprise CEO, but it\'s only for "the family."\n        '

    def __init__(
        self,
        client_dir: str,
        enterprise_spec: EnterpriseSpec,
        session_registry: SessionRegistry,
        model_name: str,
        anthropic_base_url: Optional[str] = None,
        max_department_concurrency: int = 2,  # Default runs up to 2 department
        workspace_dir: Optional[Path] = None,
    ):
        self.client_dir = Path(client_dir)
        self.workspace_dir = Path(workspace_dir) if workspace_dir else (
            self.client_dir / "workspace_multi"
        )
        self.enterprise_spec = enterprise_spec
        self.session_registry = session_registry

        self.model_name = model_name
        self.anthropic_base_url = (
            anthropic_base_url or EnvironmentConfig.LOCAL_AGENT_GATEWAY_BASE_URL
        )
        
        # parameter Co-control semaphore
        self.department_semaphore = asyncio.Semaphore(max_department_concurrency)
        self.skill_runner = SkillRunner(
            self.enterprise_spec,
            session_registry,
            self.build_options,
            Path(client_dir),
            workspace_dir=self.workspace_dir,
        )
        self.scripted_rule_runner = ScriptedRuleRunner(
            Path(client_dir),
            self.enterprise_spec,
            workspace_dir=self.workspace_dir,
        )
        # A fully scripted controller has no model dependency. Partial scripted
        # policies still require the gateway for their remaining model decisions.
        scripted_policy = get_runtime_injection_config().get("scripted_rule_policy") or {}
        scripted_departments = (scripted_policy.get("departments") or {}).get("apply_to_departments") or ["all"]
        fully_scripted = (
            bool(scripted_policy.get("enabled"))
            and bool((scripted_policy.get("analyst") or {}).get("enabled", True))
            and all(not dept.enabled or dept.dept_id == "finance"
                    or "all" in scripted_departments or dept.dept_id in scripted_departments
                    for dept in self.enterprise_spec.departments)
        )
        if not fully_scripted:
            self._validate_agent_gateway_config()


    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        if not parsed.scheme or not parsed.netloc:
            return ""
        host = (parsed.hostname or "").lower()
        port = parsed.port
        scheme = parsed.scheme.lower()
        path = (parsed.path or "").rstrip("/")
        netloc = f"{host}:{port}" if port else host
        return f"{scheme}://{netloc}{path}"

    @staticmethod
    def _is_local_project_service_url(value: str) -> bool:
        parsed = urlparse(str(value or "").strip())
        host = (parsed.hostname or "").lower()
        return host in {"127.0.0.1", "localhost", "::1"} and parsed.port in {8000, 3000}

    def _validate_agent_gateway_config(self) -> None:
        agent_base = self._canonical_url(self.anthropic_base_url)
        simulation_base = self._canonical_url(EnvironmentConfig.SIMULATION_API_BASE_URL)
        if not agent_base:
            raise RuntimeError(
                "Agent gateway is not configured. Set ANTHROPIC_BASE_URL to the "
                "OpenAI/Anthropic-compatible Agent gateway, not the simulation server."
            )
        if simulation_base and agent_base == simulation_base:
            raise RuntimeError(
                "Agent gateway configuration points to the simulation backend: "
                f"ANTHROPIC_BASE_URL={self.anthropic_base_url}, "
                f"SIMULATION_API_BASE_URL={EnvironmentConfig.SIMULATION_API_BASE_URL}. "
                "Set ANTHROPIC_BASE_URL to the model/Agent gateway instead."
            )
        if self._is_local_project_service_url(self.anthropic_base_url):
            raise RuntimeError(
                "Agent gateway configuration looks like a local project service "
                f"({self.anthropic_base_url}), not a model gateway. In this project "
                "127.0.0.1:8000 is the simulation backend and 127.0.0.1:3000 is "
                "the visualization dev server. Set ANTHROPIC_BASE_URL to the real "
                "Agent gateway."
            )

    def _get_analyst_policy(self) -> Dict[str, Any]:
        run_meta = MultiTenantUtils._read_json_if_exists(
            self._workspace_root() / "run_meta.json",
            {},
        )
        scenario_runtime = (
            ((run_meta.get("scenario_config") or {}).get("runtime_injection") or {})
            if isinstance(run_meta, dict)
            else {}
        )
        run_policy = scenario_runtime.get("analyst_policy") or {}
        if run_policy:
            return run_policy
        runtime_config = get_runtime_injection_config()
        return runtime_config.get("analyst_policy") or {
            "full_analysis_interval_rounds": 1,
            "force_on_round_zero": True,
            "fallback_to_latest_archive": True,
        }

    def _single_enterprise_case_enabled(self) -> bool:
        run_meta = MultiTenantUtils._read_json_if_exists(
            self._workspace_root() / "run_meta.json",
            {},
        )
        if run_meta:
            if str(run_meta.get("orchestrator") or "") != "single_enterprise":
                return False
            scenario_config = run_meta.get("scenario_config") or {}
            runtime_injection = scenario_config.get("runtime_injection") or {}
            case_policy = (
                run_meta.get("single_enterprise_case")
                or scenario_config.get("single_enterprise_case")
                or runtime_injection.get("single_enterprise_case_policy")
                or {}
            )
            return isinstance(case_policy, dict) and bool(case_policy.get("enabled"))

        runtime_config = get_runtime_injection_config()
        case_policy = runtime_config.get("single_enterprise_case_policy") or {}
        return isinstance(case_policy, dict) and bool(case_policy.get("enabled"))

    def _workspace_root(self) -> Path:
        workspace_dir = getattr(self, "workspace_dir", None)
        if workspace_dir is not None:
            return Path(workspace_dir)
        return Path(self.client_dir) / "workspace_multi"

    def _get_coordination_profile(self) -> Dict[str, Any]:
        run_meta_path = self._workspace_root() / "run_meta.json"
        run_meta = MultiTenantUtils._read_json_if_exists(run_meta_path, {})
        profiles = run_meta.get("integration_profiles")
        if not isinstance(profiles, dict):
            profiles = resolve_integration_profiles(
                run_meta.get("scenario_config") or {}
            )
        return (
            ((profiles.get("capabilities") or {}).get("coordination"))
            or {}
        )

    def _merge_department_communications(self, round_id: int) -> Optional[Dict[str, Any]]:
        if read_policy(self._workspace_root(), round_id).get("condition") == "no_shared_coordination":
            return None
        profile = self._get_coordination_profile()
        validation_mode = str(
            profile.get("communication_validation") or "off"
        )
        if not profile.get("blackboard_enabled") and validation_mode == "off":
            return None

        enabled_departments = [
            dept.dept_id
            for dept in self.enterprise_spec.departments
            if dept.enabled
        ]
        source_departments = [
            dept.dept_id
            for dept in self.enterprise_spec.departments
            if dept.enabled and dept.type == "Agent"
        ]
        coordinator = EnterpriseCommunicationCoordinator(
            enterprise_workspace=(
                self._workspace_root()
                / "enterprises"
                / self.enterprise_spec.enterprise_name
            ),
            enabled_departments=enabled_departments,
            source_departments=source_departments,
            validation_mode=validation_mode,
            semantic_rule_pack=str(
                profile.get("semantic_rule_pack") or "none"
            ),
            semantic_auto_repair=bool(
                profile.get("semantic_auto_repair", False)
            ),
        )
        return coordinator.merge_round(round_id)

    def _should_run_analyst(self, round_id: int) -> bool:
        if read_policy(self._workspace_root(), round_id).get("condition") == "no_analyst":
            return False
        if self._single_enterprise_case_enabled():
            return True
        policy = self._get_analyst_policy()
        if round_id == 0 and policy.get("force_on_round_zero", True):
            return True
        force_on_rounds = {
            int(value)
            for value in (policy.get("force_on_rounds") or [])
            if str(value).lstrip("-").isdigit()
        }
        if int(round_id) in force_on_rounds:
            return True
        run_meta = MultiTenantUtils._read_json_if_exists(
            self._workspace_root() / "run_meta.json",
            {},
        )
        scenario_runtime = (
            ((run_meta.get("scenario_config") or {}).get("runtime_injection") or {})
            if isinstance(run_meta, dict)
            else {}
        )
        long_run_policy = scenario_runtime.get("long_run_experiment_policy") or {}
        if long_run_policy.get("enabled"):
            event_turns = {
                int(value)
                for value in (long_run_policy.get("event_turns") or [])
                if str(value).lstrip("-").isdigit()
            }
            follow_up_offsets = {
                int(value)
                for value in (
                    long_run_policy.get("event_follow_up_offsets") or []
                )
                if str(value).lstrip("-").isdigit()
                and int(value) >= 0
            }
            if any(
                int(round_id) == event_turn + offset
                for event_turn in event_turns
                for offset in follow_up_offsets
            ):
                return True
        interval = max(1, int(policy.get("full_analysis_interval_rounds", 1) or 1))
        return round_id % interval == 0

    def _build_stderr_callback(self, role: str):
        """Capture Claude CLI stderr so subprocess failures keep concrete diagnostics."""
        log_dir = self.client_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"claude_stderr_{datetime.now().strftime('%Y%m%d')}.log"

        def _callback(line: str) -> None:
            timestamp = datetime.now().isoformat()
            record = (
                f"{timestamp} | enterprise_id={self.enterprise_spec.enterprise_id} "
                f"| enterprise_name={self.enterprise_spec.enterprise_name} "
                f"| role={role} | {line.rstrip()}\n"
            )
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(record)
            except Exception:
                pass

        return _callback
        
    # ----------------------------

    # ----------------------------

    def build_system_prompt(self, role: str) -> str:
        return f"""
You belong to enterprise {self.enterprise_spec.enterprise_name} (ID: {self.enterprise_spec.enterprise_id}).
Your current role is {role}.

Requirements:
1. Reason only from this enterprise's perspective.
2. Do not mix in another enterprise's private information.
3. Advance operations through the skills available to your role.
4. Follow the applicable SKILL.md instructions and all workspace file constraints.
        """.strip()

    def build_options(self, role: str) -> ClaudeAgentOptions:
        """
        Note:
        This points the cwd to the enterprise level workspace root directory to ensure enterprise isolation.
                """
        agent_env = {
            **EnvironmentConfig.get_agent_env(),
            "ANTHROPIC_BASE_URL": self.anthropic_base_url,
            "ANTHROPIC_MODEL": self.model_name,
        }
        agent_env = {key: value for key, value in agent_env.items() if value}
        options = ClaudeAgentOptions(
            cwd=str(self.workspace_dir / "enterprises" / self.enterprise_spec.enterprise_id),
            tools=["Read", "Write", "Skill"],
            allowed_tools=["Read", "Write", "Skill"],
            permission_mode="acceptEdits",
            setting_sources=["project"],
            include_partial_messages=False,
            system_prompt=self.build_system_prompt(role),
            model=self.model_name,
            stderr=self._build_stderr_callback(role),
            env=agent_env,
        )

        return options

    # ----------------------------

    # ----------------------------

    async def handle_department_action(self, round_id: int) -> List[dict]:
        enabled_departments = [d for d in self.enterprise_spec.departments if d.enabled]

        async def run_department_with_semaphore(dept, round_id):
            async with self.department_semaphore:  # Use semaphore to control co-production
                # return await self.run_department(dept, round_id)
                return await self.skill_runner.run_department(dept, round_id)

        tasks = [
            asyncio.create_task(run_department_with_semaphore(dept, round_id))
            for dept in enabled_departments
        ]

        results = await gather_model_tasks(*tasks)
        self._merge_department_communications(round_id)
        

        all_dept_messages = []
        for result in results:
            if result:
                all_dept_messages.extend(result)
        
        return all_dept_messages

    # ----------------------------

    # ----------------------------

    async def run_workflow(self, round_id: int) -> List[dict]:
        firm_runner = self.firm_architecture_runner()
        if firm_runner is not None:
            messages = await firm_runner.prepare(round_id, "decision")
            if firm_runner.policy.get("dispatch_protocol") == "enterprise_inline_v2":
                await firm_runner.dispatch(round_id, "decision")
            return messages
        messages: List[dict] = []

        analyst_ran = False
        fd_policy = read_policy(self._workspace_root(), round_id)
        scripted_enabled = self.scripted_rule_runner.enabled()
        if fd_policy.get("condition") == "no_analyst":
            write_neutral_analysis(
                self._workspace_root() / "enterprises" / self.enterprise_spec.enterprise_name,
                self.enterprise_spec.enterprise_name, round_id,
                [d.dept_id for d in self.enterprise_spec.departments if d.enabled],
            )
            analyst_ran = True  # The neutral interface is ready; never call a fallback Analyst.
        elif scripted_enabled:
            messages.extend(self.scripted_rule_runner.write_analysis(round_id))
            analyst_ran = True
        elif self._should_run_analyst(round_id):
            analyst_messages = await self.skill_runner.run_analyst(round_id)
            messages.extend(analyst_messages)
            analyst_ran = True

        strict_cobweb_c3_analysis = bool(
            analyst_ran
            and not scripted_enabled
            and self.skill_runner.cobweb_profit_objective_enabled()
        )
        strict_cobweb_c3_analysis = strict_cobweb_c3_analysis or bool(fd_policy)
        analysis_ready = MultiTenantUtils.ensure_enterprise_analysis_file(
            self.enterprise_spec.enterprise_name,
            round_id=round_id,
            require_round_id=(
                round_id if strict_cobweb_c3_analysis else None
            ),
            allow_archive_fallback=not strict_cobweb_c3_analysis,
        )
        if not analysis_ready and scripted_enabled:
            messages.extend(self.scripted_rule_runner.write_analysis(round_id))
            analyst_ran = True
            analysis_ready = MultiTenantUtils.ensure_enterprise_analysis_file(
                self.enterprise_spec.enterprise_name,
                round_id=round_id,
                require_round_id=round_id if fd_policy else None,
                allow_archive_fallback=not bool(fd_policy),
            )
        if not analysis_ready and not analyst_ran:
            analyst_messages = await self.skill_runner.run_analyst(round_id)
            messages.extend(analyst_messages)
            strict_cobweb_c3_analysis = bool(
                self.skill_runner.cobweb_profit_objective_enabled()
            ) or bool(fd_policy)
            analysis_ready = MultiTenantUtils.ensure_enterprise_analysis_file(
                self.enterprise_spec.enterprise_name,
                round_id=round_id,
                require_round_id=(
                    round_id if strict_cobweb_c3_analysis else None
                ),
                allow_archive_fallback=not strict_cobweb_c3_analysis,
            )
        if not analysis_ready:
            raise FileNotFoundError(
                f"analysis.json unavailable for {self.enterprise_spec.enterprise_name} at round {round_id}"
            )

        if fd_policy:
            analysis_file = self._workspace_root() / "enterprises" / self.enterprise_spec.enterprise_name / "analysis.json"
            audit_path = analysis_file.parent / "fd_audit" / f"day{round_id}" / "analysis_provenance.json"
            payload = MultiTenantUtils._read_json_if_exists(analysis_file, {})
            MultiTenantUtils._write_json(audit_path, {
                "condition": fd_policy["condition"], "requested_round": round_id,
                "analysis_round": payload.get("round_id"),
                "source": "neutral_interface" if fd_policy["condition"] == "no_analyst" else "scripted" if scripted_enabled else "model",
                "archive_fallback_allowed": False,
            })

        MultiTenantUtils.handle_enterprises_observations(round_id = round_id, enterprise_name = self.enterprise_spec.enterprise_name)

        dept_messages = await self.handle_department_action(round_id)
        messages.extend(dept_messages)
        

        MultiTenantUtils.save_model_messages(
            enterprise_name=self.enterprise_spec.enterprise_name,
            round_id=round_id,
            messages=messages
        )
        
        return messages

    async def run_trade(
        self,
        round_id: int,
        observation_already_refreshed: bool = False,
    ) -> List[dict]:
        firm_runner = self.firm_architecture_runner()
        if firm_runner is not None:
            messages = await firm_runner.prepare(round_id, "trade")
            if firm_runner.policy.get("dispatch_protocol") == "enterprise_inline_v2":
                await firm_runner.dispatch(round_id, "trade")
            return messages
        enabled_departments = [d for d in self.enterprise_spec.departments if d.enabled and d.need_trade]
        if not enabled_departments:
            return []

        fd_policy = read_policy(self._workspace_root(), round_id)
        analysis_ready = MultiTenantUtils.ensure_enterprise_analysis_file(
            self.enterprise_spec.enterprise_name,
            round_id=round_id,
            require_round_id=round_id if fd_policy else None,
            allow_archive_fallback=not bool(fd_policy),
        )
        if fd_policy and not analysis_ready:
            raise FileNotFoundError(
                f"FD trade requires current analysis.json for {self.enterprise_spec.enterprise_name} "
                f"at round {round_id}; archive fallback is disabled"
            )
        if not observation_already_refreshed:
            MultiTenantUtils.save_enterprise_observations(self.enterprise_spec.enterprise_name)
        MultiTenantUtils.handle_enterprises_observations(round_id = round_id, enterprise_name = self.enterprise_spec.enterprise_name)
        MultiTenantUtils.archive_enterprise_trade_files(self.enterprise_spec.enterprise_name, round_id)

        async def run_trade_with_semaphore(dept, round_id):
            async with self.department_semaphore:  # Use semaphore to control co-production
                return await self.skill_runner.run_department(dept, round_id, True)

        tasks = [
            asyncio.create_task(run_trade_with_semaphore(dept, round_id))
            for dept in enabled_departments
        ]
        results = await gather_model_tasks(*tasks)
        # Trade preparation refreshes blackboard.json, so restore the optional
        # enterprise-private coordination layer on the final snapshot.
        self._merge_department_communications(round_id)
        all_trade_messages = []
        for result in results:
            if result:
                all_trade_messages.extend(result)
        return all_trade_messages
        
    def firm_architecture_runner(self):
        from config.firm_architecture_experiment import policy_from_config, VERSION, FD_ABLATION_VERSION
        meta = MultiTenantUtils._read_json_if_exists(self._workspace_root() / "run_meta.json", {})
        policy = policy_from_config(meta.get("scenario_config") or {})
        if not policy:
            return None
        if policy.get("architecture") == "centralized" and policy.get("version") not in {VERSION, FD_ABLATION_VERSION}:
            raise ValueError('This SA job snapshot uses an older operating regime. Create a new SA job from the scenario card; historical archives remain unchanged.')
        if not getattr(self, "_firm_decision_runner", None):
            from agent.firm_decision_runner import FirmDecisionRunner
            self._firm_decision_runner = FirmDecisionRunner(self, policy)
        return self._firm_decision_runner

    async def dispatch_firm_phase(self, round_id, phase):
        runner = self.firm_architecture_runner()
        if runner is not None:
            await runner.dispatch(round_id, phase)

