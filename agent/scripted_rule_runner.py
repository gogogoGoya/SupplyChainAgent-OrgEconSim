import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.fd_experiment import policy_from_meta
from config.module_config import ProductionConfig


MODULE_TYPE_BY_DEPARTMENT = {
    "finance": "FinanceManager",
    "hr": "HRManager",
    "inventory": "InventoryManager",
    "procurement": "ProcurementManager",
    "production": "ProductionManager",
    "sales": "SalesManager",
}

LOGISTICS_COST_CONFIGS = {
    "road": {"base_fee": 100.0, "unit_fee": 2.0, "transit_time": 3},
    "rail": {"base_fee": 200.0, "unit_fee": 1.5, "transit_time": 2},
    "air": {"base_fee": 500.0, "unit_fee": 5.0, "transit_time": 1},
}

STAFFING_DEPARTMENT_ALIASES = {
    "PRODUCTION": {"PRODUCTION", "production", 'Production', 'Production department'},
    "PROCUREMENT": {"PROCUREMENT", "procurement", 'Procurement', 'Procurement department'},
    "SALES": {"SALES", "sales", 'Sales', 'Sales department'},
    "INVENTORY": {"INVENTORY", "inventory", 'Storage', 'Storage department', 'Inventory', 'Inventory department'},
    "HR": {"HR", "hr", 'Manpower', 'Human resources', 'Human resources department'},
}

STAFFING_DEPARTMENT_TO_STATE_KEY = {
    "PRODUCTION": "production",
    "PROCUREMENT": "procurement",
    "SALES": "sales",
    "INVENTORY": "inventory",
    "HR": "hr",
}

STAFFING_RECRUITMENT_PRIORITY = ["PROCUREMENT", "PRODUCTION", "SALES", "INVENTORY"]


