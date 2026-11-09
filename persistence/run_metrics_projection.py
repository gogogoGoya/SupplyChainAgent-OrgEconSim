"""Run metrics projection for SQL mirror and operations dashboards."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from persistence.experiment_metric_contract import build_experiment_metric_contract


RUN_METRICS_PROJECTION_SCHEMA_VERSION = "run_metrics_projection.v1"


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def _extract_action_quantity(action_name: str, params: Dict[str, Any]) -> float:
    fields_by_action = {
        "create_purchase_order": ("quantity",),
        "create_purchase_demand": ("demand_quantity", "quantity"),
        "create_production_plan": ("production_quantity", "quantity"),
        "create_replenishment_order": ("quantity", "demand_quantity"),
        "adjust_sales_demand": ("quantity", "demand_quantity"),
    }
    for field in fields_by_action.get(action_name, ("quantity",)):
        value = _safe_number(params.get(field))
        if value is not None:
            return value
    return 0.0


def _extract_round_action_response_metrics(
    workspace_dir: Path,
    enterprise_ids: List[str],
    round_id: int,
) -> Dict[str, Any]:
    by_enterprise: Dict[str, Any] = {}
    total_action_counts: Dict[str, int] = {}
    total_action_quantities: Dict[str, float] = {}
    total_decision_sources: Dict[str, int] = {}
    for enterprise_id in enterprise_ids:
        day_dirs = list(
            (
                workspace_dir
                / "enterprises"
                / enterprise_id
                / "department"
            ).glob(f"*/day{round_id}")
        )
        action_counts: Dict[str, int] = {}
        action_quantities: Dict[str, float] = {}
        decision_sources: Dict[str, int] = {}
        for day_dir in day_dirs:
            for action_path in day_dir.glob("*_action.json"):
                payload = _read_json(action_path, [])
                actions = payload if isinstance(payload, list) else [payload]
                for item in actions:
                    if not isinstance(item, dict):
                        continue
                    action = _safe_dict(item.get("action"))
                    action_name = str(action.get("action_name") or "")
                    if not action_name or action_name == "action_pass":
                        continue
                    action_counts[action_name] = action_counts.get(action_name, 0) + 1
                    total_action_counts[action_name] = (
                        total_action_counts.get(action_name, 0) + 1
                    )
                    quantity = _extract_action_quantity(
                        action_name,
                        _safe_dict(action.get("action_param")),
                    )
                    if quantity:
                        action_quantities[action_name] = (
                            action_quantities.get(action_name, 0.0) + quantity
                        )
                        total_action_quantities[action_name] = (
                            total_action_quantities.get(action_name, 0.0) + quantity
                        )
            provenance = _safe_dict(
                _read_json(day_dir / "decision_provenance.json", {})
            )
            source = str(
                provenance.get("decision_source")
                or provenance.get("source")
                or ""
            )
            if source:
                decision_sources[source] = decision_sources.get(source, 0) + 1
                total_decision_sources[source] = (
                    total_decision_sources.get(source, 0) + 1
                )
        by_enterprise[enterprise_id] = {
            "action_counts": action_counts,
            "action_quantities": action_quantities,
            "decision_sources": decision_sources,
        }
    return {
        "round_id": int(round_id),
        "by_enterprise": by_enterprise,
        "total_action_counts": total_action_counts,
        "total_action_quantities": total_action_quantities,
        "decision_sources": total_decision_sources,
    }


def _analysis_for_round(
    workspace_dir: Path,
    enterprise_id: str,
    round_id: int,
) -> Dict[str, Any]:
    return _safe_dict(
        _read_json(
            workspace_dir
            / "enterprises"
            / enterprise_id
            / "records"
            / f"day{round_id}"
            / "analysis.json",
            {},
        )
    )


def _extract_external_response_observability(
    *,
    workspace_dir: Path,
    enterprise_ids: List[str],
    round_id: int,
    external_environment: Dict[str, Any],
    external_environment_policy: Dict[str, Any],
) -> Dict[str, Any]:
    latest_assessments: Dict[str, Any] = {}
    for enterprise_id in enterprise_ids:
        latest_analysis: Dict[str, Any] = {}
        source_round: Optional[int] = None
        for candidate_round in range(int(round_id), -1, -1):
            candidate = _analysis_for_round(
                workspace_dir,
                enterprise_id,
                candidate_round,
            )
            response = _safe_dict(
                candidate.get("external_environment_response")
            )
            if response:
                latest_analysis = response
                source_round = candidate_round
                break
        latest_assessments[enterprise_id] = {
            "available": bool(latest_analysis),
            "source_round": source_round,
            "observed_event_ids": latest_analysis.get(
                "observed_event_ids",
                [],
            ),
            "active_factor_assessment": _safe_dict(
                latest_analysis.get("active_factor_assessment")
            ),
            "impact_assessment": latest_analysis.get("impact_assessment"),
            "response_strategy": latest_analysis.get("response_strategy"),
            "review_criteria": latest_analysis.get("review_criteria"),
        }

    event_detection: Dict[str, Any] = {}
    for event in external_environment_policy.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "")
        event_turn = event.get("turn")
        try:
            event_turn = int(event_turn)
        except (TypeError, ValueError):
            continue
        if not event_id or event_turn > int(round_id):
            continue
        first_detection_by_enterprise: Dict[str, Optional[int]] = {}
        for enterprise_id in enterprise_ids:
            detected_round: Optional[int] = None
            for candidate_round in range(event_turn, int(round_id) + 1):
                analysis = _analysis_for_round(
                    workspace_dir,
                    enterprise_id,
                    candidate_round,
                )
                observed_ids = (
                    _safe_dict(
                        analysis.get("external_environment_response")
                    ).get("observed_event_ids")
                    or []
                )
                if event_id in {str(item) for item in observed_ids}:
                    detected_round = candidate_round
                    break
            first_detection_by_enterprise[enterprise_id] = detected_round
        detected_rounds = [
            value
            for value in first_detection_by_enterprise.values()
            if value is not None
        ]
        event_detection[event_id] = {
            "event_turn": event_turn,
            "detected_enterprise_count": len(detected_rounds),
            "enterprise_count": len(enterprise_ids),
            "first_detection_by_enterprise": first_detection_by_enterprise,
            "detection_latency_by_enterprise": {
                enterprise_id: (
                    detected_round - event_turn
                    if detected_round is not None
                    else None
                )
                for enterprise_id, detected_round
                in first_detection_by_enterprise.items()
            },
        }

    return {
        "schema_version": "external_response_observability.v1",
        "enabled": bool(external_environment_policy.get("enabled")),
        "latest_event_id": _safe_dict(
            external_environment.get("latest_event")
        ).get("event_id"),
        "event_triggered_this_turn": bool(
            external_environment.get("event_triggered_this_turn")
        ),
        "latest_analyst_assessments": latest_assessments,
        "event_detection": event_detection,
        "round_actions": _extract_round_action_response_metrics(
            workspace_dir,
            enterprise_ids,
            round_id,
        ),
    }


def _extract_finance_metrics(history_projection: Dict[str, Any]) -> Dict[str, Any]:
    days = history_projection.get("days") or []
    latest = _safe_dict(days[-1]) if days else {}
    latest_finance = _safe_dict(
        _safe_dict(latest.get("department_metrics")).get("finance")
    )
    trends = _safe_dict(history_projection.get("trends"))
    return {
        "cash": _safe_number(latest_finance.get("cash")),
        "total_revenue": _safe_number(latest_finance.get("total_revenue")),
        "total_cost": _safe_number(latest_finance.get("total_cost")),
        "net_profit": _safe_number(latest_finance.get("net_profit")),
        "cash_delta": _safe_number(trends.get("cash_delta")),
        "revenue_delta": _safe_number(trends.get("revenue_delta")),
        "net_profit_delta": _safe_number(trends.get("net_profit_delta")),
    }


def _extract_observer_finance_metrics(observer_state: Dict[str, Any]) -> Dict[str, Any]:
    observation = _safe_dict(observer_state.get("observation"))
    finance = _safe_dict(observation.get("finance"))
    indicators = _safe_dict(finance.get("financial_indicators"))
    cash_summary = _safe_dict(finance.get("cash_summary"))
    return {
        "cash": _safe_number(cash_summary.get("current_cash", finance.get("cash"))),
        "total_revenue": _safe_number(finance.get("total_revenue")),
        "total_cost": _safe_number(finance.get("total_cost")),
        "net_profit": _safe_number(indicators.get("net_profit")),
        "cash_delta": None,
        "revenue_delta": None,
        "net_profit_delta": None,
    }


def _has_finance_metrics(metrics: Dict[str, Any]) -> bool:
    return any(
        metrics.get(key) is not None
        for key in ("cash", "total_revenue", "total_cost", "net_profit")
    )


def _extract_observer_operating_metrics(
    observer_state: Dict[str, Any],
) -> Dict[str, Any]:
    observation = _safe_dict(observer_state.get("observation"))
    sales = _safe_dict(observation.get("sales"))
    sales_metrics = _safe_dict(sales.get("sales_metrics"))
    service_summary = _safe_dict(sales.get("service_level_summary"))
    inventory = _safe_dict(observation.get("inventory"))
    production = _safe_dict(observation.get("production"))
    inventory_items = [
        item
        for item in (inventory.get("inventory_items") or [])
        if isinstance(item, dict)
    ]
    beer_item = next(
        (
            item
            for item in inventory_items
            if item.get("item_id") == "beer"
        ),
        {},
    )
    total_demand = _safe_number(
        service_summary.get(
            "total_downstream_demand",
            sales_metrics.get("total_downstream_demand"),
        )
    )
    fulfilled_demand = _safe_number(
        service_summary.get(
            "fulfilled_downstream_demand",
            sales_metrics.get("fulfilled_downstream_demand"),
        )
    )
    fill_rate = _safe_number(service_summary.get("fill_rate"))
    if fill_rate is None and total_demand:
        fill_rate = (fulfilled_demand or 0.0) / total_demand
    return {
        "sales": {
            "total_orders": _safe_number(sales_metrics.get("total_orders")),
            "accepted_orders": _safe_number(
                sales_metrics.get("accepted_orders")
            ),
            "completed_orders": _safe_number(
                sales_metrics.get("completed_orders")
            ),
            "rejected_orders": _safe_number(
                sales_metrics.get("rejected_orders")
            ),
            "breached_orders": _safe_number(
                sales_metrics.get("breached_orders")
            ),
            "total_downstream_demand": total_demand,
            "fulfilled_downstream_demand": fulfilled_demand,
            "fill_rate": fill_rate,
            "confirmed_order_backlog_quantity": _safe_number(
                service_summary.get(
                    "confirmed_order_backlog_quantity",
                    sales_metrics.get("confirmed_order_backlog_quantity"),
                )
            ),
            "lost_sales_quantity": _safe_number(
                service_summary.get(
                    "lost_sales_quantity",
                    sales_metrics.get("lost_sales_quantity"),
                )
            ),
        },
        "inventory": {
            "finished_goods_quantity": sum(
                _safe_number(item.get("quantity"), 0.0) or 0.0
                for item in inventory_items
                if item.get("item_type") == "product"
            ),
            "beer_quantity": _safe_number(beer_item.get("quantity")),
            "beer_unit_cost": _safe_number(beer_item.get("unit_price")),
            "warehouse_utilization": _safe_number(
                inventory.get("warehouse_utilization")
            ),
        },
        "production": {
            "total_capacity": _safe_number(production.get("total_capacity")),
            "available_capacity": _safe_number(
                production.get("available_capacity")
            ),
            "total_production": _safe_number(
                _safe_dict(production.get("production_metrics")).get(
                    "total_production"
                )
            ),
            "total_planned": _safe_number(
                _safe_dict(production.get("production_metrics")).get(
                    "total_planned"
                )
            ),
            "total_cost": _safe_number(
                _safe_dict(production.get("production_metrics")).get(
                    "total_cost"
                )
            ),
        },
    }


def _build_production_cost_sensitivity(
    finance_metrics: Dict[str, Any],
    operating_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Complements the cost-sensitive calibre of products sold without rewriting official books."""
    net_profit = _safe_number(finance_metrics.get("net_profit"))
    sales = _safe_dict(operating_metrics.get("sales"))
    inventory = _safe_dict(operating_metrics.get("inventory"))
    production = _safe_dict(operating_metrics.get("production"))
    fulfilled_quantity = _safe_number(
        sales.get("fulfilled_downstream_demand")
    )
    ending_unit_cost = _safe_number(inventory.get("beer_unit_cost"))
    cogs_proxy = None
    adjusted_profit_proxy = None
    if (
        net_profit is not None
        and fulfilled_quantity is not None
        and ending_unit_cost is not None
    ):
        cogs_proxy = fulfilled_quantity * ending_unit_cost
        adjusted_profit_proxy = net_profit - cogs_proxy
    return {
        "official_net_profit": net_profit,
        "cumulative_production_cost": _safe_number(
            production.get("total_cost")
        ),
        "fulfilled_quantity": fulfilled_quantity,
        "ending_finished_goods_unit_cost": ending_unit_cost,
        "cost_of_goods_sold_proxy": cogs_proxy,
        "production_cost_adjusted_profit_proxy": adjusted_profit_proxy,
        "accounting_status": "sensitivity_proxy_not_financial_ledger",
        "interpretation": (
            "Official net profit remains the cross-group primary metric. "
            "The adjusted value subtracts fulfilled quantity multiplied by "
            "ending finished-goods unit cost to test production-cost sensitivity."
        ),
    }


