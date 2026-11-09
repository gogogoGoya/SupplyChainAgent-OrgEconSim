"""Small facade for API/CLI style operations calls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional

from config.integration_profiles import (
    get_scenario_integration_profiles,
    resolve_integration_profiles,
)
from config.simulation_preset_config import get_scenario_config
from config.simulation_preset_config import normalize_market_demand_mode
from config.fd_experiment import resolve_fd_experiment
from config.firm_architecture_experiment import validate_scenario, policy_from_config

from .service import OperationsService


class OperationsFacade:
    """Convenience facade over OperationsService for external callers."""

    def __init__(
        self,
        repository: Any,
        *,
        scenario_loader: Callable[[str], Dict[str, Any]] = None,
        profile_loader: Callable[[str], Dict[str, Any]] = None,
    ):
        self.service = OperationsService(repository)
        self.scenario_loader = scenario_loader or get_scenario_config
        self.profile_loader = profile_loader or get_scenario_integration_profiles

    def create_experiment_from_scenario(
        self,
        *,
        scenario_id: str,
        planned_total_steps: Optional[int] = None,
        tags: Optional[List[str]] = None,
        requested_by: Optional[str] = None,
        job_id: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        scenario_config = _deep_merge(
            self.scenario_loader(scenario_id),
            config_overrides or {},
        )
        scenario_config = _ensure_external_order_product_alignment(scenario_config)
        if not policy_from_config(scenario_config):
            scenario_config = resolve_fd_experiment(scenario_config, scenario_id, planned_total_steps)
        validate_scenario(scenario_config, planned_total_steps)
        architecture = policy_from_config(scenario_config)
        if architecture:
            tags = list(dict.fromkeys([*(tags or []), f"firm-architecture:{architecture['architecture']}",
                                       f"firm-protocol:{architecture['version']}",
                                       f"firm-baseline:{architecture.get('baseline_group', 'legacy')}"]))
            if architecture.get("arm"):
                tags.append("fd-arm:" + architecture["arm"])
            if (architecture.get("baseline") or {}).get("source_run_id"):
                tags.append("firm-baseline-run:" + architecture["baseline"]["source_run_id"])
        fd_policy = ((scenario_config.get("runtime_injection") or {}).get(
            "single_enterprise_case_policy") or {}).get("fd_experiment") or {}
        if fd_policy.get("version"):
            tags = list(dict.fromkeys([*(tags or []), f"fd-version:{fd_policy['version']}",
                                       f"fd-condition:{fd_policy['condition']}"]))
            if fd_policy.get("instance_sequence"):
                tags.append(f"fd-instance:{fd_policy['instance_sequence']:02d}")
        _validate_cobweb_experiment_contract(scenario_config)
        _validate_long_horizon_evolution_contract(
            scenario_config,
            planned_total_steps=planned_total_steps,
        )
        integration_profiles = (
            resolve_integration_profiles(scenario_config)
            if config_overrides
            else self.profile_loader(scenario_id)
        )
        return self.service.create_experiment(
            scenario_id=scenario_id,
            scenario_config=scenario_config,
            integration_profiles=integration_profiles,
            planned_total_steps=planned_total_steps,
            tags=tags,
            requested_by=requested_by,
            job_id=job_id,
            config_overrides=config_overrides,
        )

    def request_stop(
        self,
        job_id: str,
        *,
        reason: str,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.service.request_stop(
            job_id,
            reason=reason,
            requested_by=requested_by,
        )

    def start_experiment(
        self,
        job_id: str,
        *,
        run_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        artifact_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.service.start_experiment(
            job_id,
            run_id=run_id,
            worker_id=worker_id,
            artifact_root=artifact_root,
        )

    def resume_experiment(
        self,
        job_id: str,
        *,
        target_total_steps: Optional[int] = None,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.service.resume_experiment(
            job_id,
            target_total_steps=target_total_steps,
            requested_by=requested_by,
        )

    def import_resumable_experiment(
        self,
        *,
        artifact_root: str,
        run_meta: Dict[str, Any],
        last_complete_round: int,
        target_total_steps: int = 200,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.service.import_resumable_experiment(
            artifact_root=artifact_root,
            run_meta=run_meta,
            last_complete_round=last_complete_round,
            target_total_steps=target_total_steps,
            requested_by=requested_by,
        )

    def record_checkpoint(
        self,
        job_id: str,
        *,
        completed_steps: int,
        last_complete_round: Optional[int],
        checkpoint_uri: Optional[str] = None,
        artifact_root: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.service.record_checkpoint(
            job_id,
            completed_steps=completed_steps,
            last_complete_round=last_complete_round,
            checkpoint_uri=checkpoint_uri,
            artifact_root=artifact_root,
            details=details,
        )

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self.service.get_job(job_id)

    def delete_job(self, job_id: str, *, force: bool = False) -> Dict[str, Any]:
        return self.service.delete_job(job_id, force=force)

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        active_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.service.recover_jobs(
            status=status,
            active_only=active_only,
            limit=limit,
        )

    def list_active_jobs(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.service.recover_jobs(active_only=True, limit=limit)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _external_order_targets(scenario_config: Dict[str, Any]) -> List[str]:
    runtime_injection = (scenario_config or {}).get("runtime_injection") or {}
    policy = runtime_injection.get("external_market_order_policy") or {}
    if policy.get("enabled") is False or policy.get("mode") in {"disabled", "none"}:
        return []
    targets = (
        policy.get("target_enterprise_ids")
        or runtime_injection.get("external_market_order_enterprise_ids")
        or []
    )
    if isinstance(targets, str):
        targets = [targets]
    if not targets and runtime_injection.get("external_market_order_enterprise_id"):
        targets = [runtime_injection["external_market_order_enterprise_id"]]
    return [str(target) for target in targets if target]


def _market_product_pointer(scenario_config: Dict[str, Any]) -> Optional[tuple]:
    simulation = (scenario_config or {}).get("simulation") or {}
    mode = normalize_market_demand_mode(simulation.get("market_demand_mode"))
    if mode == "scheduled_external_demand":
        return ("simulation", "beer_game_product_id", simulation.get("beer_game_product_id") or "beer")
    if mode == "shared_resource_market":
        config = simulation.get("shared_resource_config") or {}
        return ("shared_resource_config", "product_id", config.get("product_id"))
    if mode == "herding_market":
        config = simulation.get("herding_config") or {}
        return ("herding_config", "product_id", config.get("product_id"))
    if mode == "cobweb":
        config = simulation.get("cobweb_config") or {}
        return ("cobweb_config", "product_id", config.get("product_id"))
    return None


def _target_salable_products(scenario_config: Dict[str, Any], target: str) -> List[str]:
    enterprises = (
        (scenario_config or {}).get("enterprise_specs")
        or (scenario_config or {}).get("enterprise_configs")
        or []
    )
    for enterprise in enterprises:
        enterprise_id = enterprise.get("id") or enterprise.get("enterprise_id")
        if str(enterprise_id) != str(target):
            continue
        products = enterprise.get("salable_products_idList") or []
        if isinstance(products, str):
            products = [products]
        return [str(product_id) for product_id in products if product_id]
    return []


def _write_market_product_id(
    scenario_config: Dict[str, Any],
    pointer: tuple,
    product_id: str,
) -> None:
    simulation = scenario_config.setdefault("simulation", {})
    container_key, field_key, _ = pointer
    if container_key == "simulation":
        simulation[field_key] = product_id
        return
    simulation.setdefault(container_key, {})[field_key] = product_id


def _ensure_external_order_product_alignment(
    scenario_config: Dict[str, Any],
) -> Dict[str, Any]:
    materialized = deepcopy(scenario_config or {})
    targets = _external_order_targets(materialized)
    if not targets:
        return materialized
    pointer = _market_product_pointer(materialized)
    if pointer is None:
        return materialized

    product_id = pointer[2]
    if not product_id:
        for target in targets:
            salable_products = _target_salable_products(materialized, target)
            if salable_products:
                product_id = salable_products[0]
                _write_market_product_id(materialized, pointer, product_id)
                break
    if not product_id:
        raise ValueError("External order product_id is required when external order injection is enabled")

    mismatches = []
    for target in targets:
        salable_products = _target_salable_products(materialized, target)
        if salable_products and product_id not in salable_products:
            mismatches.append(f"{target}: {product_id} not in {salable_products}")
    if mismatches:
        raise ValueError(
            "External order product does not match target enterprise salable products: "
            + "; ".join(mismatches)
        )
    return materialized


def _validate_cobweb_experiment_contract(
    scenario_config: Dict[str, Any],
) -> None:
    """Reject mixed C1/C2/C3 controller settings before an operations job is frozen."""
    formal_config = (scenario_config or {}).get("formal_experiment_config") or {}
    if (
        not formal_config.get("ready_for_formal_run")
        or formal_config.get("experiment_family") != "cobweb"
    ):
        return

    simulation = (scenario_config or {}).get("simulation") or {}
    runtime_injection = (scenario_config or {}).get("runtime_injection") or {}
    experiment_design = (scenario_config or {}).get("experiment_design") or {}
    cobweb_config = simulation.get("cobweb_config") or {}
    scripted_policy = runtime_injection.get("scripted_rule_policy") or {}
    profit_policy = runtime_injection.get("profit_objective_policy") or {}
    guidance_policy = runtime_injection.get("cobweb_enterprise_guidance_policy") or {}
    long_run_policy = runtime_injection.get("long_run_experiment_policy") or {}
    integration_profiles = (scenario_config or {}).get("integration_profiles") or {}
    analysis_profile = (
        (integration_profiles.get("capabilities") or {}).get("analysis") or {}
    )
    group = str(experiment_design.get("experiment_group") or "").upper()

    errors = []
    if normalize_market_demand_mode(simulation.get("market_demand_mode")) != "cobweb":
        errors.append("market_demand_mode must be cobweb")
    if cobweb_config.get("product_id") != "beer":
        errors.append("cobweb product_id must be beer")
    if cobweb_config.get("endogenous_supply_source") != "production_plan_created":
        errors.append("endogenous_supply_source must be production_plan_created")
    if int(cobweb_config.get("endogenous_supply_lag_rounds") or 0) != 1:
        errors.append("endogenous_supply_lag_rounds must be 1")
    try:
        agent_history_window_rounds = int(
            cobweb_config.get("agent_history_window_rounds", 8) or 8
        )
    except (TypeError, ValueError):
        agent_history_window_rounds = 0
    if not 3 <= agent_history_window_rounds <= 12:
        errors.append("agent_history_window_rounds must be between 3 and 12")

    if group == "C1":
        if scripted_policy.get("enabled") is not True:
            errors.append("C1 requires scripted_rule_policy.enabled=true")
        if cobweb_config.get("production_response_mode") != "scripted_formula":
            errors.append("C1 requires production_response_mode=scripted_formula")
    elif group == "C2":
        if scripted_policy.get("enabled") is not False:
            errors.append("C2 requires scripted_rule_policy.enabled=false")
        if profit_policy.get("enabled") is not False:
            errors.append("C2 requires profit_objective_policy.enabled=false")
        if guidance_policy.get("enabled") is not True:
            errors.append("C2 requires cobweb_enterprise_guidance_policy.enabled=true")
        if cobweb_config.get("production_response_mode") != "agent_endogenous":
            errors.append("C2 requires production_response_mode=agent_endogenous")
        if cobweb_config.get("decision_profile") != "mechanism_primary":
            errors.append("C2 requires decision_profile=mechanism_primary")
    elif group == "C3":
        if scripted_policy.get("enabled") is not False:
            errors.append("C3 requires scripted_rule_policy.enabled=false")
        if profit_policy.get("enabled") is not True:
            errors.append("C3 requires profit_objective_policy.enabled=true")
        if guidance_policy.get("enabled") is not False:
            errors.append("C3 requires cobweb_enterprise_guidance_policy.enabled=false")
        if cobweb_config.get("production_response_mode") != "agent_endogenous":
            errors.append("C3 requires production_response_mode=agent_endogenous")
        if cobweb_config.get("decision_profile") != "profit_balanced":
            errors.append("C3 requires decision_profile=profit_balanced")
        if long_run_policy.get("enabled") is not True:
            errors.append("C3 requires long_run_experiment_policy.enabled=true")
        try:
            planning_horizon_rounds = int(
                profit_policy.get("planning_horizon_rounds") or 0
            )
        except (TypeError, ValueError):
            planning_horizon_rounds = 0
        if planning_horizon_rounds < 8:
            errors.append(
                "C3 requires profit_objective_policy.planning_horizon_rounds>=8"
            )
        candidate_ranking = profit_policy.get("candidate_ranking_contract") or {}
        if candidate_ranking.get("enabled") is not True:
            errors.append(
                "C3 requires profit_objective_policy.candidate_ranking_contract.enabled=true"
            )
        expected_objectives = {
            "net_profit",
            "cash_safety",
            "service_level",
            "production_adjustment_stability",
            "price_risk_buffer",
        }
        objective_weights = profit_policy.get("objective_weights") or {}
        if set(objective_weights) != expected_objectives:
            errors.append(
                "C3 cobweb objective_weights must contain only "
                + ", ".join(sorted(expected_objectives))
            )
        else:
            try:
                normalized_weights = {
                    key: float(objective_weights[key])
                    for key in expected_objectives
                }
            except (TypeError, ValueError):
                normalized_weights = {}
            if not normalized_weights or any(
                value <= 0 for value in normalized_weights.values()
            ):
                errors.append("C3 cobweb objective_weights must all be positive")
            elif abs(sum(normalized_weights.values()) - 1.0) > 1e-6:
                errors.append("C3 cobweb objective_weights must sum to 1")
        if runtime_injection.get("inventory_cost_daily_settlement_enabled") is not False:
            errors.append(
                "C3 cobweb requires inventory_cost_daily_settlement_enabled=false"
            )
        if analysis_profile.get("profile") != "historical_diagnosis":
            errors.append(
                "C3 cobweb requires analysis profile=historical_diagnosis"
            )
        try:
            analysis_history_days = int(
                analysis_profile.get("history_days") or 0
            )
        except (TypeError, ValueError):
            analysis_history_days = 0
        if analysis_history_days < 8:
            errors.append("C3 cobweb requires analysis history_days>=8")

    if errors:
        raise ValueError(
            f"Invalid formal cobweb {group or 'experiment'} configuration: "
            + "; ".join(errors)
        )


def _validate_long_horizon_evolution_contract(
    scenario_config: Dict[str, Any],
    *,
    planned_total_steps: Optional[int] = None,
) -> None:
    """Keep the expensive E1 launch aligned with its preregistered 200-turn design."""
    meta = (scenario_config or {}).get("meta") or {}
    experiment_design = (scenario_config or {}).get("experiment_design") or {}
    if not (
        str(meta.get("scenario_id") or "") == "long_horizon_evolution"
        or str(experiment_design.get("experiment_group") or "").upper() == "E1"
    ):
        return

    simulation = (scenario_config or {}).get("simulation") or {}
    runtime = (scenario_config or {}).get("runtime_injection") or {}
    external_policy = runtime.get("external_environment_policy") or {}
    long_run_policy = runtime.get("long_run_experiment_policy") or {}
    timeout_policy = long_run_policy.get("agent_timeout_policy") or {}
    fallback_policy = long_run_policy.get("state_driven_fallback_policy") or {}
    timeout_breaker = long_run_policy.get("agent_timeout_circuit_breaker") or {}
    execution_guard = long_run_policy.get("execution_guard") or {}
    retention_policy = long_run_policy.get("artifact_retention_policy") or {}
    profitability_policy = runtime.get("long_horizon_profitability_policy") or {}
    cost_policy = runtime.get("long_horizon_cost_policy") or {}
    scripted_policy = runtime.get("scripted_rule_policy") or {}
    enterprise_ids = [
        str(item.get("enterprise_id") or item.get("id") or "")
        for item in ((scenario_config or {}).get("enterprise_specs") or [])
    ]
    event_turns = [
        int(event.get("turn", -1))
        for event in (external_policy.get("events") or [])
        if isinstance(event, dict)
    ]
    errors = []
    if int(simulation.get("agent_run_steps") or 0) != 200:
        errors.append("simulation.agent_run_steps must be 200")
    if int(simulation.get("service_total_steps") or 0) != 200:
        errors.append("simulation.service_total_steps must be 200")
    if planned_total_steps is not None and int(planned_total_steps) != 200:
        errors.append("planned_total_steps must be 200")
    if enterprise_ids != ["Supplier", "Manufacturer", "Distributor", "Retailer"]:
        errors.append("enterprise chain must be Supplier->Manufacturer->Distributor->Retailer")
    if external_policy.get("enabled") is not True:
        errors.append("external_environment_policy.enabled must be true")
    if event_turns != [40, 80, 120, 160]:
        errors.append("external event turns must be 40,80,120,160")
    if long_run_policy.get("enabled") is not True:
        errors.append("long_run_experiment_policy.enabled must be true")
    if long_run_policy.get("continue_on_agent_failure") is not True:
        errors.append("long_run_experiment_policy.continue_on_agent_failure must be true")
    if (
        (long_run_policy.get("agent_context_compaction") or {}).get("enabled")
        is not True
    ):
        errors.append(
            "long_run_experiment_policy.agent_context_compaction.enabled must be true"
        )
    if timeout_policy.get("enabled") is not True:
        errors.append("long_run_experiment_policy.agent_timeout_policy.enabled must be true")
    if fallback_policy.get("enabled") is not True:
        errors.append(
            "long_run_experiment_policy.state_driven_fallback_policy.enabled must be true"
        )
    if timeout_breaker.get("enabled") is not True:
        errors.append(
            "long_run_experiment_policy.agent_timeout_circuit_breaker.enabled must be true"
        )
    if int(timeout_breaker.get("consecutive_all_timeout_rounds") or 0) < 2:
        errors.append(
            "long_run_experiment_policy.agent_timeout_circuit_breaker."
            "consecutive_all_timeout_rounds must be at least 2"
        )
    if execution_guard.get("cap_manual_purchase_demand") is not True:
        errors.append(
            "long_run_experiment_policy.execution_guard.cap_manual_purchase_demand must be true"
        )
    if retention_policy.get("retain_end_of_day_state") is not True:
        errors.append(
            "long_run_experiment_policy.artifact_retention_policy.retain_end_of_day_state must be true"
        )
    if retention_policy.get("retain_raw_observations") is not True:
        errors.append(
            "long_run_experiment_policy.artifact_retention_policy.retain_raw_observations must be true"
        )
    if profitability_policy.get("enabled") is not True:
        errors.append("long_horizon_profitability_policy.enabled must be true")
    required_buyer_roles = {
        "finished_goods_manufacturer",
        "intermediate_distributor",
        "retail_market_node",
    }
    configured_buyer_roles = set(
        (profitability_policy.get("b2b_max_price_floor_by_role_tag") or {}).keys()
    )
    if not required_buyer_roles.issubset(configured_buyer_roles):
        errors.append(
            "long_horizon_profitability_policy.b2b_max_price_floor_by_role_tag "
            "must cover manufacturer, distributor, and retailer"
        )
    if cost_policy.get("enabled") is not True:
        errors.append("long_horizon_cost_policy.enabled must be true")
    for multiplier_name in (
        "salary_cost_multiplier",
        "inventory_maintenance_cost_multiplier",
        "warehouse_operating_cost_multiplier",
        "warehouse_expansion_cost_multiplier",
    ):
        try:
            multiplier = float(cost_policy.get(multiplier_name))
        except (TypeError, ValueError):
            multiplier = 0.0
        if not 0.0 < multiplier <= 1.0:
            errors.append(
                f"long_horizon_cost_policy.{multiplier_name} must be within (0, 1]"
            )
    if scripted_policy.get("enabled") is not False:
        errors.append("scripted_rule_policy.enabled must be false")
    for policy_name in (
        "bullwhip_midstream_pass_through_mode",
        "bullwhip_proposal_conversion_acceleration",
        "bullwhip_manufacturer_upstream_amplification",
        "bullwhip_supplier_upstream_pull_through_mode",
    ):
        if (runtime.get(policy_name) or {}).get("enabled") is not False:
            errors.append(f"{policy_name}.enabled must be false")
    if errors:
        raise ValueError(
            "Invalid E1 long-horizon evolution config: " + "; ".join(errors)
        )