class ScriptedRuleRunner:
    """Deterministic rule-based replacement for Agent Skill execution."""

    def __init__(self, client_dir: Path, enterprise_spec: Any, workspace_dir: Optional[Path] = None):
        self.client_dir = Path(client_dir)
        self.workspace_dir = Path(workspace_dir) if workspace_dir else (
            self.client_dir / "workspace_multi"
        )
        self.enterprise_spec = enterprise_spec
        self.enterprise_name = enterprise_spec.enterprise_name
        self.enterprise_workspace = (
            self.workspace_dir / "enterprises" / self.enterprise_name
        )

    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ScriptedRuleRunner._sanitize_visible_payload(payload)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _sanitize_visible_text(text: Any) -> Any:
        """Keep scripted outputs free of hidden single-case experiment labels."""
        if not isinstance(text, str) or not text:
            return text
        cleaned = text
        replacements = {
            "market_insufficient": "Insufficient demand",
            "market_shortage": "Insufficient demand",
            "raw_material_shortage": "raw material Undercovered",
            "material_shortage": "raw material Undercovered",
            "capacity_bottleneck": "insufficient capacity",
            "staff_shortage": "Understaffed",
            "cash_pressure": "Cash pressure",
            "source_primary_contradiction": "Current operating constraints",
            "primary_issue": "Current operating constraints",
            "discouraged_actions": "Status constraints",
            "discouraged": "Status constraints",
            "Current case": "Current Status",
            "Currentcase": "Current Status",
            "Expected main contradiction": "Current operating constraints",
            "Main paradox": "Operating constraints",
            "Lord's responsibility is department": "Related department",
            "Non-principal department": "Other department",
            "Not Lord.": "Current status is not supported",
        }
        for old, new in replacements.items():
            cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"single_case[_\w-]*",
            "state_driven",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\bcase0[1-9]\b", "state_run", cleaned, flags=re.IGNORECASE)
        return cleaned

    @staticmethod
    def _sanitize_visible_payload(payload: Any) -> Any:
        if isinstance(payload, dict):
            return {
                key: ScriptedRuleRunner._sanitize_visible_payload(value)
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [
                ScriptedRuleRunner._sanitize_visible_payload(value)
                for value in payload
            ]
        return ScriptedRuleRunner._sanitize_visible_text(payload)

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int_set(values: Any) -> set:
        if values is None:
            return set()
        if not isinstance(values, list):
            values = [values]
        result = set()
        for value in values:
            try:
                result.add(int(value))
            except (TypeError, ValueError):
                continue
        return result

    def _run_meta(self) -> Dict[str, Any]:
        return self._read_json(self.workspace_dir / "run_meta.json", {}) or {}

    def _scenario_config(self) -> Dict[str, Any]:
        return self._run_meta().get("scenario_config") or {}

    def _simulation_config(self) -> Dict[str, Any]:
        return self._scenario_config().get("simulation") or {}

    def _enterprise_config(self) -> Dict[str, Any]:
        scenario_config = self._scenario_config()
        candidates = (
            scenario_config.get("enterprise_specs")
            or scenario_config.get("enterprise_configs")
            or []
        )
        for item in candidates:
            if not isinstance(item, dict):
                continue
            identifiers = {
                str(item.get("enterprise_id") or ""),
                str(item.get("id") or ""),
                str(item.get("enterprise_name") or ""),
                str(item.get("name") or ""),
            }
            if self.enterprise_name in identifiers:
                return item
        return {}

    def _is_multi_enterprise_run(self) -> bool:
        run_meta = self._run_meta()
        enterprise_ids = run_meta.get("enterprise_ids") or []
        if isinstance(enterprise_ids, list) and len(enterprise_ids) > 1:
            return True
        profile = (
            ((run_meta.get("integration_profiles") or {}).get("simulation") or {})
            .get("profile")
        )
        return str(profile or "").lower() == "multi_enterprise"

    def _has_upstream_b2b_exchange(self) -> bool:
        if not self._is_multi_enterprise_run():
            return False
        enterprise_config = self._enterprise_config()
        try:
            return int(enterprise_config.get("tier")) > 0
        except (TypeError, ValueError):
            return False

    def _policy(self) -> Dict[str, Any]:
        scenario_config = self._scenario_config()
        runtime_injection = scenario_config.get("runtime_injection") or {}
        return runtime_injection.get("scripted_rule_policy") or {}

    def _supplier_candidates_from_policy(self) -> List[Dict[str, Any]]:
        runtime_injection = self._scenario_config().get("runtime_injection") or {}
        policy = runtime_injection.get("single_enterprise_supplier_selection_policy") or {}
        if not isinstance(policy, dict) or not policy.get("enabled"):
            return []
        target_enterprises = (
            policy.get("target_enterprise_ids")
            or policy.get("enterprise_ids")
            or []
        )
        if target_enterprises and str(self.enterprise_name) not in {
            str(item) for item in target_enterprises
        }:
            return []
        return [
            deepcopy(item)
            for item in policy.get("candidate_suppliers") or []
            if isinstance(item, dict)
        ]

    def _rules(self) -> Dict[str, Any]:
        policy = self._policy()
        defaults = {
            "accept_orders_per_round": 99,
            "market_development_interval": 3,
            "market_development_cost": 50000,
            "market_development_cash_buffer": 0,
            "market_development_workers": 1,
            "market_development_when_no_available_orders": True,
            "market_development_active_market_soft_cap": 4,
            "market_development_low_order_entry_threshold": 1,
            "market_development_reference_order_quantity": 40,
            "market_development_inventory_order_multiple": 3,
            "market_development_surplus_inventory_multiplier": 2.0,
            "market_development_allow_when_backlog_covered": True,
            "cash_guard_threshold": 60000,
            "critical_cash_threshold": 20000,
            "reject_unfulfillable_orders": True,
            "max_procurement_orders_per_round": 1,
            "procurement_budget_cash_share": 0.35,
            "procurement_min_budget": 5000,
            "procurement_reorder_cooldown_rounds": 2,
            "procurement_min_gap_ratio": 0.25,
            "procurement_emergency_gap_ratio": 0.85,
            "material_coverage_target_quantity": 80,
            "material_order_multiplier": 1.0,
            "preferred_logistics_mode": "dynamic",
            "production_round_interval": 1,
            "production_quantity": 50,
            "production_daily_capacity": 40,
            "low_finished_goods_threshold": 40,
            "target_finished_goods_buffer": 90,
            "capacity_pressure_threshold": 0.85,
            "build_line_type": "small",
            "single_case_max_production_lines": 2,
            "production_staff_minimum": 2,
            "procurement_staff_minimum": 1,
            "sales_staff_minimum": 1,
            "inventory_staff_minimum": 1,
            "hr_recruit_people": 2,
            "warehouse_pressure_threshold": 0.88,
            "warehouse_expansion_capacity": 1000,
        }
        configured = (
            policy.get("rules")
            or policy.get("single_enterprise_rules")
            or {}
        )
        # Legacy `single_case` is intentionally ignored here: six diagnostic
        # cases must share one state-driven enterprise rule engine.
        return {**defaults, **configured}

    def _trade_rules(self) -> Dict[str, Any]:
        configured = self._policy().get("trade") or {}
        return {
            "fallback_sales_response": "accept_implicit",
            "fallback_procurement_response": "reject_without_candidate",
            "reject_procurement_proposal_when_unaffordable": True,
            "procurement_accept_cash_buffer": 0,
            **configured,
        }

    def _single_case_policy(self) -> Dict[str, Any]:
        scenario_config = self._scenario_config()
        runtime_injection = scenario_config.get("runtime_injection") or {}
        case_policy = (
            scenario_config.get("single_enterprise_case")
            or runtime_injection.get("single_enterprise_case_policy")
            or {}
        )
        return case_policy if isinstance(case_policy, dict) else {}

    def _single_case_mode_active(self) -> bool:
        # Single-enterprise scripted runs keep their prewarm/evaluation metadata,
        # but department decisions must be driven only by live operating state.
        return False

    def _single_case_primary_issue(self) -> str:
        if not self._single_case_mode_active():
            return ""
        return str(self._single_case_policy().get("primary_issue") or "")

    def _single_case_priority_round(self, round_id: int) -> bool:
        if not self._single_case_mode_active():
            return False
        case_policy = self._single_case_policy()
        try:
            current_round = int(round_id)
        except (TypeError, ValueError):
            current_round = 0
        diagnostic_rounds = self._int_set(case_policy.get("diagnostic_evaluation_rounds"))
        try:
            handoff_day = int(case_policy.get("handoff_day") or 0)
        except (TypeError, ValueError):
            handoff_day = 0
        return current_round in diagnostic_rounds or current_round == handoff_day

    def _single_case_discourages(self, action_name: str) -> bool:
        return False

    def _visible_policy_mode(self) -> str:
        mode = str(self._policy().get("mode") or "scripted_rules")
        if mode == "single_case":
            return "state_driven_rules"
        return mode

    def enabled(self) -> bool:
        return bool(self._policy().get("enabled"))

    def analyst_enabled(self) -> bool:
        policy = self._policy()
        analyst = policy.get("analyst") or {}
        return self.enabled() and bool(analyst.get("enabled", True))

    def department_enabled(self, department: str) -> bool:
        policy = self._policy()
        departments = policy.get("departments") or {}
        allowed = departments.get("apply_to_departments") or ["all"]
        return self.enabled() and ("all" in allowed or department in allowed)

    def write_analysis(self, round_id: int) -> List[dict]:
        policy = self._policy()
        departments = policy.get("departments") or {}
        mode = self._visible_policy_mode()
        dept_targets = {}
        default_departments = ["sales", "procurement", "production", "inventory", "hr"]
        allowed_departments = departments.get("apply_to_departments") or default_departments
        target_departments = (
            default_departments
            if "all" in allowed_departments
            else allowed_departments
        )
        for dept in target_departments:
            dept_targets[dept] = {
                "target": f"Use deterministic {mode} policy for {dept} at round {round_id}.",
                "evaluation": "Action file is generated by scripted rule runner and validated by normal module execution.",
                "reason": 'The department action is generated at runtime by the configured rule-based controller.',
            }

        payload = {
            "schema_version": "scripted_analysis.v1",
            "control_mode": mode,
            "enterprise_summarys": (
                f"{self.enterprise_name} is controlled by deterministic scripted rules "
                f"at round {round_id}."
            ),
            "department_targets": dept_targets,
            "state_driven_rule_policy": self._rules(),
            "evidence_refs": ["runtime_injection.scripted_rule_policy"],
        }
        if policy_from_meta(self._run_meta(), round_id):
            payload.update(round_id=round_id, enterprise_name=self.enterprise_name)
        self._write_json(self.enterprise_workspace / "analysis.json", payload)
        return [
            {
                "role": "scripted_analyst",
                "content": f"scripted analysis generated for {self.enterprise_name} day{round_id}",
            }
        ]

    def write_department_action(self, department: str, round_id: int, need_trade: bool = False) -> List[dict]:
        if not self.department_enabled(department):
            return []
        action = self._build_department_action(department, round_id, need_trade)
        output_path = (
            self.enterprise_workspace
            / "department"
            / department
            / f"day{round_id}"
            / ("finance_advice.json" if department == "finance" else f"{department}_action.json")
        )
        self._write_json(output_path, action)
        self._write_audit(department, round_id, action, need_trade)
        self._write_empty_communication(department, round_id)
        return [
            {
                "role": f"scripted_{department}",
                "content": f"scripted action generated for {self.enterprise_name}/{department}/day{round_id}",
            }
        ]

    def _write_audit(self, department: str, round_id: int, action: Any, need_trade: bool) -> None:
        audit_path = (
            self.enterprise_workspace
            / "department"
            / department
            / f"day{round_id}"
            / ("trade_skill_execution_audit.json" if need_trade else "skill_execution_audit.json")
        )
        self._write_json(
            audit_path,
            {
                "schema_version": "skill_audit.v1",
                "status": "scripted",
                "department": department,
                "round_id": round_id,
                "control_mode": "scripted_rules",
                "skill_called": False,
                "fallback_used": False,
                "actions_count": len(action) if isinstance(action, list) else 1,
            },
        )

    def _write_empty_communication(self, department: str, round_id: int) -> None:
        self._write_json(
            self.enterprise_workspace
            / "department"
            / department
            / f"day{round_id}"
            / f"{department}_communication.json",
            {
                "schema_version": "enterprise_communication.v1",
                "messages": [],
                "reason": "scripted rule runner does not emit cross-department messages by default",
            },
        )

    def _action_pass(self, department: str, reason: str) -> List[Dict[str, Any]]:
        return [
            {
                "action": {
                    "action_name": "action_pass",
                    "action_param": reason,
                },
                "action_reason": reason,
                "module_type": MODULE_TYPE_BY_DEPARTMENT.get(department, ""),
                "executor_id": self.enterprise_name,
            }
        ]

    def _build_department_action(self, department: str, round_id: int, need_trade: bool) -> Any:
        if department == "finance":
            return {
                "schema_version": "finance_advice.v1",
                "status": "scripted",
                "risk_summary": "Rule-based advisory mode is enabled.",
                "cash_position": "read_from_finance_state",
                "budget_constraints": [],
                "recommended_controls": ["preserve_positive_cash", "avoid_unfunded_expansion"],
                "evidence_refs": ["runtime_injection.scripted_rule_policy"],
            }
        if need_trade and department in {"procurement", "sales"}:
            return self._trade_action(department, round_id)
        if department == "sales":
            return self._sales_action(round_id)
        if department == "procurement":
            return self._procurement_action(round_id)
        if department == "production":
            return self._production_action(round_id)
        if department == "hr":
            return self._hr_action(round_id)
        if department == "inventory":
            return self._inventory_action(round_id)
        return self._action_pass(department, f"No scripted rule is configured for {department}.")

    def _trade_action(self, department: str, round_id: int) -> List[Dict[str, Any]]:
        card = self._read_json(
            self.enterprise_workspace
            / "department"
            / department
            / f"day{round_id}"
            / "trade_decision_card.json",
            {},
        ) or {}
        for candidate in card.get("action_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            action_name = candidate.get("action_name")
            if action_name not in {"accept_proposal_order", "reject_proposal_order"}:
                continue
            proposal_id = (
                (candidate.get("action_param") or {}).get("proposal_id")
                or candidate.get("proposal_id")
            )
            if not proposal_id:
                continue
            if (
                department == "procurement"
                and action_name == "accept_proposal_order"
                and not self._procurement_proposal_affordable(card, proposal_id, round_id)
            ):
                return [
                    {
                        "action": {
                            "action_name": "reject_proposal_order",
                            "action_param": {"proposal_id": proposal_id},
                        },
                        "action_reason": (
                            "The rule-based controller rejects the candidate proposal because it violates the cash constraint."
                            "C1 Fixed rules reject unaffordable procurement to avoid verification failure."
                        ),
                        "module_type": MODULE_TYPE_BY_DEPARTMENT.get(department, ""),
                        "executor_id": self.enterprise_name,
                    }
                ]
            return [
                {
                    "action": {
                        "action_name": action_name,
                        "action_param": {"proposal_id": proposal_id},
                    },
                    "action_reason": (
                        "The rule-based controller selects a structured candidate action from trade_decision_card."
                        "Response to proposal exchange."
                    ),
                    "module_type": MODULE_TYPE_BY_DEPARTMENT.get(department, ""),
                    "executor_id": self.enterprise_name,
                }
            ]

        for item in card.get("review_queue") or []:
            if isinstance(item, dict) and item.get("proposal_id"):
                reason_codes = {
                    str(code)
                    for code in (item.get("reason_codes") or [])
                }
                trade_rules = self._trade_rules()
                fallback_mode = str(
                    trade_rules.get(
                        "fallback_sales_response"
                        if department == "sales"
                        else "fallback_procurement_response"
                    )
                    or ""
                )
                action_name = "reject_proposal_order"
                if fallback_mode == "accept_all" or (
                    department == "sales"
                    and fallback_mode == "accept_implicit"
                    and "IMPLICIT_ROUTE" in reason_codes
                ):
                    action_name = "accept_proposal_order"
                return [
                    {
                        "action": {
                            "action_name": action_name,
                            "action_param": {"proposal_id": item.get("proposal_id")},
                        },
                        "action_reason": (
                            "Pending proposals exist, but none meets the rule-based controller's acceptance criteria."
                            f"respond using scripted trade fallback={fallback_mode}."
                        ),
                        "module_type": MODULE_TYPE_BY_DEPARTMENT.get(department, ""),
                        "executor_id": self.enterprise_name,
                    }
                ]
        return self._action_pass(
            department,
            "No actionable proposal is present, so the rule-based controller skips the trade response.",
        )

    def _review_queue_item(self, card: Dict[str, Any], proposal_id: str) -> Dict[str, Any]:
        for item in card.get("review_queue") or []:
            if isinstance(item, dict) and item.get("proposal_id") == proposal_id:
                return item
        return {}

    def _procurement_proposal_affordable(
        self,
        card: Dict[str, Any],
        proposal_id: str,
        round_id: int,
    ) -> bool:
        trade_rules = self._trade_rules()
        if not trade_rules.get("reject_procurement_proposal_when_unaffordable", True):
            return True
        item = self._review_queue_item(card, proposal_id)
        if not item:
            return True
        quantity = self._num(item.get("quantity"), 0.0)
        unit_price = self._num(
            item.get("proposed_price"),
            self._num(item.get("price_reference"), 0.0),
        )
        if quantity <= 0 or unit_price <= 0:
            return True
        estimated_cost = quantity * unit_price
        required_buffer = self._num(
            trade_rules.get("procurement_accept_cash_buffer"),
            0.0,
        )
        return (self._available_cash(round_id) - estimated_cost) >= required_buffer

    def _department_state(self, department: str, round_id: int) -> Dict[str, Any]:
        return self._read_json(
            self.enterprise_workspace / "department" / department / f"day{round_id}" / f"{department}.json",
            {},
        ) or {}

    def _self_state(self, department: str, round_id: int) -> Dict[str, Any]:
        return (self._department_state(department, round_id).get("self_state") or {})

    def _cash_summary(self, round_id: int) -> Dict[str, Any]:
        finance_state = self._self_state("finance", round_id)
        if finance_state.get("cash_summary"):
            return finance_state.get("cash_summary") or {}
        for department in ("procurement", "production", "sales", "inventory"):
            candidate = self._self_state(department, round_id).get("cash_guard") or {}
            if candidate:
                return candidate
        return {}

    def _current_cash(self, round_id: int) -> float:
        summary = self._cash_summary(round_id)
        if isinstance(summary, dict) and "current_cash" in summary:
            return self._num(summary.get("current_cash"), 0.0)
        finance_state = self._self_state("finance", round_id)
        return self._num(
            finance_state.get("current_cash"),
            self._num(
                finance_state.get("cash"),
                self._num(finance_state.get("cash_balance"), 0.0),
            ),
        )

    def _available_cash(self, round_id: int) -> float:
        summary = self._cash_summary(round_id)
        current_cash = self._current_cash(round_id)
        return self._num(summary.get("available_after_warning_buffer"), current_cash)

    def _cash_is_tight(self, round_id: int) -> bool:
        rules = self._rules()
        cash = self._current_cash(round_id)
        if cash <= 0:
            return True
        return cash < self._num(rules.get("critical_cash_threshold"), 20000)

    def _cash_is_warning(self, round_id: int) -> bool:
        cash = self._current_cash(round_id)
        if cash <= 0:
            return True
        return cash < self._num(self._rules().get("cash_guard_threshold"), 60000)

    def _market_development_affordable(self, round_id: int) -> bool:
        rules = self._rules()
        cost = self._num(rules.get("market_development_cost"), 50000.0)
        buffer = self._num(rules.get("market_development_cash_buffer"), 0.0)
        if cost <= 0:
            return True
        return self._available_cash(round_id) >= cost + buffer

    def _confirmed_demand_quantity(self, round_id: int) -> float:
        sales_state = self._self_state("sales", round_id)
        sales_orders = sales_state.get("sales_orders") or {}
        order_based_total = 0.0
        for key in ("accepted", "in_progress", "backlog", "confirmed"):
            for order in sales_orders.get(key) or []:
                if isinstance(order, dict):
                    order_based_total += self._num(order.get("quantity"))
        backlog = sales_state.get("demand_backlog") or {}
        backlog_total = max(
            self._num(backlog.get("total_backlog_quantity")),
            self._num(backlog.get("backlog_quantity")),
        )
        by_product = backlog.get("by_product") or {}
        if isinstance(by_product, dict):
            backlog_total = max(
                backlog_total,
                sum(
                    self._num(value.get("quantity") if isinstance(value, dict) else value)
                    for value in by_product.values()
                ),
            )
        service_summary = sales_state.get("service_level_summary") or {}
        summary_total = max(
            self._num(service_summary.get("confirmed_order_backlog_quantity")),
            self._num(service_summary.get("stale_backlog_quantity")),
        )
        return max(order_based_total, backlog_total, summary_total)

    def _finished_goods_quantity(self, round_id: int) -> float:
        product_id = self._first_product_id(self._department_state("production", round_id)) or "beer"
        return self._inventory_quantity_by_item(round_id).get(product_id, 0.0)

    def _inventory_unit_cost(self, product_id: str, round_id: int) -> float:
        state = self._department_state("inventory", round_id)
        self_state = state.get("self_state") or {}
        for item in self_state.get("inventory_items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_id") or item.get("id") or "") == str(product_id):
                return max(0.0, self._num(item.get("unit_price"), 0.0))
        return 0.0

    def _order_is_fulfillable(
        self,
        order: Dict[str, Any],
        round_id: int,
        reserved_finished_goods: float = 0.0,
    ) -> bool:
        product_id = order.get("product_id") or self._first_product_id(
            self._department_state("production", round_id)
        ) or "beer"
        quantity = self._num(order.get("quantity"), 0.0)
        if quantity <= 0:
            return False

        inventory = self._inventory_quantity_by_item(round_id)
        finished_goods = max(0.0, inventory.get(product_id, 0.0) - reserved_finished_goods)
        if finished_goods >= quantity:
            return True

        try:
            deadline = int(order.get("delivery_deadline"))
        except (TypeError, ValueError):
            deadline = round_id + 1
        if deadline <= round_id or self._cash_is_tight(round_id):
            return False

        rules = self._rules()
        production_days = max(1, deadline - round_id)
        capacity_window = self._num(rules.get("production_daily_capacity"), 40.0) * production_days
        material_window = self._material_feasible_product_quantity(round_id)
        feasible_extra = min(capacity_window, material_window)
        total_coverage = max(
            0.0,
            inventory.get(product_id, 0.0) + feasible_extra - reserved_finished_goods,
        )
        return total_coverage >= quantity

    def _sales_action(self, round_id: int) -> List[Dict[str, Any]]:
        rules = self._rules()
        state = self._department_state("sales", round_id)
        self_state = state.get("self_state") or {}
        sales_orders = (self_state.get("sales_orders") or {})
        available_orders = sales_orders.get("available") or []
        actions: List[Dict[str, Any]] = []

        develop_interval = max(1, int(rules.get("market_development_interval") or 3))
        service_summary = self_state.get("service_level_summary") or {}
        sales_metrics = self_state.get("sales_metrics") or {}
        markets = [
            market for market in (self_state.get("markets") or [])
            if isinstance(market, dict)
        ]
        active_markets = [
            market for market in markets
            if str(market.get("status") or "").lower() in {"active", "completed", "ready"}
        ]
        developing_markets = [
            market for market in markets
            if str(market.get("status") or "").lower() in {
                "developing",
                "under_development",
                "in_progress",
                "pending",
            }
        ]
        confirmed_backlog = self._num(
            service_summary.get("confirmed_order_backlog_quantity"),
            self._num(sales_metrics.get("confirmed_order_backlog_quantity")),
        )
        overdue_backlog = self._num(
            service_summary.get("overdue_confirmed_order_quantity"),
            self._num(sales_metrics.get("overdue_confirmed_order_quantity")),
        )
        finished_goods = self._finished_goods_quantity(round_id)
        available_order_quantity = sum(
            self._num(order.get("quantity"), 0.0)
            for order in available_orders
            if isinstance(order, dict)
        )
        active_market_soft_cap = max(
            1,
            int(
                self._num(
                    rules.get("market_development_active_market_soft_cap"),
                    rules.get("max_market_order_sources_per_round", 4),
                )
            ),
        )
        low_order_threshold = max(
            0,
            int(self._num(rules.get("market_development_low_order_entry_threshold"), 1)),
        )
        reference_order_quantity = max(
            1.0,
            self._num(
                rules.get("market_development_reference_order_quantity"),
                self._num(rules.get("production_quantity"), 40.0),
            ),
        )
        inventory_order_multiple = max(
            1.0,
            self._num(rules.get("market_development_inventory_order_multiple"), 3.0),
        )
        surplus_inventory_multiplier = max(
            1.0,
            self._num(rules.get("market_development_surplus_inventory_multiplier"), 2.0),
        )
        low_order_entry = len(available_orders) <= low_order_threshold
        active_market_capacity_room = len(active_markets) < active_market_soft_cap
        inventory_can_accept_multiple_orders = (
            finished_goods >= reference_order_quantity * inventory_order_multiple
        )
        current_order_entry_quantity = max(
            available_order_quantity,
            reference_order_quantity if available_orders else 0.0,
        )
        market_orders_cannot_digest_inventory = (
            active_market_capacity_room
            and finished_goods
            >= max(
                reference_order_quantity * inventory_order_multiple,
                current_order_entry_quantity * surplus_inventory_multiplier,
            )
        )
        backlog_covered_for_growth = (
            finished_goods
            >= confirmed_backlog + reference_order_quantity * inventory_order_multiple
        )
        allow_when_backlog_covered = bool(
            rules.get("market_development_allow_when_backlog_covered", True)
        )
        fulfillment_pressure_blocked = (
            overdue_backlog > 0
            or (
                confirmed_backlog > 0
                and not (allow_when_backlog_covered and backlog_covered_for_growth)
            )
        )
        inventory_above_minimum = finished_goods >= self._num(
            rules.get("low_finished_goods_threshold"),
            40.0,
        )
        inventory_can_absorb_growth = (
            inventory_above_minimum
            and (
                inventory_can_accept_multiple_orders
                or market_orders_cannot_digest_inventory
            )
        )
        should_develop_market = (
            rules.get("market_development_when_no_available_orders", True)
            and (
                round_id % develop_interval == 0
                or market_orders_cannot_digest_inventory
            )
            and self._market_development_affordable(round_id)
            and not developing_markets
            and active_market_capacity_room
            and (low_order_entry or market_orders_cannot_digest_inventory)
            and not fulfillment_pressure_blocked
            and inventory_can_absorb_growth
        )
        if (
            should_develop_market
        ):
            actions.append({
                "action": {
                    "action_name": "develop_market",
                    "action_param": {
                        "market_type": "regional",
                        "assigned_workers": int(rules.get("market_development_workers") or 1),
                    },
                },
                "action_reason": (
                    "Finished-goods inventory can cover additional orders, but current available-order volume is insufficient, so the rule-based controller develops another market."
                    "Also, there is no hard-on market access for fulfillment, cash, manpower or construction in progress, and therefore the real demand portal is being expanded through minimum market development."
                ),
                "module_type": "SalesManager",
                "executor_id": self.enterprise_name,
            })

        if available_orders:
            accept_limit = max(0, int(rules.get("accept_orders_per_round", 99) or 0))
            accepted_count = 0
            reserved_by_product: Dict[str, float] = {}
            ranked_orders = sorted(
                [order for order in available_orders if isinstance(order, dict)],
                key=lambda order: (
                    -(
                        self._num(order.get("unit_price"), 0.0)
                        - self._inventory_unit_cost(
                            order.get("product_id")
                            or self._first_product_id(self._department_state("production", round_id))
                            or "beer",
                            round_id,
                        )
                    ),
                    -self._num(order.get("total_amount"), 0.0),
                    self._num(order.get("delivery_deadline"), round_id + 1),
                    str(order.get("order_id") or order.get("id") or ""),
                ),
            )
            for order in ranked_orders:
                if accepted_count >= accept_limit:
                    break
                order_id = order.get("order_id") or order.get("id")
                if not order_id:
                    continue
                product_id = order.get("product_id") or self._first_product_id(
                    self._department_state("production", round_id)
                ) or "beer"
                quantity = self._num(order.get("quantity"), 0.0)
                unit_price = self._num(order.get("unit_price"), 0.0)
                unit_cost = self._inventory_unit_cost(product_id, round_id)
                reserved = reserved_by_product.get(product_id, 0.0)
                economically_positive = unit_price > unit_cost
                jointly_fulfillable = self._order_is_fulfillable(
                    order,
                    round_id,
                    reserved_finished_goods=reserved,
                )
                if rules.get("reject_unfulfillable_orders", True) and (
                    not economically_positive or not jointly_fulfillable
                ):
                    actions.append({
                        "action": {
                            "action_name": "reject_order",
                            "action_param": {
                                "order_id": order_id,
                                "reason": "The order has insufficient marginal contribution or lacks cumulative inventory, capacity, material, cash, or delivery coverage, so the rule-based controller avoids a low-quality commitment.",
                            },
                        },
                        "action_reason": "The order economics or combined fulfillment plan is infeasible, so the rule-based controller rejects it instead of accepting it blindly.",
                        "module_type": "SalesManager",
                        "executor_id": self.enterprise_name,
                    })
                    continue
                actions.append({
                        "action": {
                            "action_name": "accept_order",
                            "action_param": {"order_id": order_id},
                        },
                        "action_reason": "The rule-based controller accepts real orders by unit contribution while respecting cumulative resource coverage.",
                        "module_type": "SalesManager",
                        "executor_id": self.enterprise_name,
                })
                accepted_count += 1
                reserved_by_product[product_id] = reserved + max(0.0, quantity)
        if actions:
            return actions
        return self._action_pass("sales", "No real available order exists, so the rule-based controller does not accept an order.")

    def _procurement_action(self, round_id: int) -> List[Dict[str, Any]]:
        rules = self._rules()
        if self._cash_is_tight(round_id):
            return self._action_pass("procurement", "Cash is below the threshold, so the rule-based controller defers additional procurement expenditure.")
        state = self._department_state("procurement", round_id)
        self_state = state.get("self_state") or {}
        suppliers = (
            self_state.get("supplier_candidates")
            or self_state.get("suppliers_detail")
            or self_state.get("supplier_data")
            or self._supplier_candidates_from_policy()
            or []
        )
        supplier_name = self._first_supplier_name(suppliers)
        use_b2b_exchange = self._has_upstream_b2b_exchange()
        if not supplier_name and not use_b2b_exchange:
            return self._action_pass("procurement", "No supplier is available, so the rule-based controller skips procurement.")

        material_orders = self._build_material_gap_orders(round_id, suppliers)
        if material_orders:
            material_orders = self._filter_procurement_orders(material_orders, round_id, suppliers)
            if not material_orders:
                return self._action_pass("procurement", "A material gap exists, but staffing or budget is insufficient, so the rule-based controller defers the purchase order.")
            if use_b2b_exchange:
                return [
                    {
                        "action": {
                            "action_name": "create_purchase_demand",
                            "action_param": {
                                "material_id": material_id,
                                "quantity": quantity,
                                "logistics_mode": self._select_procurement_logistics_mode(
                                    material_id,
                                    quantity,
                                    round_id,
                                ),
                            },
                        },
                        "action_reason": (
                            "The rule-based controller publishes procurement demand to the upstream exchange, "
                            "creating an observable B2B buy_request/proposal/order chain."
                        ),
                        "module_type": "ProcurementManager",
                        "executor_id": self.enterprise_name,
                    }
                    for material_id, quantity in material_orders
                ]
            actions = []
            for material_id, quantity in material_orders:
                option = self._select_supplier_purchase_option(
                    material_id,
                    quantity,
                    round_id,
                )
                selected_supplier = option.get("supplier_name") if option else supplier_name
                selected_quantity = option.get("quantity") if option else quantity
                logistics_mode = (
                    option.get("logistics_mode")
                    if option
                    else self._select_procurement_logistics_mode(
                        material_id,
                        quantity,
                        round_id,
                    )
                )
                actions.append({
                    "action": {
                        "action_name": "create_purchase_order",
                        "action_param": {
                            "material_id": material_id,
                            "quantity": selected_quantity,
                            "supplier_name": selected_supplier,
                            "logistics_mode": logistics_mode,
                        },
                    },
                    "action_reason": (
                        "The rule-based controller compares supplier unit price, processing time, minimum order quantity, reliability, and logistics lead time, "
                        "then selects a feasible combination that meets the earliest requirement at lower landed cost."
                    ),
                    "module_type": "ProcurementManager",
                    "executor_id": self.enterprise_name,
                })
            return actions
        return self._action_pass("procurement", "No material shortfall requires action, so procurement is deferred.")

    def _production_action(self, round_id: int) -> List[Dict[str, Any]]:
        rules = self._rules()
        if self._cash_is_tight(round_id):
            return self._action_pass("production", 'Cash is below the threshold, so the rule-based controller defers new production to control expenditure.')
        if self._production_staff_shortage(round_id):
            return self._action_pass("production", 'Production staffing is below the operating threshold, so output waits for HR replenishment.')
        production_policy = self._policy().get("production") or {}
        build_line_reason = self._production_build_line_reason(round_id)
        if (
            production_policy.get("allow_build_production_line", True)
            and build_line_reason
        ):
            return [
                {
                    "action": {
                        "action_name": "build_production_line",
                        "action_param": {
                            "line_type": rules.get("build_line_type", "small"),
                        },
                    },
                    "action_reason": build_line_reason,
                    "module_type": "ProductionManager",
                    "executor_id": self.enterprise_name,
                }
            ]

        state = self._department_state("production", round_id)
        product_id = self._first_product_id(state)
        quantity = self._scripted_production_quantity(round_id, production_policy)
        if self._cash_is_warning(round_id):
            quantity = min(quantity, self._num(rules.get("production_daily_capacity"), quantity))
        daily_capacity = self._num(
            rules.get("production_daily_capacity"),
            production_policy.get("daily_capacity", quantity),
        )
        total_capacity, available_capacity = self._production_capacity_status(round_id)
        fd_revised = self._fd_demand_response_enabled(round_id)
        if fd_revised:
            daily_capacity = min(quantity, available_capacity)
        elif self._fd_simple_rules(round_id):
            daily_capacity = min(quantity, self._fd_simple_rules(round_id)["daily_capacity"])
        if total_capacity > 0 and available_capacity <= 0:
            if self._production_line_build_pending(round_id):
                return self._action_pass("production", 'A production line is still under construction and no capacity is currently available, so scheduling waits for line completion.')
            return self._action_pass("production", 'No capacity is available this round, so production scheduling is skipped.')
        if available_capacity > 0:
            daily_capacity = min(daily_capacity, available_capacity)
        feasible_quantity = self._feasible_production_quantity(round_id, quantity)
        if quantity > 0 and feasible_quantity <= 0:
            return self._action_pass("production", 'Critical raw materials are insufficient, so production scheduling is skipped to avoid an infeasible plan.')
        if quantity > 0 and feasible_quantity < quantity:
            quantity = feasible_quantity
            daily_capacity = min(daily_capacity, feasible_quantity)
        if product_id and quantity > 0:
            action_reason = 'The rule-based controller sizes production from actual demand, finished-goods inventory, cash, materials, and available capacity.'
            if self._fd_simple_rules(round_id):
                action_reason = 'RB-Simple: with confirmed demand, inventory below its threshold and no open plan, schedule a fixed batch subject to execution constraints.'
            if self._policy().get("mode") == "cobweb":
                action_reason = (
                    'Cobweb C1 controller: calculate supply from the previous price and configured supply function, then create a plan within price, quantity and physical limits.'
                )
            return [
                {
                    "action": {
                        "action_name": "create_production_plan",
                        "action_param": {
                            "product_id": product_id,
                            "quantity": quantity,
                            "daily_capacity": min(quantity, daily_capacity),
                        },
                    },
                    "action_reason": action_reason,
                    "module_type": "ProductionManager",
                    "executor_id": self.enterprise_name,
                }
            ]
        return self._action_pass("production", 'Without a target product or number of rules, no production plans are created.')

    def _hr_action(self, round_id: int) -> List[Dict[str, Any]]:
        simple = self._fd_simple_rules(round_id)
        if simple:
            status = self._department_staff_status(round_id, "PRODUCTION")
            if (status["seen"] and status["count"] < 2 and status["pending"] == 0
                    and self._confirmed_demand_quantity(round_id) > 0):
                return [{
                    "action": {"action_name": "handle_recruitment", "action_param": {
                        "department": "PRODUCTION", "num_people": simple["recruit_people"],
                    }},
                    "action_reason": 'RB-Simple: confirmed demand, fewer than two production workers and no pending recruitment trigger fixed-batch hiring.',
                    "module_type": "HRManager", "executor_id": self.enterprise_name,
                }]
            return self._action_pass("hr", 'RB-Simple: no additional hiring because the production staffing threshold is not triggered or recruitment is already pending.')
        candidates = self._hr_recruitment_candidates(round_id)
        if candidates:
            actions = []
            for candidate in candidates:
                department_code = candidate["department"]
                status = candidate.get("status") or {}
                actions.append(
                    {
                        "action": {
                            "action_name": "handle_recruitment",
                            "action_param": {
                                "department": department_code,
                                "num_people": int(candidate["num_people"]),
                            },
                        },
                        "action_reason": (
                            f'Scripted policy: current department workload exceeds available staffing; recruit for {department_code}. Current headcount {status.get('count', 0)}, available {status.get('available', 0)}, pending recruitment {status.get('pending', 0)}。'
                        ),
                        "module_type": "HRManager",
                        "executor_id": self.enterprise_name,
                    }
                )
            return actions
        return self._action_pass("hr", 'department Real-time personnel, pending recruitment and work pressure do not indicate new needs for replacements.')

    def _inventory_action(self, round_id: int) -> List[Dict[str, Any]]:
        if self._cash_is_tight(round_id):
            return self._action_pass("inventory", "Cash is below the threshold, so the rule-based controller does not expand warehouse capacity.")
        if self._warehouse_pressure(round_id):
            rules = self._rules()
            return [
                {
                    "action": {
                        "action_name": "expand_warehouse",
                        "action_param": {
                            "size": int(rules.get("warehouse_expansion_capacity") or 1000),
                        },
                    },
                    "action_reason": "Warehouse utilization exceeds the threshold, triggering a small capacity expansion.",
                    "module_type": "InventoryManager",
                    "executor_id": self.enterprise_name,
                }
            ]
        return self._action_pass("inventory", "Warehouse utilization is below the threshold, so no expansion is required.")

    def _scripted_production_quantity(self, round_id: int, production_policy: Dict[str, Any]) -> float:
        if self._fd_demand_response_enabled(round_id):
            return self._fd_revised_production_quantity(round_id)
        simple = self._fd_simple_rules(round_id)
        if simple:
            plans = self._self_state("production", round_id).get("production_plans") or {}
            rows = [row for bucket in plans.values() if isinstance(bucket, list) for row in bucket] if isinstance(plans, dict) else plans
            if any(row.get("status") in {"created", "pending", "in_progress", "active"}
                   for row in rows if isinstance(row, dict)):
                return 0
            if (self._confirmed_demand_quantity(round_id) <= 0
                    or self._finished_goods_quantity(round_id) >= simple["finished_goods_threshold"]):
                return 0
            return min(simple["production_batch"], max(0, self._production_capacity_status(round_id)[1]))
        mode = self._policy().get("mode", "scripted_rules")
        base = self._num(production_policy.get("base_quantity"), 80)
        if mode == "cobweb":
            return self._scripted_cobweb_quantity(round_id, base)
        if mode == "commons":
            growth = self._num(production_policy.get("growth_rate"), 0.08)
            return max(1, round(base * ((1 + growth) ** round_id), 2))
        if mode == "herding":
            return self._scripted_herding_quantity(round_id, production_policy, base)
        if mode == "single_case":
            rules = self._rules()
            interval = max(1, int(rules.get("production_round_interval") or 1))
            if round_id % interval != 0:
                return 0
            demand_gap = max(
                0.0,
                self._confirmed_demand_quantity(round_id)
                + self._num(rules.get("target_finished_goods_buffer"), 90)
                - self._finished_goods_quantity(round_id),
            )
            configured_quantity = self._num(
                rules.get("production_quantity"),
                production_policy.get("single_case_quantity", base),
            )
            quantity = min(max(demand_gap, 0.0), configured_quantity) if demand_gap > 0 else 0
            return max(0, quantity)
        return max(1, base)

    def _fd_demand_response_enabled(self, round_id: int) -> bool:
        from config.fd_experiment import FD2_RB_RULE_VERSION
        policy = policy_from_meta(self._run_meta(), round_id)
        return (policy.get("condition") == "rb_revised"
                or (policy.get("scenario") == "FD2" and policy.get("condition") == "rb_reference"
                    and policy.get("rule_policy_version") == FD2_RB_RULE_VERSION))

    def _fd_simple_rules(self, round_id: int) -> Dict[str, Any]:
        policy = policy_from_meta(self._run_meta(), round_id)
        if policy.get("scenario") != "FD4" or policy.get("condition") != "rb_simple":
            return {}
        return self._policy().get("fd_simple_rules") or {
            "production_batch": 250, "daily_capacity": 250,
            "finished_goods_threshold": 250, "recruit_people": 2,
        }

    def _fd_revised_production_quantity(self, round_id: int) -> float:
        state = self._self_state("production", round_id)
        product_id = self._first_product_id(self._department_state("production", round_id))
        candidate = next((row for row in (state.get("recovery_guard") or {}).get("candidates", [])
                          if row.get("product_id") == product_id), {})
        if candidate:
            demand = self._num(candidate.get("confirmed_order_backlog_quantity"))
            stock = self._num(candidate.get("on_hand"))
            pending = self._num(candidate.get("active_plan_quantity"))
        else:
            demand = self._confirmed_demand_quantity(round_id)
            stock = self._finished_goods_quantity(round_id)
            plans = state.get("production_plans") or {}
            rows = [row for bucket in plans.values() if isinstance(bucket, list) for row in bucket] if isinstance(plans, dict) else plans
            pending = sum(max(0, self._num(row.get("quantity")) - self._num(
                (row.get("progress") or {}).get("quantity_produced"))) for row in rows
                if row.get("product_id") == product_id and row.get("status") == "in_progress")
        gap = max(0, demand - stock - pending)
        _, capacity = self._production_capacity_status(round_id)
        staffing = self._department_staff_status(round_id, "PRODUCTION")
        staffing_limit = max(0, staffing["available"]) * ProductionConfig.PLAN_QUANTITY_PER_WORKER
        quantity = min(gap, max(0, capacity), staffing_limit)
        quantity = self._feasible_production_quantity(round_id, quantity)
        recipes = state.get("product_recipes") or {}
        recipe = recipes.get(product_id, {}) if isinstance(recipes, dict) else next(
            (row for row in recipes if row.get("product_id") == product_id), {}
        )
        conversion = self._num(recipe.get("labor_cost_per_unit")) + self._num(recipe.get("equipment_cost_per_unit"))
        lines = state.get("production_lines") or {}
        line_rows = lines.get("details", []) if isinstance(lines, dict) else lines
        line_configs = ProductionConfig().LINE_CONFIGS
        operating = sum(self._num(row.get("operating_cost"),
                        (line_configs.get(row.get("line_type")) or {}).get("operating_cost", 0))
                        for row in line_rows if self._num(row.get("remaining_capacity")) > 0)
        if conversion > 0:
            quantity = min(quantity, max(0, self._available_cash(round_id) - operating) / conversion)
        elif self._available_cash(round_id) < operating:
            quantity = 0
        return max(0, round(quantity, 2))

    def _scripted_cobweb_quantity(self, round_id: int, fallback: float) -> float:
        state = self._department_state("production", round_id)
        self_state = state.get("self_state") or {}
        signal = (
            self_state.get("cobweb_decision_signal")
            or state.get("cobweb_decision_signal")
            or {}
        )
        config = self._simulation_config().get("cobweb_config") or {}
        decision_price = signal.get("current_market_price")
        if decision_price is None:
            lag = max(1, int(self._num(config.get("production_lag_rounds"), 1)))
            previous_day = round_id - lag
            if previous_day >= 0:
                previous_state = self._department_state("production", previous_day)
                previous_self_state = previous_state.get("self_state") or {}
                previous_signal = (
                    previous_self_state.get("cobweb_decision_signal")
                    or previous_state.get("cobweb_decision_signal")
                    or {}
                )
                decision_price = previous_signal.get("current_market_price")
        if decision_price is None:
            decision_price = signal.get("lagged_price", config.get("initial_price"))

        if decision_price is None:
            return max(0.0, round(fallback, 2))

        raw_quantity = (
            self._num(config.get("supply_intercept"), 0.0)
            + self._num(config.get("supply_slope"), 0.0) * self._num(decision_price)
        )
        quantity_floor = self._num(config.get("quantity_floor"), 1.0)
        quantity_ceiling = self._num(config.get("quantity_ceiling"), max(quantity_floor, raw_quantity))
        quantity = min(max(raw_quantity, quantity_floor), max(quantity_floor, quantity_ceiling))
        return round(quantity, 2)

    def _scripted_herding_quantity(
        self,
        round_id: int,
        production_policy: Dict[str, Any],
        base: float,
    ) -> float:
        config = self._simulation_config().get("herding_config") or {}
        peer_visible = bool(config.get("peer_visibility_enabled", True))
        if peer_visible:
            sensitivity = self._num(production_policy.get("peer_signal_sensitivity"), 0.2)
            multiplier = self._num(production_policy.get("herding_peer_visibility_multiplier"), 1.0)
            return max(1, round(base * multiplier * (1 + sensitivity * min(round_id, 6)), 2))

        true_demand = self._series_value(config.get("true_demand_series"), round_id, base)
        market_heat = self._series_value(config.get("market_heat_series"), round_id, 0.0)
        target_ids = config.get("target_enterprise_ids") or []
        enterprise_count = max(1, len(target_ids))
        demand_share = true_demand / enterprise_count if production_policy.get("herding_no_peer_use_demand_share", True) else base
        multipliers = production_policy.get("herding_no_peer_enterprise_multipliers") or {}
        enterprise_multiplier = self._num(multipliers.get(self.enterprise_name), 1.0)
        heat_sensitivity = self._num(production_policy.get("herding_no_peer_heat_sensitivity"), 0.25)
        phase_offset = sum(ord(char) for char in self.enterprise_name) % 4
        phase_wave = [0.75, 1.20, 0.60, 1.35][(round_id + phase_offset) % 4]
        quantity = demand_share * enterprise_multiplier * phase_wave * (1 + heat_sensitivity * market_heat)
        return max(1, round(quantity, 2))

    @staticmethod
    def _series_value(series: Any, round_id: int, default: float = 0.0) -> float:
        if not isinstance(series, list) or not series:
            return default
        index = min(max(0, int(round_id or 0)), len(series) - 1)
        try:
            return float(series[index] or 0.0)
        except (TypeError, ValueError):
            return default

    def _inventory_quantity_by_item(self, round_id: int) -> Dict[str, float]:
        state = self._department_state("inventory", round_id)
        self_state = state.get("self_state") or {}
        quantities: Dict[str, float] = {}
        for item in self_state.get("inventory_items") or []:
            item_id = item.get("item_id") or item.get("id")
            if item_id:
                quantities[item_id] = quantities.get(item_id, 0.0) + self._num(item.get("quantity"))
        return quantities

    def _inventory_position_by_material(self, round_id: int) -> Dict[str, float]:
        procurement_state = self._self_state("procurement", round_id)
        operational_summary = procurement_state.get("operational_summary") or {}
        positions = operational_summary.get("inventory_position_by_material") or {}
        result = {}
        for material_id, data in positions.items():
            if isinstance(data, dict):
                result[material_id] = self._num(
                    data.get("inventory_position"),
                    self._num(data.get("on_hand"), 0.0),
                )
        if result:
            return result
        return self._inventory_quantity_by_item(round_id)

    def _incoming_procurement_quantity_by_material(self, round_id: int) -> Dict[str, float]:
        procurement_state = self._self_state("procurement", round_id)
        result: Dict[str, float] = {}

        def add_max(material_id: Any, quantity: Any) -> None:
            material_key = str(material_id or "").strip()
            if not material_key:
                return
            result[material_key] = max(result.get(material_key, 0.0), self._num(quantity))

        operational_summary = procurement_state.get("operational_summary") or {}
        positions = operational_summary.get("inventory_position_by_material") or {}
        if isinstance(positions, dict):
            for material_id, data in positions.items():
                if isinstance(data, dict):
                    add_max(material_id, data.get("incoming"))

        for block_key in ("replenishment", "operational_summary"):
            block = procurement_state.get(block_key) or {}
            pending = block.get("pending_by_material") if isinstance(block, dict) else {}
            if isinstance(pending, dict):
                for material_id, quantity in pending.items():
                    add_max(material_id, quantity)

        order_totals: Dict[str, float] = {}

        def visit_orders(value: Any) -> None:
            if isinstance(value, dict):
                material_id = value.get("material_id")
                if material_id:
                    status = str(value.get("status") or "").lower()
                    if status in {"pending", "ordered", "in_transit", "confirmed", "created"}:
                        material_key = str(material_id).strip()
                        order_totals[material_key] = order_totals.get(material_key, 0.0) + self._num(value.get("quantity"))
                for nested in value.values():
                    visit_orders(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit_orders(nested)

        visit_orders(procurement_state.get("orders") or {})
        visit_orders(procurement_state.get("purchase_orders") or {})
        for material_id, quantity in order_totals.items():
            add_max(material_id, quantity)
        return result

    def _recent_procurement_action_round(self, material_id: str, round_id: int) -> Optional[int]:
        procurement_dir = self.enterprise_workspace / "department" / "procurement"
        if not procurement_dir.exists():
            return None
        target_material = str(material_id or "").strip()
        if not target_material:
            return None
        latest_round: Optional[int] = None
        for day_dir in procurement_dir.glob("day*"):
            try:
                day = int(str(day_dir.name).replace("day", ""))
            except ValueError:
                continue
            if day >= round_id:
                continue
            for action_path in day_dir.glob("procurement_action*.json"):
                payload = self._read_json(action_path, None)
                for action in self._iter_action_payloads(payload):
                    action_name = action.get("action_name")
                    action_param = action.get("action_param") or {}
                    if action_name not in {"create_purchase_order", "create_purchase_demand"}:
                        continue
                    if str(action_param.get("material_id") or "").strip() == target_material:
                        latest_round = day if latest_round is None else max(latest_round, day)
        return latest_round

    @classmethod
    def _iter_action_payloads(cls, payload: Any) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            for item in payload:
                actions.extend(cls._iter_action_payloads(item))
            return actions
        if not isinstance(payload, dict):
            return actions
        if isinstance(payload.get("action"), dict):
            actions.extend(cls._iter_action_payloads(payload.get("action")))
        action_name = payload.get("action_name")
        if action_name:
            actions.append(payload)
        for key in ("actions", "workflow", "department_actions"):
            nested = payload.get(key)
            if nested is not None:
                actions.extend(cls._iter_action_payloads(nested))
        return actions

    def _production_recipe_materials(self, round_id: int) -> Dict[str, float]:
        state = self._department_state("production", round_id)
        self_state = state.get("self_state") or {}
        recipes = self_state.get("product_recipes") or {}
        if isinstance(recipes, list) and recipes:
            first_recipe = next((recipe for recipe in recipes if isinstance(recipe, dict)), {})
            return {
                material_id: self._num(quantity)
                for material_id, quantity in (first_recipe.get("raw_materials") or {}).items()
            }
        if isinstance(recipes, dict) and recipes:
            first_recipe = next(iter(recipes.values()))
            if isinstance(first_recipe, dict):
                return {
                    material_id: self._num(quantity)
                    for material_id, quantity in (first_recipe.get("raw_materials") or {}).items()
                }
        return {}

    def _feasible_production_quantity(self, round_id: int, requested_quantity: float) -> float:
        requested_quantity = self._num(requested_quantity)
        if requested_quantity <= 0:
            return 0.0
        recipe = self._production_recipe_materials(round_id)
        if not recipe:
            return requested_quantity
        inventory = self._inventory_quantity_by_item(round_id)
        feasible = requested_quantity
        for material_id, per_unit in recipe.items():
            per_unit = self._num(per_unit)
            if per_unit <= 0:
                continue
            feasible = min(feasible, inventory.get(material_id, 0.0) / per_unit)
        return max(0.0, round(feasible, 2))

    def _material_feasible_product_quantity(self, round_id: int) -> float:
        recipe = self._production_recipe_materials(round_id)
        if not recipe:
            return 0.0
        inventory = self._inventory_quantity_by_item(round_id)
        feasible = None
        for material_id, per_unit in recipe.items():
            per_unit = self._num(per_unit)
            if per_unit <= 0:
                continue
            material_feasible = inventory.get(material_id, 0.0) / per_unit
            feasible = material_feasible if feasible is None else min(feasible, material_feasible)
        return max(0.0, round(feasible or 0.0, 2))

    @staticmethod
    def _staffing_alias_key(value: Any) -> str:
        return str(value or "").strip().lower()

    def _staffing_aliases(self, department_code: str) -> set:
        return {
            self._staffing_alias_key(alias)
            for alias in STAFFING_DEPARTMENT_ALIASES.get(department_code, {department_code})
        }

    def _department_staff_status(self, round_id: int, department_code: str) -> Dict[str, Any]:
        aliases = self._staffing_aliases(department_code)
        hr_state = self._self_state("hr", round_id)
        status = {
            "count": 0,
            "allocated": 0,
            "available": 0,
            "pending": 0,
            "seen": False,
        }

        def apply_entry(entry: Dict[str, Any]) -> None:
            if not isinstance(entry, dict):
                return
            status["seen"] = True
            count = int(self._num(entry.get("count"), status["count"]))
            allocated = int(self._num(entry.get("allocated"), status["allocated"]))
            physical_available = max(0, count - allocated)
            available = int(self._num(entry.get("available"), physical_available))
            status["count"] = max(status["count"], count)
            status["allocated"] = max(status["allocated"], allocated)
            status["available"] = max(status["available"], available)
            status["pending"] = max(
                status["pending"],
                int(self._num(entry.get("pending_recruits"), status["pending"])),
            )

        department_staffing = hr_state.get("department_staffing") or {}
        if isinstance(department_staffing, dict):
            for key, entry in department_staffing.items():
                if self._staffing_alias_key(key) in aliases:
                    apply_entry(entry)

        for employee in hr_state.get("employees") or []:
            if not isinstance(employee, dict):
                continue
            if self._staffing_alias_key(employee.get("department")) in aliases:
                apply_entry(employee)

        pending = (hr_state.get("recruitment_status") or {}).get("pending_by_department") or {}
        if isinstance(pending, dict):
            for key, value in pending.items():
                if self._staffing_alias_key(key) in aliases:
                    status["pending"] = max(status["pending"], int(self._num(value, 0)))

        state_key = STAFFING_DEPARTMENT_TO_STATE_KEY.get(department_code)
        if state_key:
            module_state = self._self_state(state_key, round_id)
            staff_summary = module_state.get("staff_summary") or module_state.get("labor_summary") or {}
            if isinstance(staff_summary, dict) and staff_summary:
                count = int(self._num(
                    staff_summary.get("total_workers"),
                    self._num(staff_summary.get("workers"), status["count"]),
                ))
                available = int(self._num(
                    staff_summary.get("available_workers"),
                    self._num(staff_summary.get(f"{state_key}_workers"), status["available"]),
                ))
                status["seen"] = True
                status["count"] = max(status["count"], count)
                status["available"] = max(status["available"], available)

        return status

    def _department_minimum_workers(self, department_code: str) -> int:
        rules = self._rules()
        rule_keys = {
            "PRODUCTION": ("production_staff_minimum", 2),
            "PROCUREMENT": ("procurement_staff_minimum", 1),
            "SALES": ("sales_staff_minimum", 1),
            "INVENTORY": ("inventory_staff_minimum", 1),
        }
        key, default = rule_keys.get(department_code, ("staff_minimum", 0))
        return max(0, int(self._num(rules.get(key), default)))

    def _department_has_work_pressure(self, round_id: int, department_code: str) -> bool:
        if department_code == "PROCUREMENT":
            procurement_state = self._self_state("procurement", round_id)
            suppliers = procurement_state.get("suppliers") or procurement_state.get("supplier_data") or []
            return bool(self._build_material_gap_orders(round_id, suppliers))
        if department_code == "PRODUCTION":
            return (
                self._confirmed_demand_quantity(round_id) > 0
                or self._finished_goods_quantity(round_id) < self._num(
                    self._rules().get("target_finished_goods_buffer"),
                    90.0,
                )
            )
        if department_code == "SALES":
            sales_state = self._self_state("sales", round_id)
            sales_orders = sales_state.get("sales_orders") or {}
            return any(
                bool(sales_orders.get(key))
                for key in ("available", "accepted", "in_progress", "backlog")
            )
        if department_code == "INVENTORY":
            return self._warehouse_pressure(round_id)
        return False

    def _department_needs_recruitment(self, round_id: int, department_code: str) -> bool:
        minimum = self._department_minimum_workers(department_code)
        if minimum <= 0:
            return False
        status = self._department_staff_status(round_id, department_code)
        if not status.get("seen"):
            return False
        if status["pending"] > 0:
            return False
        hard_shortage = status["count"] < minimum or status["available"] <= 0
        soft_shortage = status["available"] < minimum
        if hard_shortage:
            return True
        return soft_shortage and self._department_has_work_pressure(round_id, department_code)

    def _hr_recruitment_candidates(self, round_id: int) -> List[Dict[str, Any]]:
        rules = self._rules()
        default_people = max(1, min(10, int(self._num(rules.get("hr_recruit_people"), 2))))
        candidates: List[Dict[str, Any]] = []
        for department_code in STAFFING_RECRUITMENT_PRIORITY:
            if not self._department_needs_recruitment(round_id, department_code):
                continue
            status = self._department_staff_status(round_id, department_code)
            minimum = self._department_minimum_workers(department_code)
            shortage = max(
                1,
                minimum - int(status.get("count", 0)),
                minimum - int(status.get("available", 0)),
            )
            candidates.append({
                "department": department_code,
                "num_people": max(default_people, shortage),
                "status": status,
                "minimum": minimum,
            })
        return candidates

    def _production_staff_shortage(self, round_id: int) -> bool:
        minimum = self._department_minimum_workers("PRODUCTION")
        if minimum <= 0:
            return False
        status = self._department_staff_status(round_id, "PRODUCTION")
        if not status.get("seen"):
            return False
        return status["count"] < minimum or status["available"] <= 0

    def _capacity_pressure(self, round_id: int) -> bool:
        if self._cash_is_tight(round_id):
            return False
        rules = self._rules()
        demand = self._confirmed_demand_quantity(round_id)
        if demand <= 0:
            return False
        total_capacity, available_capacity = self._production_capacity_status(round_id)
        if total_capacity <= 0:
            return True
        pressure = demand / max(available_capacity, 1.0)
        return pressure >= self._num(rules.get("capacity_pressure_threshold"), 0.85)

    def _production_line_count(self, round_id: int) -> float:
        production_state = self._self_state("production", round_id)
        production_lines = production_state.get("production_lines") or {}
        if isinstance(production_lines, dict):
            explicit_total = production_lines.get("total")
            if explicit_total is not None:
                return self._num(explicit_total, 0.0)
            by_status = production_lines.get("by_status") or {}
            if isinstance(by_status, dict) and by_status:
                return sum(self._num(value, 0.0) for value in by_status.values())
            details = production_lines.get("details") or []
            if isinstance(details, list):
                return float(len([line for line in details if isinstance(line, dict)]))
            return 0.0
        if isinstance(production_lines, list):
            return float(len([line for line in production_lines if isinstance(line, dict)]))
        return 0.0

    def _single_case_line_capacity_already_recovered(self, round_id: int) -> bool:
        if self._policy().get("mode") != "single_case":
            return False
        rules = self._rules()
        max_lines = int(self._num(rules.get("single_case_max_production_lines"), 2))
        if max_lines <= 0:
            return False
        return self._production_line_count(round_id) >= max_lines

    def _production_build_line_reason(
        self,
        round_id: int,
    ) -> str:
        if self._cash_is_tight(round_id):
            return ""
        if self._production_line_build_pending(round_id):
            return ""
        single_case_mode = self._policy().get("mode") == "single_case"
        if single_case_mode and self._single_case_line_capacity_already_recovered(round_id):
            return ""

        total_capacity, available_capacity = self._production_capacity_status(round_id)
        demand = self._confirmed_demand_quantity(round_id)
        if total_capacity <= 0:
            if demand > 0 or self._single_enterprise_stockout_capacity_gap(round_id):
                return "No base production line is available while real demand or a stock-buffer gap exists, so the rule-based controller builds the minimum feasible line."
            return ""

        if single_case_mode:
            if demand <= 0:
                return ""
            rules = self._rules()
            pressure = demand / max(total_capacity, 1.0)
            threshold = self._num(rules.get("capacity_pressure_threshold"), 0.85)
            if pressure >= threshold:
                return (
                    "Real demand is pressuring total capacity and recovery remains incomplete, so the rule-based controller adds capacity."
                    "Priority is given to supplementing primary production line capacity."
                )
            return ""

        if self._capacity_pressure(round_id):
            return "Real demand and existing capacity pressure justify prioritizing additional production-line capacity."
        return ""

    def _single_enterprise_stockout_capacity_gap(self, round_id: int) -> bool:
        if self._policy().get("mode") != "single_case":
            return False
        total_capacity, available_capacity = self._production_capacity_status(round_id)
        if available_capacity > 0:
            return False
        if self._production_line_build_pending(round_id):
            return False
        if total_capacity > 0 and self._single_case_line_capacity_already_recovered(round_id):
            return False
        rules = self._rules()
        finished_goods = self._finished_goods_quantity(round_id)
        demand = self._confirmed_demand_quantity(round_id)
        low_finished_goods_threshold = self._num(
            rules.get("low_finished_goods_threshold"),
            40.0,
        )
        target_buffer = self._num(rules.get("target_finished_goods_buffer"), 90.0)
        stock_is_low = finished_goods < low_finished_goods_threshold
        demand_uncovered = demand > 0 and finished_goods < demand
        buffer_uncovered = target_buffer > 0 and finished_goods < min(
            target_buffer,
            max(low_finished_goods_threshold, 1.0),
        )
        if not (stock_is_low or demand_uncovered or buffer_uncovered):
            return False
        product_id = self._first_product_id(self._department_state("production", round_id))
        if not product_id:
            return False
        return total_capacity <= 0

    def _production_line_build_pending(self, round_id: int) -> bool:
        production_state = self._self_state("production", round_id)
        production_lines = production_state.get("production_lines") or {}
        pending_statuses = {
            "under_construction",
            "building",
            "build_production_line",
            "construction",
            "pending",
            "planned",
        }
        if isinstance(production_lines, dict):
            by_status = production_lines.get("by_status") or {}
            for status in pending_statuses:
                if self._num(by_status.get(status), 0.0) > 0:
                    return True
            details = production_lines.get("details") or []
        elif isinstance(production_lines, list):
            details = production_lines
        else:
            details = []
        for line in details if isinstance(details, list) else []:
            if not isinstance(line, dict):
                continue
            status = str(
                line.get("status")
                or line.get("lifecycle_status")
                or line.get("state")
                or ""
            ).lower()
            if status in pending_statuses:
                return True
        return False

    def _available_production_daily_capacity(self, round_id: int) -> float:
        return self._production_capacity_status(round_id)[1]

    def _production_capacity_status(self, round_id: int) -> tuple:
        production_state = self._self_state("production", round_id)
        production_lines = production_state.get("production_lines") or {}
        if isinstance(production_lines, dict):
            total_capacity = self._num(production_lines.get("total_capacity"), 0.0)
            available_raw = production_lines.get("available_capacity")
            if available_raw is not None:
                available_capacity = self._num(available_raw, total_capacity)
            else:
                by_status = production_lines.get("by_status") or {}
                if isinstance(by_status, dict) and by_status:
                    ready_count = sum(
                        self._num(by_status.get(status), 0.0)
                        for status in ("idle", "ready", "available")
                    )
                    total_count = self._num(
                        production_lines.get("total"),
                        sum(self._num(value, 0.0) for value in by_status.values()),
                    )
                    if ready_count <= 0:
                        available_capacity = 0.0
                    elif total_capacity > 0 and total_count > 0:
                        available_capacity = total_capacity * min(1.0, ready_count / total_count)
                    else:
                        available_capacity = total_capacity
                else:
                    available_capacity = total_capacity
            return total_capacity, available_capacity
        if not isinstance(production_lines, list):
            return 0.0, 0.0
        ready_statuses = {"idle", "ready", "available"}
        ready_lines = [
            line for line in production_lines
            if isinstance(line, dict) and str(line.get("status") or "").lower() in ready_statuses
        ]
        available_capacity = sum(self._num(line.get("daily_capacity"), 0.0) for line in ready_lines)
        total_capacity = sum(
            self._num(line.get("daily_capacity"), self._num(line.get("capacity"), 0.0))
            for line in production_lines
            if isinstance(line, dict)
        )
        return total_capacity, available_capacity

    def _warehouse_pressure(self, round_id: int) -> bool:
        rules = self._rules()
        inventory_state = self._self_state("inventory", round_id)
        used = self._num(inventory_state.get("used_capacity"), 0.0)
        capacity = self._num(inventory_state.get("warehouse_capacity"), 0.0)
        if capacity <= 0:
            return False
        return (used / capacity) >= self._num(rules.get("warehouse_pressure_threshold"), 0.88)

    def _supplier_min_order_by_material(self, suppliers: Any, round_id: Optional[int] = None) -> Dict[str, float]:
        if isinstance(suppliers, dict):
            suppliers = list(suppliers.values())
        result: Dict[str, float] = {}
        for supplier in suppliers if isinstance(suppliers, list) else []:
            if not isinstance(supplier, dict):
                continue
            materials = supplier.get("materials") or {}
            if isinstance(materials, dict):
                for material_id, data in materials.items():
                    if isinstance(data, dict):
                        result[material_id] = max(
                            result.get(material_id, 0.0),
                            self._num(data.get("min_order_quantity"), 0.0),
                        )
        if round_id is not None:
            procurement_state = self._self_state("procurement", round_id)
            matrix = procurement_state.get("materials_suppliers_matrix") or {}
            if isinstance(matrix, dict):
                for material_id, supplier_rows in matrix.items():
                    if not isinstance(supplier_rows, list):
                        continue
                    for row in supplier_rows:
                        if not isinstance(row, dict):
                            continue
                        result[material_id] = max(
                            result.get(material_id, 0.0),
                            self._num(row.get("min_order_quantity"), 0.0),
                        )
        return result

    def _procurement_available_workers(self, round_id: int) -> int:
        status = self._department_staff_status(round_id, "PROCUREMENT")
        if status.get("seen"):
            return max(0, int(status.get("available", 0)))
        hr_state = self._self_state("hr", round_id)
        employees = hr_state.get("employees") or []
        for employee in employees:
            if str(employee.get("department") or "").upper() == "PROCUREMENT":
                count = int(self._num(employee.get("count"), 0))
                allocated = int(self._num(employee.get("allocated"), 0))
                return max(0, count - allocated)
        procurement_state = self._self_state("procurement", round_id)
        staff_summary = procurement_state.get("staff_summary") or procurement_state.get("labor_summary") or {}
        available = self._num(staff_summary.get("available_workers"), 0.0)
        return max(0, int(available))

    def _supplier_price_by_material(self, round_id: int) -> Dict[str, float]:
        procurement_state = self._self_state("procurement", round_id)
        matrix = procurement_state.get("materials_suppliers_matrix") or {}
        result = {}
        if isinstance(matrix, dict):
            for material_id, suppliers in matrix.items():
                prices = [
                    self._num(supplier.get("unit_price"), 0.0)
                    for supplier in (suppliers or [])
                    if isinstance(supplier, dict) and self._num(supplier.get("unit_price"), 0.0) > 0
                ]
                if prices:
                    result[material_id] = min(prices)
        for supplier in (
            procurement_state.get("supplier_candidates")
            or self._supplier_candidates_from_policy()
            or []
        ):
            if not isinstance(supplier, dict):
                continue
            materials = supplier.get("materials") or {}
            if not isinstance(materials, dict):
                continue
            for material_id, material_info in materials.items():
                if not isinstance(material_info, dict):
                    continue
                unit_price = self._num(material_info.get("unit_price"), 0.0)
                if unit_price > 0:
                    result[str(material_id)] = min(
                        result.get(str(material_id), unit_price),
                        unit_price,
                    )
        return result

    def _top_tier_supply_guard(self, round_id: int) -> Dict[str, Any]:
        procurement_state = self._self_state("procurement", round_id)
        guard = procurement_state.get("top_tier_supply_guard") or {}
        return guard if isinstance(guard, dict) else {}

    def _single_case_top_tier_order_budget(self, round_id: int) -> Optional[float]:
        if self._policy().get("mode") != "single_case":
            return None
        guard = self._top_tier_supply_guard(round_id)
        if not guard.get("enabled"):
            return None
        limits = []
        max_single = self._num(guard.get("max_single_order_amount"), 0.0)
        available_credit = self._num(guard.get("available_credit"), 0.0)
        if max_single > 0:
            limits.append(max_single)
        if available_credit > 0:
            limits.append(available_credit)
        return min(limits) if limits else None

    def _select_procurement_logistics_mode(
        self,
        material_id: str,
        quantity: float,
        round_id: int,
    ) -> str:
        rules = self._rules()
        configured = str(rules.get("preferred_logistics_mode") or "dynamic").lower()
        if configured in LOGISTICS_COST_CONFIGS:
            return configured

        recipe = self._production_recipe_materials(round_id)
        per_unit = self._num(recipe.get(material_id), 0.0)
        demand_quantity = max(
            self._confirmed_demand_quantity(round_id),
            self._num(rules.get("production_quantity"), 0.0),
            self._num(rules.get("material_coverage_target_quantity"), 0.0),
        )
        required_material = per_unit * demand_quantity if per_unit > 0 else self._num(quantity)
        current_position = self._inventory_position_by_material(round_id).get(material_id, 0.0)
        shortage_ratio = 1.0
        if required_material > 0:
            shortage_ratio = max(0.0, (required_material - current_position) / required_material)

        if current_position <= 0 or shortage_ratio >= 0.5:
            return "air"
        if shortage_ratio > 0:
            return "rail"
        return "road"

    def _procurement_logistics_cost(
        self,
        quantity: float,
        round_id: int,
        logistics_mode: Optional[str] = None,
        material_id: Optional[str] = None,
    ) -> float:
        rules = self._rules()
        if logistics_mode is None:
            logistics_mode = self._select_procurement_logistics_mode(
                material_id or "",
                quantity,
                round_id,
            )
        logistics_mode = str(logistics_mode or rules.get("preferred_logistics_mode") or "road")
        config = LOGISTICS_COST_CONFIGS.get(logistics_mode, LOGISTICS_COST_CONFIGS["road"])
        raw_cost = self._num(config.get("base_fee"), 0.0) + self._num(config.get("unit_fee"), 0.0) * self._num(quantity)
        guard = self._top_tier_supply_guard(round_id)
        multiplier = self._num(guard.get("external_logistics_cost_multiplier"), 1.0)
        return max(0.0, raw_cost * multiplier)

    def _estimate_procurement_order_cost(
        self,
        material_id: str,
        quantity: float,
        round_id: int,
        logistics_mode: Optional[str] = None,
    ) -> float:
        unit_price = self._supplier_price_by_material(round_id).get(material_id, 1.0)
        material_cost = self._num(quantity) * unit_price
        if self._single_case_top_tier_order_budget(round_id) is not None:
            return max(
                0.0,
                material_cost + self._procurement_logistics_cost(
                    quantity,
                    round_id,
                    logistics_mode=logistics_mode,
                    material_id=material_id,
                ),
            )
        return max(0.0, material_cost)

    def _affordable_procurement_quantity(
        self,
        material_id: str,
        budget: float,
        round_id: int,
        logistics_mode: Optional[str] = None,
    ) -> float:
        unit_price = self._supplier_price_by_material(round_id).get(material_id, 1.0)
        if self._single_case_top_tier_order_budget(round_id) is not None:
            rules = self._rules()
            if logistics_mode is None:
                logistics_mode = self._select_procurement_logistics_mode(
                    material_id,
                    budget,
                    round_id,
                )
            logistics_mode = str(logistics_mode or rules.get("preferred_logistics_mode") or "road")
            config = LOGISTICS_COST_CONFIGS.get(logistics_mode, LOGISTICS_COST_CONFIGS["road"])
            guard = self._top_tier_supply_guard(round_id)
            multiplier = self._num(guard.get("external_logistics_cost_multiplier"), 1.0)
            fixed_cost = self._num(config.get("base_fee"), 0.0) * multiplier
            variable_cost = unit_price + self._num(config.get("unit_fee"), 0.0) * multiplier
            if variable_cost <= 0:
                return 0.0
            return max(0.0, (self._num(budget) - fixed_cost) / variable_cost)
        return max(0.0, self._num(budget) / max(unit_price, 0.0001))

    def _filter_procurement_orders(
        self,
        material_orders: List[tuple],
        round_id: int,
        suppliers: Any = None,
    ) -> List[tuple]:
        rules = self._rules()
        max_orders = max(1, int(rules.get("max_procurement_orders_per_round") or 1))
        available_workers = self._procurement_available_workers(round_id)
        if available_workers <= 0:
            return []
        max_orders = min(max_orders, available_workers)
        min_orders = self._supplier_min_order_by_material(suppliers, round_id)
        budget = max(
            self._num(rules.get("procurement_min_budget"), 5000.0),
            self._current_cash(round_id) * self._num(rules.get("procurement_budget_cash_share"), 0.35),
        )
        top_tier_budget = self._single_case_top_tier_order_budget(round_id)
        if top_tier_budget is not None:
            budget = min(budget, top_tier_budget)
        selected = []
        spent = 0.0
        def _normalized_order_cost(item: tuple) -> float:
            material_id, quantity = item
            min_order = min_orders.get(material_id, 0.0)
            if min_order > 0 and quantity < min_order:
                quantity = min_order
            logistics_mode = self._select_procurement_logistics_mode(
                material_id,
                quantity,
                round_id,
            )
            return self._estimate_procurement_order_cost(
                material_id,
                quantity,
                round_id,
                logistics_mode=logistics_mode,
            )

        for material_id, quantity in sorted(
            material_orders,
            key=_normalized_order_cost,
        ):
            if len(selected) >= max_orders:
                break
            min_order = min_orders.get(material_id, 0.0)
            if min_order > 0 and quantity < min_order:
                quantity = min_order
            logistics_mode = self._select_procurement_logistics_mode(
                material_id,
                quantity,
                round_id,
            )
            estimated_cost = self._estimate_procurement_order_cost(
                material_id,
                quantity,
                round_id,
                logistics_mode=logistics_mode,
            )
            if estimated_cost <= 0:
                continue
            if spent + estimated_cost > budget and selected:
                continue
            if estimated_cost > budget:
                affordable_quantity = self._affordable_procurement_quantity(
                    material_id,
                    budget,
                    round_id,
                    logistics_mode=logistics_mode,
                )
                quantity = min(quantity, round(affordable_quantity, 2))
                if min_order > 0 and quantity < min_order:
                    continue
                estimated_cost = self._estimate_procurement_order_cost(
                    material_id,
                    quantity,
                    round_id,
                    logistics_mode=logistics_mode,
                )
            if quantity <= 0 or spent + estimated_cost > budget:
                continue
            selected.append((material_id, round(quantity, 2)))
            spent += estimated_cost
        return selected

    def _build_material_gap_orders(self, round_id: int, suppliers: Any) -> List[tuple]:
        rules = self._rules()
        demand_quantity = self._confirmed_demand_quantity(round_id)
        target_quantity = max(
            self._num(rules.get("material_coverage_target_quantity"), 0.0),
            demand_quantity,
        )
        if target_quantity <= 0:
            return []
        min_orders = self._supplier_min_order_by_material(suppliers, round_id)
        recipe = self._production_recipe_materials(round_id)
        procurement_state = self._self_state("procurement", round_id)
        position_snapshot = (
            (procurement_state.get("operational_summary") or {})
            .get("inventory_position_by_material")
            or {}
        )
        inventory = self._inventory_position_by_material(round_id)
        incoming = self._incoming_procurement_quantity_by_material(round_id)
        multiplier = self._num(rules.get("material_order_multiplier"), 1.15)
        min_gap_ratio = max(0.0, self._num(rules.get("procurement_min_gap_ratio"), 0.0))
        emergency_gap_ratio = max(0.0, self._num(rules.get("procurement_emergency_gap_ratio"), 1.0))
        cooldown_rounds = max(0, int(self._num(rules.get("procurement_reorder_cooldown_rounds"), 0)))

        def should_skip_material(material_id: str, required: float, current: float, gap: float) -> bool:
            gap_ratio = gap / required if required > 0 else 1.0
            if min_gap_ratio > 0 and gap_ratio < min_gap_ratio:
                return True
            last_purchase_round = self._recent_procurement_action_round(material_id, round_id)
            return (
                last_purchase_round is not None
                and cooldown_rounds > 0
                and round_id - last_purchase_round <= cooldown_rounds
                and gap_ratio < emergency_gap_ratio
            )

        if not recipe:
            material_id = self._first_material_id(self._department_state("procurement", round_id))
            if not material_id:
                return []
            current = inventory.get(material_id, 0.0)
            if material_id not in position_snapshot:
                current += incoming.get(material_id, 0.0)
            gap = max(0.0, target_quantity - current)
            if gap <= 0 or should_skip_material(material_id, target_quantity, current, gap):
                return []
            return [(material_id, round(max(gap * multiplier, min_orders.get(material_id, 0.0)), 2))]

        orders = []
        for material_id, per_unit in recipe.items():
            required = per_unit * target_quantity
            current = inventory.get(material_id, 0.0)
            if material_id not in position_snapshot:
                # Some legacy snapshots expose on-hand only. Treat separately
                # reported in-transit procurement as coverage to avoid repeated
                # scripted purchases while earlier orders are still arriving.
                current += incoming.get(material_id, 0.0)
            gap = max(0.0, required - current)
            if gap <= 0:
                continue
            if should_skip_material(material_id, required, current, gap):
                continue
            quantity = max(gap * multiplier, min_orders.get(material_id, 0.0))
            orders.append((material_id, round(quantity, 2)))
        return orders

    def _first_product_id(self, state: Dict[str, Any]) -> Optional[str]:
        self_state = state.get("self_state") or {}
        recipes = self_state.get("product_recipes") or {}
        if isinstance(recipes, list) and recipes:
            for recipe in recipes:
                if isinstance(recipe, dict) and recipe.get("product_id"):
                    return recipe.get("product_id")
        if isinstance(recipes, dict) and recipes:
            return next(iter(recipes.keys()))
        context = self_state.get("production_context") or {}
        for key in ("product_id", "target_product_id", "beer_game_product_id"):
            if context.get(key):
                return context[key]
        run_meta = self._run_meta()
        return run_meta.get("beer_game_product_id") or "beer"

    def _first_material_id(self, state: Dict[str, Any]) -> Optional[str]:
        self_state = state.get("self_state") or {}
        for key in ("purchasable_materials_idList", "purchasable_materials", "materials"):
            values = self_state.get(key)
            if isinstance(values, list) and values:
                first = values[0]
                return first.get("material_id") if isinstance(first, dict) else first
        inventory_position = (
            ((state.get("blackboard") or {}).get("departments") or {})
            .get("procurement", {})
            .get("inventory_position_by_material", {})
        )
        if isinstance(inventory_position, dict) and inventory_position:
            return next(iter(inventory_position.keys()))
        return None

    @staticmethod
    def _first_supplier_name(suppliers: Any) -> Optional[str]:
        if isinstance(suppliers, dict):
            suppliers = list(suppliers.values())
        if not isinstance(suppliers, list) or not suppliers:
            return None
        first = suppliers[0]
        if isinstance(first, dict):
            return first.get("supplier_name") or first.get("name") or first.get("supplier_id")
        return str(first)

    def _earliest_sales_delivery_deadline(self, round_id: int) -> Optional[int]:
        state = self._self_state("sales", round_id)
        deadlines = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                status = str(value.get("status") or "").lower()
                deadline = value.get("delivery_deadline")
                if status in {"accepted", "in_progress"} and deadline is not None:
                    try:
                        deadlines.append(int(deadline))
                    except (TypeError, ValueError):
                        pass
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(state.get("orders") or state.get("sales_orders") or {})
        return min(deadlines) if deadlines else None

    def _select_supplier_purchase_option(
        self,
        material_id: str,
        quantity: float,
        round_id: int,
    ) -> Optional[Dict[str, Any]]:
        procurement_state = self._self_state("procurement", round_id)
        matrix = procurement_state.get("materials_suppliers_matrix") or {}
        rows = matrix.get(material_id) if isinstance(matrix, dict) else []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = []
        if not rows:
            candidate_rows = []
            for supplier in (
                procurement_state.get("supplier_candidates")
                or self._supplier_candidates_from_policy()
                or []
            ):
                if not isinstance(supplier, dict):
                    continue
                materials = supplier.get("materials") or {}
                material_info = (
                    materials.get(material_id)
                    if isinstance(materials, dict)
                    else None
                )
                if not isinstance(material_info, dict):
                    continue
                candidate_rows.append({
                    "supplier_name": supplier.get("supplier_name"),
                    "supplier_type": supplier.get("supplier_type"),
                    "unit_price": material_info.get("unit_price", 0),
                    "available_quantity": material_info.get("quantity", 0),
                    "min_order_quantity": material_info.get("min_order_quantity", 0),
                    "quality_level": supplier.get("quality_level"),
                    "reliability_score": supplier.get("reliability_score"),
                    "processing_time": supplier.get("processing_time", 0),
                    "registration_status": supplier.get("registration_status", "candidate"),
                    "logistics_options": supplier.get("logistics_options") or {},
                })
            rows = candidate_rows
        earliest_deadline = self._earliest_sales_delivery_deadline(round_id)
        options = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            supplier_name = row.get("supplier_name") or row.get("name")
            if not supplier_name:
                continue
            unit_price = self._num(row.get("unit_price"), 0.0)
            processing_time = max(0, int(self._num(row.get("processing_time"), 0.0)))
            reliability = self._num(row.get("reliability_score"), 0.0)
            min_order_quantity = max(0.0, self._num(row.get("min_order_quantity"), 0.0))
            normalized_quantity = max(self._num(quantity), min_order_quantity)
            logistics_options = row.get("logistics_options") or {}
            for logistics_mode, logistics_config in LOGISTICS_COST_CONFIGS.items():
                supplied_option = logistics_options.get(logistics_mode) or {}
                transit_time = int(
                    self._num(
                        supplied_option.get("transit_time"),
                        logistics_config.get("transit_time", 0),
                    )
                )
                arrival_round = round_id + processing_time + transit_time
                landed_cost = (
                    normalized_quantity * unit_price
                    + self._num(logistics_config.get("base_fee"), 0.0)
                    + normalized_quantity * self._num(logistics_config.get("unit_fee"), 0.0)
                )
                deadline_feasible = (
                    earliest_deadline is None or arrival_round <= earliest_deadline
                )
                options.append({
                    "supplier_name": supplier_name,
                    "quantity": normalized_quantity,
                    "logistics_mode": logistics_mode,
                    "arrival_round": arrival_round,
                    "landed_cost": landed_cost,
                    "reliability_score": reliability,
                    "deadline_feasible": deadline_feasible,
                })
        if not options:
            return None
        feasible = [item for item in options if item["deadline_feasible"]]
        has_feasible_options = bool(feasible)
        candidate_options = feasible or options
        candidate_options.sort(
            key=lambda item: (
                item["landed_cost"] if has_feasible_options else item["arrival_round"],
                item["arrival_round"] if has_feasible_options else item["landed_cost"],
                -item["reliability_score"],
                item["supplier_name"],
            )
        )
        return candidate_options[0]