def _population_sd(values: List[float]) -> Optional[float]:
    if not values:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _cobweb_series_metrics(
    records: List[Dict[str, Any]],
    *,
    equilibrium_price: Optional[float],
    equilibrium_quantity: Optional[float],
    price_floor: Optional[float],
    price_ceiling: Optional[float],
    quantity_floor: Optional[float],
    quantity_ceiling: Optional[float],
) -> Dict[str, Any]:
    prices = [
        value
        for value in (
            _safe_number(item.get("unit_price"))
            for item in records
        )
        if value is not None
    ]
    supplies = [
        value
        for value in (
            _safe_number(item.get("market_supply_quantity"))
            for item in records
        )
        if value is not None
    ]
    adjustments = [
        abs(supplies[index] - supplies[index - 1])
        for index in range(1, len(supplies))
    ]
    return {
        "round_count": len(records),
        "price_mean": sum(prices) / len(prices) if prices else None,
        "price_mean_abs_equilibrium_deviation": (
            sum(abs(value - equilibrium_price) for value in prices)
            / len(prices)
            if prices and equilibrium_price is not None
            else None
        ),
        "price_standard_deviation": _population_sd(prices),
        "price_minimum": min(prices) if prices else None,
        "price_maximum": max(prices) if prices else None,
        "price_range": max(prices) - min(prices) if prices else None,
        "price_boundary_hit_count": sum(
            value in {price_floor, price_ceiling}
            for value in prices
            if price_floor is not None and price_ceiling is not None
        ),
        "supply_mean": sum(supplies) / len(supplies) if supplies else None,
        "supply_mean_abs_equilibrium_deviation": (
            sum(abs(value - equilibrium_quantity) for value in supplies)
            / len(supplies)
            if supplies and equilibrium_quantity is not None
            else None
        ),
        "supply_standard_deviation": _population_sd(supplies),
        "supply_minimum": min(supplies) if supplies else None,
        "supply_maximum": max(supplies) if supplies else None,
        "supply_range": max(supplies) - min(supplies) if supplies else None,
        "supply_boundary_hit_count": sum(
            value in {quantity_floor, quantity_ceiling}
            for value in supplies
            if quantity_floor is not None and quantity_ceiling is not None
        ),
        "mean_abs_supply_adjustment": (
            sum(adjustments) / len(adjustments)
            if adjustments
            else None
        ),
        "maximum_abs_supply_adjustment": (
            max(adjustments) if adjustments else None
        ),
    }


