import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from simulate.StateObservation import StateObservation
from simulate.external_environment import ExternalEnvironmentEvolution
from config.simulation_preset_config import get_runtime_injection_config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SimulationEnvAdapter:
    """
    Standardized adapter for supply chain simulation engines and external Agent systems
    Achieve a unified environmental interface to support multiple Agent interactive modes
        """

    def __init__(self, simulation_instance, runtime_injection_config: Optional[Dict[str, Any]] = None):
        self.sim = simulation_instance
        self._initialized = False
        self.runtime_injection_config = runtime_injection_config
        self.external_environment = ExternalEnvironmentEvolution(
            getattr(simulation_instance, "controller", None),
            runtime_injection_config,
        )
        self._propagate_runtime_injection_config()

    def set_runtime_injection_config(self, runtime_injection_config: Optional[Dict[str, Any]]) -> None:
        self.runtime_injection_config = runtime_injection_config
        self.external_environment.set_runtime_config(runtime_injection_config)
        self._propagate_runtime_injection_config()

    def _get_runtime_injection_config(self) -> Dict[str, Any]:
        return self.runtime_injection_config or get_runtime_injection_config()

    def _propagate_runtime_injection_config(self) -> None:
        """Allows enterprise business modules to read the real experimental strategy of this operation."""
        runtime_config = self.runtime_injection_config or {}
        controller = getattr(getattr(self, "sim", None), "controller", None)
        if controller is None:
            return
        if hasattr(controller, "set_runtime_injection_config"):
            controller.set_runtime_injection_config(runtime_config)
        else:
            controller.runtime_injection_config = runtime_config
            for enterprise in getattr(controller, "enterprises", {}).values():
                enterprise.runtime_injection_config = runtime_config
        self.external_environment.controller = controller
        self.external_environment.set_runtime_config(runtime_config)

    def normalize_executor_id(self, payload: list, enterprise_name: str, dept: str) -> list:
        """
        Normalize every executor_id in the payload to the current enterprise name.
        Also amend module_type to the correct value
                """
        if not isinstance(payload, list):
            raise ValueError("Payload must be a list")
        for item in payload:
            if not isinstance(item, dict):
                continue

            if item.get("executor_id") != enterprise_name:
                item["executor_id"] = enterprise_name
            dept_name = dept.capitalize() + "Manager"
            if dept_name == "HrManager":
                dept_name = "HRManager"
            if item.get("module_type") != dept_name:
                item["module_type"] = dept_name

        return payload


    def _parse_parameters(self, params: Dict[str, str]) -> Tuple[Dict, List]:
        """Parsing Parameter Type and Required Filling"""
        properties = {}
        required_params = []

        for param_name, param_desc in params.items():
            param_type = self._parse_param_type(param_desc)
            properties[param_name] = {
                "type": param_type,
                "description": str(param_desc)
            }
            if "optional" not in str(param_desc).lower():
                required_params.append(param_name)

        return properties, required_params

    def _parse_param_type(self, param_desc: str) -> str:
        """Parsing Parameter Type"""
        desc = str(param_desc).lower()
        if "int" in desc:
            return "integer"
        elif "float" in desc or "decimal" in desc or "price" in desc or "cost" in desc:
            return "number"
        elif "bool" in desc or "boolean" in desc or "whether" in desc:
            return "boolean"
        elif "list" in desc or "array" in desc or "multiple" in desc:
            return "array"
        elif "dict" in desc or "object" in desc or "structure" in desc:
            return "object"
        return "string"

    def _cap_external_order_deadline(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        External market orders still use the absolute delivery cut-off date; if beyond the current imitation is really final, round, cut off.
                """
        if not isinstance(order, dict):
            return order
        deadline = order.get("delivery_deadline")
        if deadline is None:
            return order
        market_manager = getattr(self.sim.controller, "market_manager", None)
        if market_manager and hasattr(market_manager, "cap_delivery_deadline"):
            order["delivery_deadline"] = market_manager.cap_delivery_deadline(deadline)
            return order

        total_steps = getattr(self.sim.controller, "total_steps", None)
        if total_steps is not None:
            final_round = max(0, int(total_steps) - 1)
            if deadline > final_round:
                order["delivery_deadline"] = final_round
        return order

    def _get_external_market_order_targets(
        self,
        runtime_config: Dict[str, Any],
        current_day: int = None,
    ) -> List[str]:
        """
        Resolve daily external-market order targets.

        external_market_order_policy takes precedence; legacy fields remain as a
        compatibility fallback for reproducible historical scenarios.
        """
        policy = runtime_config.get("external_market_order_policy") or {}
        if policy.get("enabled") is False:
            return []
        if policy.get("mode") in {"disabled", "none"}:
            return []
        if policy.get("generation_timing", "daily_start") != "daily_start":
            return []
        if current_day is not None:
            try:
                day = int(current_day)
            except (TypeError, ValueError):
                day = None
            if day is not None:
                start_day = policy.get("start_day")
                end_day = policy.get("end_day")
                try:
                    if start_day is not None and day < int(start_day):
                        return []
                except (TypeError, ValueError):
                    pass
                try:
                    if end_day is not None and day > int(end_day):
                        return []
                except (TypeError, ValueError):
                    pass

        configured_targets = policy.get("target_enterprise_ids") or []
        if isinstance(configured_targets, str):
            configured_targets = [configured_targets]

        legacy_targets = runtime_config.get("external_market_order_enterprise_ids") or []
        if isinstance(legacy_targets, str):
            legacy_targets = [legacy_targets]

        targets = list(configured_targets) or list(legacy_targets)
        if not targets and runtime_config.get("external_market_order_enterprise_id"):
            targets = [runtime_config["external_market_order_enterprise_id"]]

        normalized_targets = []
        seen = set()
        for enterprise_id in targets:
            if not enterprise_id or enterprise_id in seen:
                continue
            normalized_targets.append(enterprise_id)
            seen.add(enterprise_id)
        return normalized_targets

    def _append_external_market_orders(
        self,
        workflow: List[Dict[str, Any]],
        runtime_config: Dict[str, Any],
        time_day: int,
    ) -> None:
        """Generate and add external market order actions to the configuration policy."""
        policy = runtime_config.get("external_market_order_policy") or {}
        for external_market_order_enterprise_id in self._get_external_market_order_targets(
            runtime_config,
            current_day=time_day,
        ):
            if external_market_order_enterprise_id not in self.sim.controller.enterprises:
                logger.warning(
                    "Skip external market order injection for missing enterprise %s. Available enterprises: %s",
                    external_market_order_enterprise_id,
                    list(self.sim.controller.enterprises.keys()),
                )
                continue
            if policy.get("mode") == "fixed_schedule":
                orders_by_day = policy.get("orders_by_day") or {}
                order_info = orders_by_day.get(str(time_day)) or orders_by_day.get(time_day) or []
                order_info = [dict(item) for item in order_info if isinstance(item, dict)]
                for index, order in enumerate(order_info, start=1):
                    order.setdefault(
                        "external_demand_id",
                        (
                            f"{external_market_order_enterprise_id}:fixed_external_demand:"
                            f"{time_day}:{index}:{order.get('source_id', 'market')}"
                        ),
                    )
                    order.setdefault("source_type", "external_market")
                    self.sim.controller.market_manager.record_external_demand_order(
                        external_market_order_enterprise_id,
                        time_day,
                        order,
                        demand_mode="fixed_schedule",
                    )
            elif policy.get("mode") == "market_bound_fixed_schedule":
                orders_by_day = policy.get("orders_by_day") or {}
                templates = orders_by_day.get(str(time_day)) or orders_by_day.get(time_day) or []
                market_manager = self.sim.controller.market_manager
                market_ids = (
                    market_manager.get_order_source_market_ids(
                        external_market_order_enterprise_id
                    )
                    if hasattr(market_manager, "get_order_source_market_ids")
                    else []
                )
                order_info = []
                for index, template in enumerate(templates, start=1):
                    if not isinstance(template, dict):
                        continue
                    order = dict(template)
                    try:
                        market_slot = max(1, int(order.pop("market_slot", 1)))
                    except (TypeError, ValueError):
                        market_slot = 1
                    if market_slot > len(market_ids):
                        continue
                    source_id = market_ids[market_slot - 1]
                    order["source_id"] = source_id
                    order["source_type"] = "market"
                    order.setdefault(
                        "external_demand_id",
                        (
                            f"{external_market_order_enterprise_id}:"
                            f"market_bound_fixed_demand:{time_day}:"
                            f"{market_slot}:{source_id}:{index}"
                        ),
                    )
                    market_manager.record_external_demand_order(
                        external_market_order_enterprise_id,
                        time_day,
                        order,
                        demand_mode="market_bound_fixed_schedule",
                    )
                    order_info.append(order)
            else:
                order_info = self.sim.controller.market_manager.generate_external_orders(
                    external_market_order_enterprise_id,
                    time_day
                )
            if order_info:
                for order in order_info:
                    order = self._cap_external_order_deadline(order)
                    workflow.append({
                        "action": {
                            "action_name": "create_order",
                            "action_param": order,
                        },
                        "module_type": "SalesManager",
                        "executor_id": external_market_order_enterprise_id,
                    })

    async def step(self, workflow: List[Dict[str, Any]], execute_type: str, enterprise_name: str, dept: str) -> str:
        """
        Standard core 2: Implementing action
        Received Tool Calls from Claude, converted to engine internal format
                """
        try:
            if execute_type == "daily":
                time_day = self.sim.controller.time_manager.get_day()
                runtime_config = self._get_runtime_injection_config()
                self.external_environment.set_runtime_config(runtime_config)
                self.external_environment.apply(time_day)
                self._append_external_market_orders(workflow, runtime_config, time_day)
                salary_payment_interval_days = runtime_config.get("salary_payment_interval_days") or 1
                salary_payment_enterprises = runtime_config.get("salary_payment_enterprise_ids") or []
                if time_day % salary_payment_interval_days == 0:
                    for enterprise_name in salary_payment_enterprises:
                        if enterprise_name not in self.sim.controller.enterprises:
                            logger.warning(
                                "Skip salary payment injection for missing enterprise %s. Available enterprises: %s",
                                enterprise_name,
                                list(self.sim.controller.enterprises.keys()),
                            )
                            continue
                        workflow.append({
                            "action": {
                                "action_name": "process_salary_payment",
                                "action_param": {},
                            },
                            "module_type": "HRManager",
                            "executor_id": enterprise_name
                        })
            elif execute_type == "run":
                workflow = self.normalize_executor_id(workflow,enterprise_name,dept)
            return await self.sim.controller.advance_simulation(
                workflow=workflow,part=execute_type
            )

        except Exception as e:
            from core.execution_boundary import ExecutionIndeterminateError
            if isinstance(e, ExecutionIndeterminateError):
                raise
            logger.error(f"Error in simulation step: {str(e)}", exc_info=True)
            try:
                current_state = self._get_observation(enterprise_name or "")
            except Exception as observation_error:
                logger.error(
                    "Error getting fallback observation after failed step: %s",
                    str(observation_error),
                    exc_info=True,
                )
                current_state = (
                    f"Observation unavailable for enterprise "
                    f"'{enterprise_name}': {observation_error}"
                )
            return f"Error: {str(e)}\n\nCurrent state:\n{current_state}"

    def _get_observation(self, enterprise_name: str) -> str:
        """
        Standard core 3: Access to state of the environment text
        Use the StateObservation class to get full enterprise status
                """
        try:
            enterprise = self.sim.controller.enterprises.get(enterprise_name)
            if enterprise:
                state_obs = StateObservation.from_enterprise(enterprise)
                state = state_obs.to_dict()
                if hasattr(self.sim.controller, "get_simulation_context"):
                    state["simulation_context"] = self.sim.controller.get_simulation_context()
                logger.debug(f"Generated observation for enterprise {enterprise_name}")
                return state
            return f"Enterprise with ID '{enterprise_name}' not found"
        except Exception as e:
            logger.error(f"Error getting observation: {str(e)}", exc_info=True)
            return f"Error getting observation: {str(e)}"

    def get_available_actions(self) -> List[Dict]:
        """Get details of available actions"""
        actions = []
        for method_name, spec in self.action_registry.items():
            actions.append({
                "name": method_name,
                "description": spec.description,
                "module_type": spec.module_type,
                "influence": spec.influence,
                "params": list(spec.params.keys())
            })
        return actions