def _build_cobweb_metrics(
    *,
    scenario_config: Dict[str, Any],
    external_demand: Dict[str, Any],
    round_id: int,
) -> Dict[str, Any]:
    simulation = _safe_dict(scenario_config.get("simulation"))
    cobweb_config = _safe_dict(simulation.get("cobweb_config"))
    if (
        simulation.get("market_demand_mode") != "cobweb"
        and cobweb_config.get("enabled") is not True
    ):
        return {}
    data = _safe_dict(external_demand.get("data"))
    history = []
    for item in (
        data.get("history") or data.get("cobweb_history") or []
    ):
        if not isinstance(item, dict):
            continue
        item_round = _safe_number(item.get("round"))
        if item_round is not None and int(item_round) <= int(round_id):
            history.append(item)
    demand_intercept = _safe_number(cobweb_config.get("demand_intercept"))
    demand_slope = _safe_number(cobweb_config.get("demand_slope"))
    supply_intercept = _safe_number(cobweb_config.get("supply_intercept"))
    supply_slope = _safe_number(cobweb_config.get("supply_slope"))
    equilibrium_price = None
    equilibrium_quantity = None
    if (
        demand_intercept is not None
        and demand_slope is not None
        and supply_intercept is not None
        and supply_slope is not None
        and demand_slope + supply_slope != 0
    ):
        equilibrium_price = (
            demand_intercept - supply_intercept
        ) / (demand_slope + supply_slope)
        equilibrium_quantity = (
            supply_intercept + supply_slope * equilibrium_price
        )

    formal = _safe_dict(scenario_config.get("formal_experiment_config"))
    primary_window = _safe_dict(formal.get("primary_evaluation_window"))
    start_round = int(primary_window.get("start_round", 3) or 3)
    end_round = int(
        primary_window.get("end_round_inclusive", round_id) or round_id
    )
    end_round = min(end_round, int(round_id))
    midpoint = (start_round + end_round) // 2
    primary_records = [
        item
        for item in history
        if start_round <= int(item.get("round", -1)) <= end_round
    ]
    early_records = [
        item
        for item in history
        if start_round <= int(item.get("round", -1)) < midpoint
    ]
    late_records = [
        item
        for item in history
        if midpoint < int(item.get("round", -1)) <= end_round
    ]
    metric_args = {
        "equilibrium_price": equilibrium_price,
        "equilibrium_quantity": equilibrium_quantity,
        "price_floor": _safe_number(cobweb_config.get("price_floor")),
        "price_ceiling": _safe_number(cobweb_config.get("price_ceiling")),
        "quantity_floor": _safe_number(cobweb_config.get("quantity_floor")),
        "quantity_ceiling": _safe_number(cobweb_config.get("quantity_ceiling")),
    }
    primary_metrics = _cobweb_series_metrics(primary_records, **metric_args)
    early_metrics = _cobweb_series_metrics(early_records, **metric_args)
    late_metrics = _cobweb_series_metrics(late_records, **metric_args)
    source_counts: Dict[str, int] = {}
    for item in history:
        source = str(item.get("market_supply_source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    early_price_deviation = early_metrics.get(
        "price_mean_abs_equilibrium_deviation"
    )
    late_price_deviation = late_metrics.get(
        "price_mean_abs_equilibrium_deviation"
    )
    early_supply_deviation = early_metrics.get(
        "supply_mean_abs_equilibrium_deviation"
    )
    late_supply_deviation = late_metrics.get(
        "supply_mean_abs_equilibrium_deviation"
    )
    return {
        "available": bool(history),
        "history_round_count": len(history),
        "equilibrium_price": equilibrium_price,
        "equilibrium_quantity": equilibrium_quantity,
        "primary_evaluation_window": {
            "start_round": start_round,
            "end_round_inclusive": end_round,
        },
        "primary": primary_metrics,
        "early": early_metrics,
        "late": late_metrics,
        "late_to_early_price_deviation_ratio": (
            late_price_deviation / early_price_deviation
            if early_price_deviation not in (None, 0)
            and late_price_deviation is not None
            else None
        ),
        "late_to_early_supply_deviation_ratio": (
            late_supply_deviation / early_supply_deviation
            if early_supply_deviation not in (None, 0)
            and late_supply_deviation is not None
            else None
        ),
        "market_supply_source_counts": source_counts,
        "agent_plan_source_rounds": source_counts.get(
            "agent_production_plan_created",
            0,
        ),
        "explicit_no_agent_supply_rounds": [
            item.get("round")
            for item in history
            if item.get("market_supply_source")
            == "no_agent_supply_for_round"
        ],
        "interpretation_note": (
            "Equilibrium deviation is descriptive for C3. Profit-balanced "
            "behavior may rationally remain away from the competitive "
            "equilibrium; volatility, adjustment, profit, cash, and service "
            "must be assessed together."
        ),
    }


class RunMetricsProjectionBuilder:
    """Build a compact run/day projection from run_meta, integrity, and history."""

    def __init__(self, read_json: Callable[[Path, Any], Any] = None):
        self.read_json = read_json or _read_json

    def build(
        self,
        *,
        workspace_dir: Path,
        round_id: int,
    ) -> Dict[str, Any]:
        workspace_dir = Path(workspace_dir)
        run_meta = _safe_dict(self.read_json(workspace_dir / "run_meta.json", {}))
        scenario_config = _safe_dict(run_meta.get("scenario_config"))
        scenario_meta = _safe_dict(run_meta.get("scenario_meta"))
        experiment_design = _safe_dict(
            run_meta.get("experiment_design")
            or scenario_config.get("experiment_design")
        )
        runtime_injection = _safe_dict(scenario_config.get("runtime_injection"))
        profit_objective_policy = _safe_dict(
            runtime_injection.get("profit_objective_policy")
        )
        scripted_rule_policy = _safe_dict(
            runtime_injection.get("scripted_rule_policy")
        )
        long_run_experiment_policy = _safe_dict(
            runtime_injection.get("long_run_experiment_policy")
        )
        external_environment_policy = _safe_dict(
            runtime_injection.get("external_environment_policy")
        )
        round_integrity = _safe_dict(
            self.read_json(
                workspace_dir / "round_integrity" / f"day{round_id}.json",
                {},
            )
        )
        architecture_quality = _safe_dict(self.read_json(workspace_dir / "firm_architecture_quality.json", {}))
        totals = _safe_dict(round_integrity.get("totals"))
        enterprise_ids = [
            str(item)
            for item in run_meta.get("enterprise_ids", [])
            if str(item or "").strip()
        ]
        enterprise_metrics: Dict[str, Any] = {}
        for enterprise_id in enterprise_ids:
            history_projection = _safe_dict(
                self.read_json(
                    workspace_dir
                    / "projections"
                    / "history"
                    / f"day{round_id}"
                    / enterprise_id
                    / "history_projection.json",
                    {},
                )
            )
            history_finance = _extract_finance_metrics(history_projection)
            observer_state = _safe_dict(
                self.read_json(
                    workspace_dir
                    / "public"
                    / "observer_state"
                    / f"day{round_id}"
                    / "end_of_day"
                    / f"{enterprise_id}.json",
                    {},
                )
            )
            observer_state_available = bool(observer_state)
            observer_finance = _extract_observer_finance_metrics(
                observer_state
            )
            if _has_finance_metrics(observer_finance):
                finance_metrics = {
                    **observer_finance,
                    "cash_delta": history_finance.get("cash_delta"),
                    "revenue_delta": history_finance.get("revenue_delta"),
                    "net_profit_delta": history_finance.get(
                        "net_profit_delta"
                    ),
                }
                finance_source = (
                    "observer_state_end_of_day_with_history_trends"
                    if history_projection
                    else "observer_state_end_of_day"
                )
            elif _has_finance_metrics(history_finance):
                finance_metrics = history_finance
                finance_source = "history_projection"
            else:
                finance_metrics = history_finance
                finance_source = "unavailable"
            operating_metrics = _extract_observer_operating_metrics(
                observer_state
            )
            enterprise_metrics[enterprise_id] = {
                "finance": finance_metrics,
                "finance_source": finance_source,
                **operating_metrics,
                "production_cost_sensitivity": (
                    _build_production_cost_sensitivity(
                        finance_metrics,
                        operating_metrics,
                    )
                ),
                "history_projection_available": bool(history_projection),
                "observer_state_available": observer_state_available,
                "history_source_day_range": history_projection.get("source_day_range"),
                "history_operating_trends": _safe_dict(
                    _safe_dict(history_projection.get("trends")).get(
                        "operating"
                    )
                ),
            }

        completed_steps = run_meta.get("completed_steps")
        if completed_steps is None and run_meta.get("status") == "running":
            completed_steps = int(round_id) + 1
        scenario_id = str(
            run_meta.get("scenario_id")
            or scenario_meta.get("scenario_id")
            or _safe_dict(scenario_config.get("meta")).get("scenario_id")
            or ""
        )
        metric_contract = build_experiment_metric_contract(
            scenario_id=scenario_id,
            experiment_design=experiment_design,
        )
        run_status = str(run_meta.get("status") or "unknown")
        decision_regime = (
            run_meta.get("decision_regime")
            or experiment_design.get("decision_regime")
        )
        experiment_group = (
            run_meta.get("experiment_group")
            or experiment_design.get("experiment_group")
        )
        config_snapshot_available = bool(scenario_config)
        group_specific_gate_results = {
            "scripted_rule_policy_enabled": bool(scripted_rule_policy.get("enabled")),
            "agent_open_reasoning_disabled": bool(scripted_rule_policy.get("enabled")),
            "profit_objective_disabled": not bool(profit_objective_policy.get("enabled")),
            "profit_objective_enabled": bool(profit_objective_policy.get("enabled")),
            "objective_function_available": bool(
                _safe_dict(profit_objective_policy.get("objective_weights"))
            ),
            "mechanism_policy_context_visible": bool(experiment_design),
            "external_environment_policy_enabled": bool(
                external_environment_policy.get("enabled")
            ),
            "long_run_policy_enabled": bool(long_run_experiment_policy.get("enabled")),
            "future_event_content_hidden": bool(
                _safe_dict(external_environment_policy.get("agent_visibility")).get(
                    "hide_future_event_content"
                )
            ),
        }
        if experiment_group in {"MA", "SA", "NA", "NC"}:
            architecture_policy = _safe_dict(runtime_injection.get("firm_architecture_experiment"))
            group_specific_gate_results.update(
                architecture_protocol_labeled=bool(architecture_policy.get("enabled") and architecture_policy.get("version")),
                phase_records_complete=round_integrity.get("ok") is True,
                failures_retained_in_evaluation=architecture_policy.get("failure_policy") == "record_and_continue",
            )
            if architecture_policy.get("baseline_group") == "C3":
                from config.firm_architecture_experiment import economic_contract, load_baseline
                try:
                    baseline, provenance = load_baseline(architecture_policy["family"])
                    group_specific_gate_results["baseline_regime_matched"] = (
                        architecture_policy.get("baseline") == provenance
                        and economic_contract(scenario_config) == economic_contract(baseline)
                    )
                except (KeyError, OSError, ValueError):
                    group_specific_gate_results["baseline_regime_matched"] = False
        external_demand = _safe_dict(
            self.read_json(
                workspace_dir
                / "public"
                / "exchange"
                / f"day{round_id}"
                / "end_of_day"
                / "external_demand.json",
                {},
            )
        )
        external_environment = _safe_dict(
            self.read_json(
                workspace_dir
                / "public"
                / "exchange"
                / f"day{round_id}"
                / "end_of_day"
                / "external_environment.json",
                {},
            )
        ) or _safe_dict(
            self.read_json(
                workspace_dir
                / "public"
                / "exchange"
                / f"day{round_id}"
                / "external_environment.json",
                {},
            )
        )
        cobweb_metrics = _build_cobweb_metrics(
            scenario_config=scenario_config,
            external_demand=external_demand,
            round_id=round_id,
        )
        external_response_observability = (
            _extract_external_response_observability(
                workspace_dir=workspace_dir,
                enterprise_ids=enterprise_ids,
                round_id=round_id,
                external_environment=external_environment,
                external_environment_policy=external_environment_policy,
            )
            if scenario_id == "long_horizon_evolution"
            else {"enabled": False}
        )
        return {
            "schema_version": RUN_METRICS_PROJECTION_SCHEMA_VERSION,
            "run_id": str(run_meta.get("run_id") or "workspace_unarchived"),
            "scenario_id": scenario_id,
            "orchestrator": run_meta.get("orchestrator", "multi_enterprise"),
            "status": run_status,
            "round_id": int(round_id),
            "planned_total_steps": run_meta.get("planned_total_steps"),
            "completed_steps": completed_steps,
            "enterprise_count": len(enterprise_ids),
            "enterprise_ids": enterprise_ids,
            "round_integrity": {
                "available": bool(round_integrity),
                "ok": round_integrity.get("ok"),
                **({"decision_quality_ok": round_integrity.get("decision_quality_ok")}
                   if experiment_group in {"MA", "SA", "NA", "NC"} else {}),
                "totals": totals,
                "quality_breakdown": {
                    "error_files": totals.get("error_files", 0),
                    "recoverable_error_files": totals.get("recoverable_error_files", 0),
                    "stale_trade_error_files": totals.get("stale_trade_error_files", 0),
                    "validation_failed_error_files": totals.get("validation_failed_error_files", 0),
                    "skill_audit_fallback": totals.get("skill_audit_fallback", 0),
                    "skill_audit_unsuccessful": totals.get(
                        "skill_audit_unsuccessful",
                        0,
                    ),
                    "cobweb_agent_execution_failures_cumulative": totals.get(
                        "cobweb_agent_execution_failures_cumulative",
                        0,
                    ),
                    "strict_gate_note": "Formal clean samples should still require round_integrity.ok == true.",
                },
            },
            "firm_architecture_quality": architecture_quality,
            "experiment_design": experiment_design,
            "decision_regime": decision_regime,
            "agent_objective_profile": (
                run_meta.get("agent_objective_profile")
                or experiment_design.get("agent_objective_profile")
            ),
            "constraint_level": (
                run_meta.get("constraint_level")
                or experiment_design.get("constraint_level")
            ),
            "experiment_group": experiment_group,
            "experiment_metric_contract": metric_contract,
            "metric_family": metric_contract.get("family"),
            "headline_metrics": metric_contract.get("headline_metrics", []),
            "primary_metrics": metric_contract.get("primary_metrics", []),
            "profit_objective_enabled": bool(profit_objective_policy.get("enabled")),
            "objective_function": _safe_dict(profit_objective_policy.get("objective_weights")),
            "long_run_experiment": {
                "enabled": bool(long_run_experiment_policy.get("enabled")),
                "recommended_total_steps": long_run_experiment_policy.get("recommended_total_steps"),
                "history_days": long_run_experiment_policy.get("history_days"),
                "warmup_rounds": long_run_experiment_policy.get("warmup_rounds"),
                "exclude_tail_rounds": long_run_experiment_policy.get("exclude_tail_rounds"),
            },
            "scripted_rule_policy": {
                "enabled": bool(scripted_rule_policy.get("enabled")),
                "mode": scripted_rule_policy.get("mode"),
            },
            "group_specific_gate_results": group_specific_gate_results,
            "external_environment": external_environment,
            "external_response_observability": (
                external_response_observability
            ),
            "sample_inclusion": {
                "run_status_eligible": run_status in {"running", "completed", "stopped", "stopped_by_guard"},
                "round_integrity_ok": round_integrity.get("ok"),
                "recoverable_error_files": totals.get("recoverable_error_files", 0),
                "stale_trade_error_files": totals.get("stale_trade_error_files", 0),
                "cobweb_agent_execution_failures_cumulative": totals.get(
                    "cobweb_agent_execution_failures_cumulative",
                    0,
                ),
                "decision_regime_labeled": bool(decision_regime),
                "config_snapshot_available": config_snapshot_available,
                "group_specific_gates_ok": all(
                    group_specific_gate_results.get(gate) is True
                    for gate in metric_contract.get("group_specific_gates", [])
                ),
                **({"architecture_decision_quality_ok": (
                    round_integrity.get("decision_quality_ok") is True
                    and architecture_quality.get("schema_version") == "firm_architecture_quality.v1"
                    and architecture_quality.get("unavailable_phases") == 0
                    and architecture_quality.get("technical_no_submission_phases") == 0
                )} if experiment_group in {"MA", "SA", "NA", "NC"} else {}),
            },
            "cobweb_metrics": cobweb_metrics,
            "enterprise_metrics": enterprise_metrics,
            "scenario_meta": scenario_meta,
        }


def write_run_metrics_projection(
    *,
    workspace_dir: Path,
    round_id: int,
    output_path: Path,
) -> Dict[str, Any]:
    payload = RunMetricsProjectionBuilder().build(
        workspace_dir=workspace_dir,
        round_id=round_id,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload
