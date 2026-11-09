"""
Environmental Market Module

Responsible for modelling spontaneous procurement from external markets
"""
import logging
import random
import math

from config.simulation_preset_config import (
    DEFAULT_BEER_GAME_CUSTOMER_DELIVERY_LEAD_TIME,
    DEFAULT_BEER_GAME_DEMAND_SERIES,
    DEFAULT_BEER_GAME_UNIT_PRICE,
    DEFAULT_COBWEB_CONFIG,
    DEFAULT_HERDING_CONFIG,
    DEFAULT_MARKET_DEMAND_MODE,
    DEFAULT_SHARED_RESOURCE_CONFIG,
    LEGACY_MARKET_DEMAND_MODE_BEER_GAME,
    MARKET_DEMAND_MODE_HERDING,
    MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
    MARKET_DEMAND_MODE_SHARED_RESOURCE,
    normalize_market_demand_mode,
)

logger = logging.getLogger(__name__)


class MarketManager:
    def __init__(
        self,
        get_enterprise_instance,
        total_steps=None,
        demand_mode=None,
        beer_game_demand_series=None,
        beer_game_customer_delivery_lead_time=None,
        beer_game_unit_price=None,
        beer_game_product_id=None,
        cobweb_config=None,
        shared_resource_config=None,
        herding_config=None
    ):
        self.random = random.Random()
        self.get_enterprise_instance = get_enterprise_instance
        self.markets = {}  # Embedded dictionary structure: parameter:parameter}
        self.total_steps = total_steps
        self.demand_mode = normalize_market_demand_mode(
            demand_mode or DEFAULT_MARKET_DEMAND_MODE
        )
        self.beer_game_demand_series = list(
            beer_game_demand_series or DEFAULT_BEER_GAME_DEMAND_SERIES
        )
        self.beer_game_customer_delivery_lead_time = (
            beer_game_customer_delivery_lead_time
            if beer_game_customer_delivery_lead_time is not None
            else DEFAULT_BEER_GAME_CUSTOMER_DELIVERY_LEAD_TIME
        )
        self.beer_game_unit_price = (
            beer_game_unit_price
            if beer_game_unit_price is not None
            else DEFAULT_BEER_GAME_UNIT_PRICE
        )
        self.beer_game_product_id = str(beer_game_product_id or "beer")
        self.cobweb_config = self._normalize_cobweb_config(cobweb_config)
        self.shared_resource_config = self._normalize_shared_resource_config(shared_resource_config)
        self.herding_config = self._normalize_herding_config(herding_config)
        self.external_demand_history = []
        self.external_order_events = []
        self.cobweb_history = []
        self.shared_resource_history = []
        self.shared_resource_state = self._build_initial_shared_resource_state()
        self.herding_history = []
        self.herding_state = self._build_initial_herding_state()

    def set_total_steps(self, total_steps):
        if total_steps is not None:
            self.total_steps = int(total_steps)

    def configure(
        self,
        demand_mode=None,
        beer_game_demand_series=None,
        beer_game_customer_delivery_lead_time=None,
        beer_game_unit_price=None,
        beer_game_product_id=None,
        cobweb_config=None,
        shared_resource_config=None,
        herding_config=None
    ):
        if demand_mode:
            self.demand_mode = normalize_market_demand_mode(demand_mode)
        if beer_game_demand_series is not None:
            self.beer_game_demand_series = list(beer_game_demand_series)
        if beer_game_customer_delivery_lead_time is not None:
            self.beer_game_customer_delivery_lead_time = int(beer_game_customer_delivery_lead_time)
        if beer_game_unit_price is not None:
            self.beer_game_unit_price = float(beer_game_unit_price)
        if beer_game_product_id is not None:
            self.beer_game_product_id = str(beer_game_product_id or "beer")
        if cobweb_config is not None:
            self.cobweb_config = self._normalize_cobweb_config(cobweb_config)
        if shared_resource_config is not None:
            self.shared_resource_config = self._normalize_shared_resource_config(shared_resource_config)
            self.shared_resource_history = []
            self.shared_resource_state = self._build_initial_shared_resource_state()
        if herding_config is not None:
            self.herding_config = self._normalize_herding_config(herding_config)
            self.herding_history = []
            self.herding_state = self._build_initial_herding_state()
        return self.get_config()

    def get_config(self):
        return {
            "total_steps": self.total_steps,
            "final_round": self._get_final_round(),
            "demand_mode": self.demand_mode,
            "beer_game_demand_series": self.beer_game_demand_series,
            "beer_game_customer_delivery_lead_time": self.beer_game_customer_delivery_lead_time,
            "beer_game_unit_price": self.beer_game_unit_price,
            "beer_game_product_id": self.beer_game_product_id,
            "cobweb_config": dict(self.cobweb_config),
            "cobweb_history": list(self.cobweb_history),
            "shared_resource_config": dict(self.shared_resource_config),
            "shared_resource_state": dict(self.shared_resource_state),
            "shared_resource_history": list(self.shared_resource_history),
            "herding_config": dict(self.herding_config),
            "herding_state": dict(self.herding_state),
            "herding_history": list(self.herding_history),
        }

    def _enterprise_salable_products(self, enterprise_id):
        """Return product ids that the target enterprise can sell to external demand."""
        try:
            enterprise = self.get_enterprise_instance(enterprise_id)
        except Exception:
            enterprise = None
        if enterprise is None:
            return []

        business_modules = getattr(enterprise, "business_modules", {}) or {}
        sales_modules = business_modules.get("SalesManager") or []
        if not sales_modules:
            return []
        try:
            state = sales_modules[0].get_state()
        except Exception:
            return []
        if hasattr(state, "data"):
            state = state.data
        if not isinstance(state, dict):
            return []
        products = state.get("salable_products_idList") or []
        if isinstance(products, str):
            products = [products]
        return [str(product_id) for product_id in products if product_id]

    def _resolve_external_order_product_id(
        self,
        enterprise_id,
        configured_product_id,
        *,
        demand_label="external demand",
    ):
        configured_product_id = str(configured_product_id or "")
        salable_products = self._enterprise_salable_products(enterprise_id)
        if not salable_products or configured_product_id in salable_products:
            return configured_product_id

        resolved_product_id = salable_products[0]
        logger.warning(
            "%s product %s is not salable by %s; "
            "using %s to keep external demand aligned with the target enterprise.",
            demand_label,
            configured_product_id,
            enterprise_id,
            resolved_product_id,
        )
        return resolved_product_id

    def _resolve_scheduled_external_product_id(self, enterprise_id):
        resolved_product_id = self._resolve_external_order_product_id(
            enterprise_id,
            self.beer_game_product_id or "beer",
            demand_label="scheduled external demand",
        )
        self.beer_game_product_id = resolved_product_id
        return resolved_product_id

    def _normalize_cobweb_config(self, cobweb_config=None):
        config = dict(DEFAULT_COBWEB_CONFIG)
        if isinstance(cobweb_config, dict):
            config.update(cobweb_config)
        numeric_keys = [
            "initial_price",
            "demand_intercept",
            "demand_slope",
            "supply_intercept",
            "supply_slope",
            "price_floor",
            "price_ceiling",
            "quantity_floor",
            "quantity_ceiling",
        ]
        for key in numeric_keys:
            config[key] = float(config.get(key, DEFAULT_COBWEB_CONFIG.get(key, 0.0)) or 0.0)
        config["production_lag_rounds"] = max(1, int(config.get("production_lag_rounds", 1) or 1))
        config["customer_delivery_lead_time"] = max(0, int(config.get("customer_delivery_lead_time", 1) or 0))
        if config["demand_slope"] <= 0:
            config["demand_slope"] = float(DEFAULT_COBWEB_CONFIG["demand_slope"])
        if config["price_ceiling"] < config["price_floor"]:
            config["price_ceiling"] = config["price_floor"]
        if config["quantity_ceiling"] < config["quantity_floor"]:
            config["quantity_ceiling"] = config["quantity_floor"]
        config["enabled"] = bool(config.get("enabled", False))
        config["product_id"] = str(config.get("product_id") or "beer")
        config["stability_label"] = str(config.get("stability_label") or "unspecified")
        config["production_response_mode"] = str(
            config.get("production_response_mode") or "agent_endogenous"
        )
        config["endogenous_supply_source"] = str(
            config.get("endogenous_supply_source") or "production_plan_created"
        )
        config["endogenous_supply_lag_rounds"] = max(
            0,
            int(config.get("endogenous_supply_lag_rounds", 1) or 0),
        )
        config["endogenous_supply_fallback"] = str(
            config.get("endogenous_supply_fallback") or "theoretical_lagged_supply"
        )
        return config

    def _normalize_shared_resource_config(self, shared_resource_config=None):
        config = dict(DEFAULT_SHARED_RESOURCE_CONFIG)
        if isinstance(shared_resource_config, dict):
            config.update(shared_resource_config)
        numeric_keys = [
            "initial_stock_quantity",
            "max_stock_quantity",
            "sustainable_acquisition_per_round",
            "regeneration_quantity",
            "resource_quality_floor",
            "base_unit_price",
            "unit_acquisition_cost",
            "collapse_threshold_ratio",
            "warning_threshold_ratio",
            "initial_acquisition_quantity_per_enterprise",
        ]
        for key in numeric_keys:
            config[key] = float(config.get(key, DEFAULT_SHARED_RESOURCE_CONFIG.get(key, 0.0)) or 0.0)
        if config["max_stock_quantity"] <= 0:
            config["max_stock_quantity"] = max(1.0, config["initial_stock_quantity"])
        config["initial_stock_quantity"] = self._clamp(
            config["initial_stock_quantity"],
            0,
            config["max_stock_quantity"],
        )
        config["resource_quality_floor"] = self._clamp(config["resource_quality_floor"], 0, 1)
        config["collapse_threshold_ratio"] = self._clamp(config["collapse_threshold_ratio"], 0, 1)
        config["warning_threshold_ratio"] = self._clamp(config["warning_threshold_ratio"], 0, 1)
        config["enabled"] = bool(config.get("enabled", False))
        config["resource_id"] = str(config.get("resource_id") or "shared_resource_pool")
        config["resource_label"] = str(config.get("resource_label") or config["resource_id"])
        config["product_id"] = str(config.get("product_id") or "resource_product")
        config["target_enterprise_ids"] = list(config.get("target_enterprise_ids") or [])
        config["regeneration_mode"] = str(config.get("regeneration_mode") or "fixed")
        config["quality_function"] = str(config.get("quality_function") or "stock_ratio_linear")
        config["quality_degradation_enabled"] = bool(config.get("quality_degradation_enabled", True))
        config["scarcity_price_enabled"] = bool(config.get("scarcity_price_enabled", False))
        config["acquisition_lag_rounds"] = max(0, int(config.get("acquisition_lag_rounds", 1) or 0))
        config["customer_delivery_lead_time"] = max(0, int(config.get("customer_delivery_lead_time", 1) or 0))
        config["apply_acquisition_cost_to_finance"] = bool(config.get("apply_acquisition_cost_to_finance", False))
        config["acquisition_cost_finance_category"] = str(config.get("acquisition_cost_finance_category") or "raw_materials")
        config["constrain_production_output_to_effective_acquisition"] = bool(
            config.get("constrain_production_output_to_effective_acquisition", False)
        )
        config["external_order_quantity_mode"] = str(
            config.get("external_order_quantity_mode") or "effective_acquisition"
        )
        config["apply_breach_penalty_to_finance"] = bool(config.get("apply_breach_penalty_to_finance", False))
        config["breach_penalty_per_unit"] = max(0.0, float(config.get("breach_penalty_per_unit", 0) or 0))
        config["breach_penalty_finance_category"] = str(config.get("breach_penalty_finance_category") or "market_cost")
        return config

    def _normalize_herding_config(self, herding_config=None):
        config = dict(DEFAULT_HERDING_CONFIG)
        if isinstance(herding_config, dict):
            config.update(herding_config)
        numeric_keys = [
            "base_unit_price",
            "unit_cost_reference",
            "initial_reference_plan_quantity",
            "market_heat_noise_level",
            "herding_pressure_weight",
            "overproduction_threshold_ratio",
            "target_synchronization_threshold",
        ]
        for key in numeric_keys:
            config[key] = float(config.get(key, DEFAULT_HERDING_CONFIG.get(key, 0.0)) or 0.0)
        config["enabled"] = bool(config.get("enabled", False))
        config["product_id"] = str(config.get("product_id") or "smart_sensor")
        config["target_enterprise_ids"] = list(config.get("target_enterprise_ids") or [])
        config["true_demand_series"] = [float(value or 0) for value in (config.get("true_demand_series") or [])]
        config["market_heat_series"] = [float(value or 0) for value in (config.get("market_heat_series") or [])]
        config["customer_delivery_lead_time"] = max(0, int(config.get("customer_delivery_lead_time", 1) or 0))
        config["production_lag_rounds"] = max(0, int(config.get("production_lag_rounds", 1) or 0))
        config["peer_visibility_enabled"] = bool(config.get("peer_visibility_enabled", True))
        config["peer_visibility_lag_rounds"] = max(0, int(config.get("peer_visibility_lag_rounds", 1) or 0))
        return config

    def _build_initial_shared_resource_state(self):
        config = self.shared_resource_config
        stock = float(config.get("initial_stock_quantity", 0) or 0)
        capacity = float(config.get("max_stock_quantity", 1) or 1)
        stock_ratio = stock / capacity if capacity > 0 else 0
        return {
            "resource_id": config.get("resource_id"),
            "resource_label": config.get("resource_label"),
            "product_id": config.get("product_id"),
            "resource_stock": stock,
            "resource_capacity": capacity,
            "resource_stock_ratio": stock_ratio,
            "resource_quality": self._calculate_shared_resource_quality(stock),
            "warning_level": self._get_shared_resource_warning_level(stock_ratio),
            "regeneration_quantity": 0.0,
            "last_round_total_acquisition": 0.0,
            "sustainable_total_acquisition": config.get("sustainable_acquisition_per_round"),
        }

    def _build_initial_herding_state(self):
        config = self.herding_config
        return {
            "product_id": config.get("product_id"),
            "market_heat": self._get_series_value(config.get("market_heat_series"), 0, 0.0),
            "market_heat_label": self._get_market_heat_label(self._get_series_value(config.get("market_heat_series"), 0, 0.0)),
            "visible_demand_signal": self._get_series_value(config.get("true_demand_series"), 0, 0.0),
            "trend_direction": "observe",
            "noise_level": config.get("market_heat_noise_level"),
            "peer_summary": {},
        }

    @staticmethod
    def _clamp(value, floor, ceiling):
        return max(float(floor), min(float(ceiling), float(value)))

    @staticmethod
    def _safe_int(value, default=-1):
        try:
            if value is None:
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _get_series_value(series, day, default=0.0):
        if not series:
            return default
        index = min(max(0, int(day or 0)), len(series) - 1)
        return float(series[index] or 0)

    @staticmethod
    def _get_market_heat_label(market_heat):
        heat = float(market_heat or 0)
        if heat >= 0.80:
            return "hot"
        if heat >= 0.55:
            return "rising"
        if heat >= 0.35:
            return "normal"
        return "cooling"

    @staticmethod
    def _calculate_synchronization_index(values):
        values = [float(value or 0) for value in values if value is not None]
        if len(values) < 2:
            return 0.0
        mean_value = sum(values) / len(values)
        if mean_value <= 0:
            return 0.0
        variance = sum((value - mean_value) ** 2 for value in values) / len(values)
        coefficient_variation = math.sqrt(variance) / mean_value
        return max(0.0, min(1.0, 1.0 - coefficient_variation))
        
    def register_market(self, market_id, enterprise, assigned_workers, market_type, products=None):
        """
        Register market to specified enterprise
                
        Args:
            market: Examples of markets to register
            Enterprise: Examples of enterprise market-owned
                """
        if products is None:
            products = []
        # Register the market into a market dictionary corresponding to enterprise Medium
        try:
            if enterprise.id not in self.markets:
                self.markets[enterprise.id] = {}
            k = 0.5 if market_type == "regional" else 0.3
            type_factor = 1.0 if market_type == "regional" else 1.5
            base_demand = 300
            market_activity_i = self.random.uniform(0.5, 1.5)
            sales_per_market = assigned_workers
            sales_effect = 1 - math.exp(-k * assigned_workers)
            demand_i = base_demand * market_activity_i * sales_effect
            base_prob = 0.1
            alpha = 0.5
            L0 = 5
            beta = 0.5
            order_prob_i = min(0.8, base_prob + alpha * sales_effect)
            base_lead_time_i = L0 * type_factor * (1 / market_activity_i)
            sales_adjustment = 1 + beta * (1 - sales_effect)
            self.markets[enterprise.id][market_id] = {
                "products": products,
                "market_activity_i": market_activity_i,
                "sales_per_market" : sales_per_market,
                "market_type" : market_type,
                "sales_effect" : sales_effect,
                "demand_i" : demand_i,
                "order_prob_i": order_prob_i,
                "base_lead_time_i": base_lead_time_i,
                "sales_adjustment": sales_adjustment
            }
        except Exception as e:
            print("register_market_error", e)

    def get_markets_by_enterprise(self, enterprise_id):
        """
        Access to all designated markets enterprise
                
        Args:
            enterprise_id: enterpriseID
                        
        Returns:
            Dict: all registered markets keyed by market identifier.
                """
        return self.markets.get(enterprise_id, {})

    def _get_order_source_markets(self, enterprise_id):
        """Return markets that can currently serve as external order sources."""
        registered_markets = dict(self.get_markets_by_enterprise(enterprise_id) or {})
        try:
            enterprise = self.get_enterprise_instance(enterprise_id)
        except Exception:
            enterprise = None
        sales_modules = (
            (getattr(enterprise, "business_modules", {}) or {}).get("SalesManager")
            if enterprise is not None else None
        ) or []
        sales_manager = sales_modules[0] if sales_modules else None
        sales_markets = getattr(sales_manager, "markets", {}) if sales_manager is not None else {}
        if isinstance(sales_markets, dict):
            for market_id, market in sales_markets.items():
                if not isinstance(market, dict):
                    continue
                status = str(market.get("status") or "").lower()
                if status in {"active", "completed", "ready"}:
                    registered_markets.setdefault(str(market_id), dict(market))
        return registered_markets

    def get_order_source_market_ids(self, enterprise_id):
        """Return stable active-market ids for deterministic order binding."""
        return sorted(str(item) for item in self._get_order_source_markets(enterprise_id))

    def get_all_markets(self):
        """
        Access to all markets enterprise
                
        Returns:
            Dect: All markets in format parameter:parameter}
                """
        return self.markets

    def get_market(self, enterprise_id, market_id):
        """
        Access to designated market enterprise
                
        Args:
            enterprise_id: enterpriseID
            parameter: Market ID
                        
        Returns:
            None
                """
        enterprise_markets = self.markets.get(enterprise_id, {})
        return enterprise_markets.get(market_id, None)

    def remove_market(self, enterprise_id, market_id):
        """
        Remove specified market enterprise
                
        Args:
            enterprise_id: enterpriseID
            parameter: Market ID
                """
        if enterprise_id in self.markets:
            if market_id in self.markets[enterprise_id]:
                del self.markets[enterprise_id][market_id]

    def _get_final_round(self):
        if self.total_steps is None:
            return None
        return max(0, int(self.total_steps) - 1)

    def cap_delivery_deadline(self, delivery_deadline):
        if delivery_deadline is None:
            return delivery_deadline
        deadline = math.ceil(float(delivery_deadline))
        final_round = self._get_final_round()
        if final_round is not None and deadline > final_round:
            return final_round
        return deadline

    def _record_external_demand(self, demand):
        demand_id = demand.get("external_demand_id")
        if demand_id and any(
            item.get("external_demand_id") == demand_id
            for item in self.external_demand_history
        ):
            return
        self.external_demand_history.append(demand)

    def record_external_order_event(self, enterprise_id, event_type, event_data):
        event = {
            "enterprise_id": enterprise_id,
            "event_type": event_type,
            **event_data
        }
        self.external_order_events.append(event)

    def record_external_demand_order(
        self,
        enterprise_id,
        current_day,
        order,
        demand_mode="fixed_schedule",
    ):
        """Record a deterministic external offer without invoking a random generator."""
        if not isinstance(order, dict):
            return
        self._record_external_demand({
            "external_demand_id": order.get("external_demand_id"),
            "round": int(current_day or 0),
            "enterprise_id": enterprise_id,
            "market_id": order.get("source_id"),
            "product_id": order.get("product_id"),
            "quantity": order.get("quantity", 0),
            "unit_price": order.get("unit_price", 0),
            "delivery_deadline": order.get("delivery_deadline"),
            "offer_expiry_day": order.get("offer_expiry_day"),
            "demand_mode": demand_mode,
        })

    def get_external_demand_history(self, enterprise_id=None):
        if enterprise_id:
            return [
                item for item in self.external_demand_history
                if item.get("enterprise_id") == enterprise_id
            ]
        return list(self.external_demand_history)

    def get_external_order_events(self, enterprise_id=None):
        if enterprise_id:
            return [
                item for item in self.external_order_events
                if item.get("enterprise_id") == enterprise_id
            ]
        return list(self.external_order_events)

    def get_external_demand_summary(self, enterprise_id=None):
        demand_history = self.get_external_demand_history(enterprise_id)
        order_events = self.get_external_order_events(enterprise_id)
        status_quantity = {}
        for event in order_events:
            status = event.get("status")
            quantity = event.get("quantity", 0) or 0
            if status:
                status_quantity[status] = status_quantity.get(status, 0) + quantity
        return {
            "demand_mode": self.demand_mode,
            "total_demand_quantity": sum(item.get("quantity", 0) or 0 for item in demand_history),
            "demand_count": len(demand_history),
            "history": demand_history,
            "order_events": order_events,
            "status_quantity": status_quantity,
            "cobweb_history": list(self.cobweb_history),
            "shared_resource_state": dict(self.shared_resource_state),
            "shared_resource_history": list(self.shared_resource_history),
            "shared_resource_latest_metrics": (
                dict(self.shared_resource_history[-1])
                if self.shared_resource_history else None
            ),
            "herding_state": dict(self.herding_state),
            "herding_history": list(self.herding_history),
            "herding_latest_metrics": (
                dict(self.herding_history[-1])
                if self.herding_history else None
            ),
        }

    def _get_enterprise_runtime_config(self, enterprise_id):
        enterprise = self.get_enterprise_instance(enterprise_id)
        if enterprise is None:
            return {}
        runtime_config = getattr(enterprise, "runtime_injection_config", None)
        if isinstance(runtime_config, dict) and runtime_config:
            return runtime_config
        controller = getattr(enterprise, "controller", None)
        runtime_config = getattr(controller, "runtime_injection_config", None)
        return runtime_config if isinstance(runtime_config, dict) else {}

    def _is_single_enterprise_diagnostic(self, enterprise_id):
        runtime_config = self._get_enterprise_runtime_config(enterprise_id)
        single_case_policy = runtime_config.get("single_enterprise_case_policy") or {}
        return bool(single_case_policy.get("enabled"))

    def _single_enterprise_market_source_limit(self, enterprise_id):
        runtime_config = self._get_enterprise_runtime_config(enterprise_id)
        scripted_policy = runtime_config.get("scripted_rule_policy") or {}
        rules = scripted_policy.get("single_enterprise_rules") or {}
        return max(1, self._safe_int(rules.get("max_market_order_sources_per_round"), 4))

    def generate_external_orders(self, enterprise_id, current_day=None):
        if self.demand_mode in {
            MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
            LEGACY_MARKET_DEMAND_MODE_BEER_GAME,
        }:
            return self._generate_beer_game_orders(enterprise_id, current_day)
        if self.demand_mode == "cobweb":
            return self._generate_cobweb_orders(enterprise_id, current_day)
        if self.demand_mode == MARKET_DEMAND_MODE_SHARED_RESOURCE:
            return self._generate_shared_resource_orders(enterprise_id, current_day)
        if self.demand_mode == MARKET_DEMAND_MODE_HERDING:
            return self._generate_herding_orders(enterprise_id, current_day)
        return self._generate_random_orders(enterprise_id, current_day)

    def _generate_beer_game_orders(self, enterprise_id, current_day=None):
        markets = self._get_order_source_markets(enterprise_id)
        day = int(current_day or 0)
        series = self.beer_game_demand_series or list(DEFAULT_BEER_GAME_DEMAND_SERIES)
        quantity = series[day] if day < len(series) else series[-1]
        delivery_deadline = self.cap_delivery_deadline(
            day + self.beer_game_customer_delivery_lead_time
        )
        product_id = self._resolve_scheduled_external_product_id(enterprise_id)

        def build_order(source_id, source_type, demand_key=None):
            external_demand_id = f"{enterprise_id}:scheduled_external_demand:{day}:{product_id}"
            if demand_key:
                external_demand_id = f"{external_demand_id}:{demand_key}"
            return {
                'product_id': product_id,
                'unit_price': self.beer_game_unit_price,
                'quantity': quantity,
                'source_id': source_id,
                'source_type': source_type,
                'delivery_deadline': delivery_deadline,
                'external_demand_id': external_demand_id
            }

        product_infos = []
        if self._is_single_enterprise_diagnostic(enterprise_id) and markets:
            source_limit = self._single_enterprise_market_source_limit(enterprise_id)
            for market_id in sorted(markets.keys())[:source_limit]:
                product_infos.append(build_order(market_id, 'market', market_id))
        else:
            if markets:
                market_id = next(iter(markets.keys()))
                source_type = 'market'
            else:
                # The underlying external demand is a rule-based input and should not rely on the end enterprise to develop the market first.
                market_id = "external_scheduled_demand_market"
                source_type = 'external_market'
            product_infos.append(build_order(market_id, source_type))

        for product_info in product_infos:
            self._record_external_demand({
                "external_demand_id": product_info["external_demand_id"],
                "round": day,
                "enterprise_id": enterprise_id,
                "market_id": product_info["source_id"],
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": self.beer_game_unit_price,
                "delivery_deadline": delivery_deadline,
                "demand_mode": MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL
            })
        return product_infos

    def _get_lagged_cobweb_price(self, day):
        lag = self.cobweb_config["production_lag_rounds"]
        lagged_round = day - lag
        if lagged_round < 0:
            return self.cobweb_config["initial_price"], "initial_price"
        for item in reversed(self.cobweb_history):
            if item.get("round") == lagged_round:
                return float(item.get("unit_price", self.cobweb_config["initial_price"])), f"round_{lagged_round}"
        return self.cobweb_config["initial_price"], "initial_price_fallback"

    def _get_production_manager(self, enterprise_id):
        enterprise = self.get_enterprise_instance(enterprise_id)
        if enterprise is None:
            return None
        modules = getattr(enterprise, "business_modules", {}).get("ProductionManager") or []
        return modules[0] if modules else None

    def _get_finance_manager(self, enterprise_id):
        enterprise = self.get_enterprise_instance(enterprise_id)
        if enterprise is None:
            return None
        modules = getattr(enterprise, "business_modules", {}).get("FinanceManager") or []
        return modules[0] if modules else None

    def _get_inventory_manager(self, enterprise_id):
        enterprise = self.get_enterprise_instance(enterprise_id)
        if enterprise is None:
            return None
        modules = getattr(enterprise, "business_modules", {}).get("InventoryManager") or []
        return modules[0] if modules else None

    def _get_cobweb_actual_supply_quantity(self, enterprise_id, product_id, day):
        config = self.cobweb_config
        source = config.get("endogenous_supply_source")
        controller_prefix = (
            "scripted"
            if config.get("production_response_mode") == "scripted_formula"
            else "agent"
        )
        supply_round = int(day) - int(config.get("endogenous_supply_lag_rounds", 1))
        if supply_round < 0:
            return None, {
                "source": f"no_lagged_{controller_prefix}_supply",
                "supply_round": supply_round,
                "source_detail": "endogenous supply lag points before simulation start",
            }

        production_manager = self._get_production_manager(enterprise_id)
        if production_manager is None:
            return None, {
                "source": "production_manager_unavailable",
                "supply_round": supply_round,
                "source_detail": "target enterprise has no ProductionManager",
            }

        plans = [
            plan for plan in getattr(production_manager, "production_plans", [])
            if isinstance(plan, dict) and plan.get("product_id") == product_id
        ]
        if source == "production_plan_completed":
            matched_plans = [
                plan for plan in plans
                if self._safe_int(plan.get("completion_time")) == supply_round
                and plan.get("status") == "completed"
            ]
            quantity = sum(
                float((plan.get("progress") or {}).get("quantity_produced", plan.get("quantity", 0)) or 0)
                for plan in matched_plans
            )
        elif source == "production_daily_output":
            matched_plans = [
                plan for plan in plans
                if self._safe_int(plan.get("start_time"), 0) <= supply_round
                and self._safe_int(plan.get("completion_time"), 0) >= supply_round
            ]
            quantity = sum(
                min(
                    float(plan.get("daily_capacity", 0) or 0),
                    float(plan.get("quantity", 0) or 0),
                )
                for plan in matched_plans
            )
        else:
            matched_plans = [
                plan for plan in plans
                if self._safe_int(plan.get("created_time")) == supply_round
            ]
            quantity = sum(float(plan.get("quantity", 0) or 0) for plan in matched_plans)

        if quantity <= 0:
            return None, {
                "source": f"no_{controller_prefix}_supply_for_round",
                "supply_round": supply_round,
                "source_detail": f"no {source} quantity found for {product_id}",
            }
        return quantity, {
            "source": f"{controller_prefix}_{source}",
            "supply_round": supply_round,
            "matched_plan_count": len(matched_plans),
            "matched_plan_ids": [plan.get("plan_id") for plan in matched_plans],
        }

    def _get_enterprise_herding_plan_records(self, enterprise_id, product_id, day):
        config = self.herding_config
        plan_round = int(day) - int(config.get("production_lag_rounds", 1) or 0)
        if plan_round < 0:
            return float(config.get("initial_reference_plan_quantity", 0) or 0), {
                "source": "initial_reference_plan_quantity",
                "plan_round": plan_round,
                "source_detail": "herding cold-start fallback before first Agent plan",
            }, []

        production_manager = self._get_production_manager(enterprise_id)
        if production_manager is None:
            return 0.0, {
                "source": "production_manager_unavailable",
                "plan_round": plan_round,
                "source_detail": "target enterprise has no ProductionManager",
            }, []

        matched_plans = [
            plan for plan in getattr(production_manager, "production_plans", [])
            if isinstance(plan, dict)
            and plan.get("product_id") == product_id
            and self._safe_int(plan.get("created_time")) == plan_round
        ]
        quantity = sum(float(plan.get("quantity", 0) or 0) for plan in matched_plans)
        if quantity <= 0:
            return 0.0, {
                "source": "no_agent_plan_for_round",
                "plan_round": plan_round,
                "source_detail": f"no production plan quantity found for {product_id}",
            }, []
        return quantity, {
            "source": "agent_production_plan_created",
            "plan_round": plan_round,
            "matched_plan_count": len(matched_plans),
            "matched_plan_ids": [plan.get("plan_id") for plan in matched_plans],
        }, matched_plans

    def _get_inventory_quantity(self, enterprise_id, product_id):
        inventory_manager = self._get_inventory_manager(enterprise_id)
        if inventory_manager is None:
            return None
        inventories = getattr(inventory_manager, "inventory", None) or getattr(inventory_manager, "inventories", None)
        if isinstance(inventories, dict):
            item = inventories.get(product_id)
            if isinstance(item, dict):
                return self._safe_float(item.get("quantity"), None)
            if item is not None:
                return self._safe_float(item, None)
        items = getattr(inventory_manager, "items", None)
        if isinstance(items, dict):
            item = items.get(product_id)
            if isinstance(item, dict):
                return self._safe_float(item.get("quantity"), None)
        return None

    def _get_cash_balance(self, enterprise_id):
        finance_manager = self._get_finance_manager(enterprise_id)
        if finance_manager is None:
            return None
        for attr_name in ("cash", "cash_balance", "capital", "current_cash"):
            if hasattr(finance_manager, attr_name):
                return self._safe_float(getattr(finance_manager, attr_name), None)
        return None

    def _settle_herding_round(self, day):
        existing = next((item for item in self.herding_history if item.get("round") == day), None)
        if existing is not None:
            return existing

        config = self.herding_config
        target_enterprise_ids = list(config.get("target_enterprise_ids") or [])
        product_id = config.get("product_id")
        true_demand = self._get_series_value(config.get("true_demand_series"), day, 0.0)
        market_heat = self._get_series_value(config.get("market_heat_series"), day, 0.0)
        previous_heat = self._get_series_value(config.get("market_heat_series"), max(0, int(day) - 1), market_heat)
        trend_direction = "up" if market_heat > previous_heat else "down" if market_heat < previous_heat else "flat"
        unit_price = float(config.get("base_unit_price", 0) or 0)
        unit_cost = float(config.get("unit_cost_reference", 0) or 0)

        enterprise_records = {}
        planned_values = []
        total_planned = 0.0
        total_inventory = 0.0
        cash_values = []
        for enterprise_id in target_enterprise_ids:
            planned_quantity, source_detail, _matched_plans = self._get_enterprise_herding_plan_records(
                enterprise_id,
                product_id,
                day,
            )
            planned_quantity = max(0.0, float(planned_quantity or 0))
            inventory_quantity = self._get_inventory_quantity(enterprise_id, product_id)
            cash_balance = self._get_cash_balance(enterprise_id)
            if inventory_quantity is not None:
                total_inventory += max(0.0, inventory_quantity)
            if cash_balance is not None:
                cash_values.append(cash_balance)
            planned_values.append(planned_quantity)
            total_planned += planned_quantity
            enterprise_records[enterprise_id] = {
                "planned_quantity": planned_quantity,
                "plan_source": source_detail.get("source"),
                "plan_source_detail": source_detail,
                "ending_inventory": inventory_quantity,
                "cash_balance": cash_balance,
                "estimated_production_cost": planned_quantity * unit_cost,
                "estimated_revenue_capacity": planned_quantity * unit_price,
            }

        previous_metrics = None
        for item in reversed(self.herding_history):
            if item.get("round") == int(day) - 1:
                previous_metrics = item
                break
        production_growth_records = {}
        aligned_growth_count = 0
        comparable_growth_count = 0
        total_growth_rate = 0.0
        growth_threshold = 0.05
        for enterprise_id, record in enterprise_records.items():
            previous_record = ((previous_metrics or {}).get("enterprises") or {}).get(enterprise_id) or {}
            previous_quantity = self._safe_float(previous_record.get("planned_quantity"), None)
            current_quantity = self._safe_float(record.get("planned_quantity"), 0.0)
            growth_rate = None
            growth_direction = "unknown"
            aligned_with_heat_trend = None
            if previous_quantity is not None and previous_quantity > 0:
                growth_rate = (current_quantity - previous_quantity) / previous_quantity
                total_growth_rate += growth_rate
                comparable_growth_count += 1
                if growth_rate > growth_threshold:
                    growth_direction = "increase"
                elif growth_rate < -growth_threshold:
                    growth_direction = "decrease"
                else:
                    growth_direction = "stable"
                if trend_direction == "up":
                    aligned_with_heat_trend = growth_direction == "increase"
                elif trend_direction == "down":
                    aligned_with_heat_trend = growth_direction == "decrease"
                else:
                    aligned_with_heat_trend = growth_direction == "stable"
                if aligned_with_heat_trend:
                    aligned_growth_count += 1
            production_growth_records[enterprise_id] = {
                "previous_planned_quantity": previous_quantity,
                "current_planned_quantity": current_quantity,
                "growth_rate": growth_rate,
                "growth_direction": growth_direction,
                "aligned_with_market_heat_trend": aligned_with_heat_trend,
            }
            record["production_growth"] = production_growth_records[enterprise_id]

        enterprise_count = len(target_enterprise_ids) or 1
        average_planned = total_planned / enterprise_count
        demand_share = true_demand / enterprise_count if enterprise_count else true_demand
        overproduction_gap = max(0.0, total_planned - true_demand)
        overproduction_ratio = total_planned / true_demand if true_demand > 0 else 0.0
        synchronization_index = self._calculate_synchronization_index(planned_values)
        production_growth_sync_rate = (
            aligned_growth_count / comparable_growth_count
            if comparable_growth_count > 0
            else None
        )
        average_production_growth_rate = (
            total_growth_rate / comparable_growth_count
            if comparable_growth_count > 0
            else None
        )
        heat_component = max(0.0, min(1.0, market_heat))
        overproduction_component = max(0.0, min(1.0, overproduction_ratio - 1.0))
        herding_index = max(0.0, min(
            1.0,
            0.45 * synchronization_index + 0.35 * heat_component + 0.20 * overproduction_component,
        ))
        inventory_pressure = total_inventory / true_demand if true_demand > 0 else 0.0
        cash_stress_index = 0.0
        if cash_values:
            negative_cash_count = len([value for value in cash_values if value < 0])
            cash_stress_index = negative_cash_count / len(cash_values)

        peer_summary = {}
        if config.get("peer_visibility_enabled", True):
            peer_summary = {
                "visible": True,
                "lag_rounds": config.get("peer_visibility_lag_rounds"),
                "average_planned_quantity": average_planned,
                "total_planned_quantity": total_planned,
                "synchronization_index": synchronization_index,
                "planned_quantity_dispersion": max(planned_values) - min(planned_values) if planned_values else 0.0,
            }
        else:
            peer_summary = {
                "visible": False,
                "lag_rounds": config.get("peer_visibility_lag_rounds"),
                "reason": "peer_visibility_disabled_by_scenario_config",
            }

        metrics = {
            "round": day,
            "demand_mode": MARKET_DEMAND_MODE_HERDING,
            "product_id": product_id,
            "true_demand_quantity": true_demand,
            "visible_demand_signal": true_demand * (0.85 + 0.30 * market_heat),
            "market_heat": market_heat,
            "market_heat_label": self._get_market_heat_label(market_heat),
            "trend_direction": trend_direction,
            "unit_price": unit_price,
            "unit_cost_reference": unit_cost,
            "total_planned_quantity": total_planned,
            "average_planned_quantity": average_planned,
            "demand_share_per_enterprise": demand_share,
            "overproduction_gap": overproduction_gap,
            "overproduction_ratio": overproduction_ratio,
            "overproduction_threshold_ratio": config.get("overproduction_threshold_ratio"),
            "synchronization_index": synchronization_index,
            "production_growth_sync_rate": production_growth_sync_rate,
            "average_production_growth_rate": average_production_growth_rate,
            "production_growth_records": production_growth_records,
            "target_synchronization_threshold": config.get("target_synchronization_threshold"),
            "herding_index": herding_index,
            "inventory_pressure": inventory_pressure,
            "cash_stress_index": cash_stress_index,
            "peer_visibility_enabled": config.get("peer_visibility_enabled"),
            "peer_summary": peer_summary,
            "enterprises": enterprise_records,
            "mechanism_switches": {
                "peer_visibility_enabled": config.get("peer_visibility_enabled"),
                "peer_visibility_lag_rounds": config.get("peer_visibility_lag_rounds"),
                "herding_pressure_weight": config.get("herding_pressure_weight"),
            },
        }
        self.herding_history.append(metrics)
        self.herding_state = {
            "product_id": product_id,
            "market_heat": market_heat,
            "market_heat_label": metrics["market_heat_label"],
            "visible_demand_signal": metrics["visible_demand_signal"],
            "trend_direction": trend_direction,
            "noise_level": config.get("market_heat_noise_level"),
            "peer_summary": peer_summary,
        }
        return metrics

    def _calculate_shared_resource_quality(self, stock_quantity):
        config = self.shared_resource_config
        if not config.get("quality_degradation_enabled", True):
            return 1.0
        capacity = float(config.get("max_stock_quantity", 1) or 1)
        stock_ratio = self._clamp(float(stock_quantity or 0) / capacity, 0, 1)
        floor = float(config.get("resource_quality_floor", 0.35) or 0.35)
        if config.get("quality_function") == "stock_ratio_linear":
            return self._clamp(max(floor, stock_ratio), floor, 1)
        return self._clamp(stock_ratio, floor, 1)

    def _get_shared_resource_warning_level(self, stock_ratio):
        config = self.shared_resource_config
        ratio = float(stock_ratio or 0)
        if ratio <= float(config.get("collapse_threshold_ratio", 0.2) or 0.2):
            return "collapse"
        if ratio <= float(config.get("warning_threshold_ratio", 0.45) or 0.45):
            return "warning"
        return "normal"

    def _get_enterprise_resource_acquisition_plan_records(self, enterprise_id, product_id, day):
        config = self.shared_resource_config
        acquisition_round = int(day) - int(config.get("acquisition_lag_rounds", 1) or 0)
        if acquisition_round < 0:
            return float(config.get("initial_acquisition_quantity_per_enterprise", 0) or 0), {
                "source": "initial_reference_acquisition",
                "acquisition_round": acquisition_round,
                "source_detail": "shared resource cold-start fallback before first Agent plan",
            }, []

        production_manager = self._get_production_manager(enterprise_id)
        if production_manager is None:
            return 0.0, {
                "source": "production_manager_unavailable",
                "acquisition_round": acquisition_round,
                "source_detail": "target enterprise has no ProductionManager",
            }, []

        matched_plans = [
            plan for plan in getattr(production_manager, "production_plans", [])
            if isinstance(plan, dict)
            and plan.get("product_id") == product_id
            and self._safe_int(plan.get("created_time")) == acquisition_round
        ]
        quantity = sum(float(plan.get("quantity", 0) or 0) for plan in matched_plans)
        if quantity <= 0:
            return 0.0, {
                "source": "no_agent_acquisition_for_round",
                "acquisition_round": acquisition_round,
                "source_detail": f"no production plan quantity found for {product_id}",
            }, []
        return quantity, {
            "source": "agent_production_plan_created",
            "acquisition_round": acquisition_round,
            "matched_plan_count": len(matched_plans),
            "matched_plan_ids": [plan.get("plan_id") for plan in matched_plans],
        }, matched_plans

    def _get_enterprise_resource_acquisition_plan_quantity(self, enterprise_id, product_id, day):
        quantity, source_detail, _matched_plans = self._get_enterprise_resource_acquisition_plan_records(
            enterprise_id,
            product_id,
            day,
        )
        return quantity, source_detail

    def _apply_shared_resource_finance_cost(self, enterprise_id, amount, metrics_round):
        if amount <= 0:
            return {
                "enabled": self.shared_resource_config.get("apply_acquisition_cost_to_finance", False),
                "applied": False,
                "amount": 0.0,
                "reason": "non_positive_cost",
            }
        if not self.shared_resource_config.get("apply_acquisition_cost_to_finance", False):
            return {"enabled": False, "applied": False, "amount": amount, "reason": "disabled"}
        finance_manager = self._get_finance_manager(enterprise_id)
        if finance_manager is None:
            return {"enabled": True, "applied": False, "amount": amount, "reason": "finance_manager_unavailable"}
        category = self.shared_resource_config.get("acquisition_cost_finance_category") or "raw_materials"
        result = finance_manager.add_cost(
            amount,
            category,
            description=f"shared_resource_acquisition_cost_round_{metrics_round}",
        )
        return {
            "enabled": True,
            "applied": bool(getattr(result, "success", False)),
            "amount": amount,
            "category": category,
            "reason": "applied" if getattr(result, "success", False) else getattr(result, "message", "finance_cost_failed"),
        }

    def _constrain_shared_resource_production_output(self, matched_plans, effective_quantity):
        if not self.shared_resource_config.get("constrain_production_output_to_effective_acquisition", False):
            return {"enabled": False, "applied": False}
        if not matched_plans:
            return {"enabled": True, "applied": False, "reason": "no_matched_agent_plan"}

        total_planned = sum(float(plan.get("quantity", 0) or 0) for plan in matched_plans)
        if total_planned <= 0:
            return {"enabled": True, "applied": False, "reason": "non_positive_planned_quantity"}

        remaining_effective = max(0.0, float(effective_quantity or 0))
        plan_adjustments = []
        for index, plan in enumerate(matched_plans):
            original_quantity = float(plan.get("quantity", 0) or 0)
            if index == len(matched_plans) - 1:
                constrained_quantity = min(original_quantity, remaining_effective)
            else:
                constrained_quantity = min(
                    original_quantity,
                    effective_quantity * (original_quantity / total_planned),
                )
                remaining_effective -= constrained_quantity
            constrained_quantity = max(0.0, constrained_quantity)
            original_daily_capacity = float(plan.get("daily_capacity", original_quantity) or 0)
            original_total_cost = float(plan.get("total_cost", 0) or 0)
            original_unit_cost = float(plan.get("unit_cost", 0) or 0)
            progress = plan.get("progress") if isinstance(plan.get("progress"), dict) else {}
            already_produced = float(progress.get("quantity_produced", 0) or 0)
            if plan.get("status") == "completed" or already_produced > constrained_quantity:
                plan_adjustments.append({
                    "plan_id": plan.get("plan_id"),
                    "applied": False,
                    "reason": "plan_already_completed_or_over_produced",
                    "original_quantity": original_quantity,
                    "constrained_quantity": constrained_quantity,
                    "already_produced": already_produced,
                })
                continue

            plan["shared_resource_output_constraint"] = {
                "enabled": True,
                "original_quantity": original_quantity,
                "constrained_quantity": constrained_quantity,
                "effective_acquisition_quantity": effective_quantity,
                "reason": "resource_quality_and_stock_limit_effective_output",
            }
            plan["quantity"] = constrained_quantity
            plan["daily_capacity"] = min(original_daily_capacity, constrained_quantity)
            if original_quantity > 0 and original_total_cost > 0:
                plan["total_cost"] = original_total_cost * (constrained_quantity / original_quantity)
            if constrained_quantity > 0:
                plan["unit_cost"] = original_unit_cost
            plan_adjustments.append({
                "plan_id": plan.get("plan_id"),
                "applied": True,
                "original_quantity": original_quantity,
                "constrained_quantity": constrained_quantity,
                "original_daily_capacity": original_daily_capacity,
                "constrained_daily_capacity": plan.get("daily_capacity"),
            })

        return {
            "enabled": True,
            "applied": any(item.get("applied") for item in plan_adjustments),
            "adjustments": plan_adjustments,
        }

    def _calculate_shared_resource_unit_price(self, stock_ratio):
        config = self.shared_resource_config
        base_price = float(config.get("base_unit_price", 0) or 0)
        if not config.get("scarcity_price_enabled", False):
            return base_price
        scarcity_multiplier = 1 + max(0.0, 1.0 - float(stock_ratio or 0))
        return base_price * scarcity_multiplier

    def _settle_shared_resource_round(self, day):
        existing = next((item for item in self.shared_resource_history if item.get("round") == day), None)
        if existing is not None:
            return existing

        config = self.shared_resource_config
        target_enterprise_ids = list(config.get("target_enterprise_ids") or [])
        previous_stock = float(self.shared_resource_state.get("resource_stock", config.get("initial_stock_quantity", 0)) or 0)
        capacity = float(config.get("max_stock_quantity", 1) or 1)
        regeneration_quantity = (
            float(config.get("regeneration_quantity", 0) or 0)
            if config.get("regeneration_mode") == "fixed"
            else 0.0
        )
        available_stock = self._clamp(previous_stock + regeneration_quantity, 0, capacity)
        stock_ratio_before = available_stock / capacity if capacity > 0 else 0
        resource_quality = self._calculate_shared_resource_quality(available_stock)
        unit_price = self._calculate_shared_resource_unit_price(stock_ratio_before)
        unit_cost = float(config.get("unit_acquisition_cost", 0) or 0)

        enterprise_records = {}
        total_planned = 0.0
        total_raw_effective = 0.0
        product_id = config.get("product_id")
        matched_plans_by_enterprise = {}
        for enterprise_id in target_enterprise_ids:
            planned_quantity, source_detail, matched_plans = self._get_enterprise_resource_acquisition_plan_records(
                enterprise_id,
                product_id,
                day,
            )
            matched_plans_by_enterprise[enterprise_id] = matched_plans
            planned_quantity = max(0.0, float(planned_quantity or 0))
            raw_effective = planned_quantity * resource_quality
            enterprise_records[enterprise_id] = {
                "planned_acquisition": planned_quantity,
                "raw_effective_acquisition": raw_effective,
                "effective_acquisition": raw_effective,
                "acquisition_source": source_detail.get("source"),
                "acquisition_source_detail": source_detail,
                "unit_price": unit_price,
                "unit_acquisition_cost": unit_cost,
            }
            total_planned += planned_quantity
            total_raw_effective += raw_effective

        compression_ratio = 1.0
        if total_raw_effective > available_stock and total_raw_effective > 0:
            compression_ratio = available_stock / total_raw_effective
        total_effective = 0.0
        for record in enterprise_records.values():
            effective = record["raw_effective_acquisition"] * compression_ratio
            record["effective_acquisition"] = effective
            record["revenue"] = effective * unit_price
            record["acquisition_cost"] = record["planned_acquisition"] * unit_cost
            record["gross_profit"] = record["revenue"] - record["acquisition_cost"]
            total_effective += effective

        for enterprise_id, record in enterprise_records.items():
            record["finance_cost_effect"] = self._apply_shared_resource_finance_cost(
                enterprise_id,
                record["acquisition_cost"],
                day,
            )
            record["production_output_constraint"] = self._constrain_shared_resource_production_output(
                matched_plans_by_enterprise.get(enterprise_id) or [],
                record["effective_acquisition"],
            )

        stock_after = self._clamp(available_stock - total_effective, 0, capacity)
        stock_ratio_after = stock_after / capacity if capacity > 0 else 0
        warning_level = self._get_shared_resource_warning_level(stock_ratio_after)
        sustainable = float(config.get("sustainable_acquisition_per_round", 0) or 0)
        metrics = {
            "round": day,
            "demand_mode": MARKET_DEMAND_MODE_SHARED_RESOURCE,
            "resource_id": config.get("resource_id"),
            "resource_label": config.get("resource_label"),
            "product_id": product_id,
            "resource_stock_before": previous_stock,
            "resource_stock_after_regeneration": available_stock,
            "resource_stock": stock_after,
            "resource_capacity": capacity,
            "resource_stock_ratio": stock_ratio_after,
            "regeneration_quantity": regeneration_quantity,
            "resource_quality": resource_quality,
            "warning_level": warning_level,
            "unit_price": unit_price,
            "unit_acquisition_cost": unit_cost,
            "total_planned_acquisition": total_planned,
            "total_raw_effective_acquisition": total_raw_effective,
            "total_effective_acquisition": total_effective,
            "sustainable_total_acquisition": sustainable,
            "overuse_quantity": max(0.0, total_planned - sustainable),
            "resource_shortage_compression_ratio": compression_ratio,
            "mechanism_switches": {
                "apply_acquisition_cost_to_finance": config.get("apply_acquisition_cost_to_finance", False),
                "acquisition_cost_finance_category": config.get("acquisition_cost_finance_category"),
                "constrain_production_output_to_effective_acquisition": config.get(
                    "constrain_production_output_to_effective_acquisition", False
                ),
                "external_order_quantity_mode": config.get("external_order_quantity_mode"),
                "apply_breach_penalty_to_finance": config.get("apply_breach_penalty_to_finance", False),
                "breach_penalty_per_unit": config.get("breach_penalty_per_unit", 0),
                "breach_penalty_finance_category": config.get("breach_penalty_finance_category"),
            },
            "enterprises": enterprise_records,
        }
        self.shared_resource_history.append(metrics)
        self.shared_resource_state = {
            "resource_id": config.get("resource_id"),
            "resource_label": config.get("resource_label"),
            "product_id": product_id,
            "resource_stock": stock_after,
            "resource_capacity": capacity,
            "resource_stock_ratio": stock_ratio_after,
            "resource_quality": resource_quality,
            "warning_level": warning_level,
            "regeneration_quantity": regeneration_quantity,
            "last_round_total_acquisition": total_planned,
            "sustainable_total_acquisition": sustainable,
        }
        return metrics

    def _generate_shared_resource_orders(self, enterprise_id, current_day=None):
        day = int(current_day or 0)
        config = self.shared_resource_config
        if not config.get("enabled", False):
            return []
        if enterprise_id not in set(config.get("target_enterprise_ids") or []):
            return []

        product_id = self._resolve_external_order_product_id(
            enterprise_id,
            config.get("product_id"),
            demand_label="shared resource external demand",
        )
        config["product_id"] = product_id
        metrics = self._settle_shared_resource_round(day)
        enterprise_metrics = (metrics.get("enterprises") or {}).get(enterprise_id, {})
        effective_quantity = float(enterprise_metrics.get("effective_acquisition", 0) or 0)
        planned_quantity = float(enterprise_metrics.get("planned_acquisition", 0) or 0)
        order_quantity_mode = config.get("external_order_quantity_mode") or "effective_acquisition"
        if order_quantity_mode == "planned_acquisition":
            order_quantity = math.ceil(planned_quantity)
        elif order_quantity_mode == "max_planned_effective":
            order_quantity = math.ceil(max(planned_quantity, effective_quantity))
        else:
            order_quantity = math.ceil(effective_quantity)

        markets = self.get_markets_by_enterprise(enterprise_id)
        if markets:
            market_id = next(iter(markets.keys()))
            source_type = "market"
        else:
            market_id = "external_shared_resource_market"
            source_type = "external_market"
        delivery_deadline = self.cap_delivery_deadline(
            day + int(config.get("customer_delivery_lead_time", 1) or 0)
        )
        external_demand_id = f"{enterprise_id}:shared_resource:{day}:{product_id}"
        record = {
            "external_demand_id": external_demand_id,
            "round": day,
            "enterprise_id": enterprise_id,
            "market_id": market_id,
            "product_id": product_id,
            "quantity": order_quantity,
            "unit_price": metrics.get("unit_price"),
            "delivery_deadline": delivery_deadline,
            "demand_mode": MARKET_DEMAND_MODE_SHARED_RESOURCE,
            "planned_acquisition": enterprise_metrics.get("planned_acquisition"),
            "effective_acquisition": effective_quantity,
            "external_order_quantity_mode": order_quantity_mode,
            "acquisition_source": enterprise_metrics.get("acquisition_source"),
            "resource_id": config.get("resource_id"),
            "resource_stock": metrics.get("resource_stock"),
            "resource_stock_ratio": metrics.get("resource_stock_ratio"),
            "resource_quality": metrics.get("resource_quality"),
            "warning_level": metrics.get("warning_level"),
            "breach_penalty_enabled": config.get("apply_breach_penalty_to_finance", False),
            "breach_penalty_per_unit": config.get("breach_penalty_per_unit", 0),
            "breach_penalty_finance_category": config.get("breach_penalty_finance_category"),
            "shared_resource_round_metrics": metrics,
        }
        self._record_external_demand(record)
        if order_quantity <= 0:
            return []
        return [{
            "product_id": product_id,
            "unit_price": metrics.get("unit_price"),
            "quantity": order_quantity,
            "source_id": market_id,
            "source_type": source_type,
            "delivery_deadline": delivery_deadline,
            "external_demand_id": external_demand_id,
            "breach_penalty_enabled": config.get("apply_breach_penalty_to_finance", False),
            "breach_penalty_per_unit": config.get("breach_penalty_per_unit", 0),
            "breach_penalty_finance_category": config.get("breach_penalty_finance_category"),
        }]

    def _generate_herding_orders(self, enterprise_id, current_day=None):
        day = int(current_day or 0)
        config = self.herding_config
        if not config.get("enabled", False):
            return []
        if enterprise_id not in set(config.get("target_enterprise_ids") or []):
            return []

        product_id = self._resolve_external_order_product_id(
            enterprise_id,
            config.get("product_id"),
            demand_label="herding external demand",
        )
        config["product_id"] = product_id
        metrics = self._settle_herding_round(day)
        enterprise_count = max(1, len(config.get("target_enterprise_ids") or []))
        true_demand = float(metrics.get("true_demand_quantity", 0) or 0)
        market_heat = float(metrics.get("market_heat", 0) or 0)
        heat_multiplier = 0.85 + 0.30 * market_heat
        order_quantity = max(0, math.ceil((true_demand / enterprise_count) * heat_multiplier))

        markets = self.get_markets_by_enterprise(enterprise_id)
        if markets:
            market_id = next(iter(markets.keys()))
            source_type = "market"
        else:
            market_id = "external_herding_market"
            source_type = "external_market"
        delivery_deadline = self.cap_delivery_deadline(
            day + int(config.get("customer_delivery_lead_time", 1) or 0)
        )
        external_demand_id = f"{enterprise_id}:herding:{day}:{product_id}"
        enterprise_metrics = (metrics.get("enterprises") or {}).get(enterprise_id, {})
        record = {
            "external_demand_id": external_demand_id,
            "round": day,
            "enterprise_id": enterprise_id,
            "market_id": market_id,
            "product_id": product_id,
            "quantity": order_quantity,
            "unit_price": metrics.get("unit_price"),
            "delivery_deadline": delivery_deadline,
            "demand_mode": MARKET_DEMAND_MODE_HERDING,
            "true_demand_quantity": true_demand,
            "market_heat": market_heat,
            "market_heat_label": metrics.get("market_heat_label"),
            "visible_demand_signal": metrics.get("visible_demand_signal"),
            "peer_visibility_enabled": config.get("peer_visibility_enabled"),
            "planned_quantity": enterprise_metrics.get("planned_quantity"),
            "herding_round_metrics": metrics,
        }
        self._record_external_demand(record)
        if order_quantity <= 0:
            return []
        return [{
            "product_id": product_id,
            "unit_price": metrics.get("unit_price"),
            "quantity": order_quantity,
            "source_id": market_id,
            "source_type": source_type,
            "delivery_deadline": delivery_deadline,
            "external_demand_id": external_demand_id,
        }]

    def _get_endogenous_supply_fallback_quantity(
        self,
        theoretical_supply_quantity,
        actual_supply_source=None,
    ):
        fallback = self.cobweb_config.get("endogenous_supply_fallback")
        source = str((actual_supply_source or {}).get("source") or "")
        if (
            fallback == "theoretical_lagged_supply"
            and source.startswith("no_lagged_")
        ):
            return theoretical_supply_quantity, "theoretical_lagged_supply"
        if fallback == "last_market_supply":
            for item in reversed(self.cobweb_history):
                quantity = item.get("market_supply_quantity", item.get("planned_supply_quantity"))
                if quantity is not None:
                    return (
                        self._clamp(
                            quantity,
                            self.cobweb_config["quantity_floor"],
                            self.cobweb_config["quantity_ceiling"],
                        ),
                        "last_market_supply",
                    )
        return float(self.cobweb_config["quantity_floor"]), "quantity_floor"

    def _generate_cobweb_orders(self, enterprise_id, current_day=None):
        day = int(current_day or 0)
        config = self.cobweb_config
        product_id = self._resolve_external_order_product_id(
            enterprise_id,
            config["product_id"],
            demand_label="cobweb external demand",
        )
        config["product_id"] = product_id
        lagged_price, lagged_price_source = self._get_lagged_cobweb_price(day)

        raw_theoretical_supply = config["supply_intercept"] + config["supply_slope"] * lagged_price
        theoretical_supply_quantity = self._clamp(
            raw_theoretical_supply,
            config["quantity_floor"],
            config["quantity_ceiling"],
        )
        supply_quantity = theoretical_supply_quantity
        supply_source = {
            "source": "theoretical_lagged_supply",
            "supply_round": day,
            "source_detail": "scripted cobweb supply from lagged price",
        }
        endogenous_supply_mode = config.get("production_response_mode") in {
            "agent_endogenous",
            "scripted_formula",
        }
        if endogenous_supply_mode:
            actual_supply, actual_supply_source = self._get_cobweb_actual_supply_quantity(
                enterprise_id,
                product_id,
                day,
            )
            if actual_supply is not None:
                supply_quantity = self._clamp(
                    actual_supply,
                    config["quantity_floor"],
                    config["quantity_ceiling"],
                )
                supply_source = actual_supply_source
            else:
                (
                    fallback_supply_quantity,
                    effective_fallback,
                ) = self._get_endogenous_supply_fallback_quantity(
                    theoretical_supply_quantity,
                    actual_supply_source,
                )
                supply_quantity = self._clamp(
                    fallback_supply_quantity,
                    config["quantity_floor"],
                    config["quantity_ceiling"],
                )
                supply_source = {
                    **actual_supply_source,
                    "fallback": config.get("endogenous_supply_fallback"),
                    "effective_fallback": effective_fallback,
                    "fallback_supply_quantity": supply_quantity,
                }

        raw_market_price = (
            config["demand_intercept"] - supply_quantity
        ) / config["demand_slope"]
        unit_price = self._clamp(
            raw_market_price,
            config["price_floor"],
            config["price_ceiling"],
        )
        raw_demand_quantity = config["demand_intercept"] - config["demand_slope"] * unit_price
        demand_quantity = self._clamp(
            raw_demand_quantity,
            config["quantity_floor"],
            config["quantity_ceiling"],
        )
        order_quantity = math.ceil(demand_quantity)

        markets = self.get_markets_by_enterprise(enterprise_id)
        if markets:
            market_id = next(iter(markets.keys()))
            source_type = "market"
        else:
            market_id = "external_cobweb_market"
            source_type = "external_market"
        delivery_deadline = self.cap_delivery_deadline(
            day + config["customer_delivery_lead_time"]
        )
        external_demand_id = f"{enterprise_id}:cobweb:{day}:{product_id}"
        record = {
            "external_demand_id": external_demand_id,
            "round": day,
            "enterprise_id": enterprise_id,
            "market_id": market_id,
            "product_id": product_id,
            "quantity": order_quantity,
            "unit_price": unit_price,
            "delivery_deadline": delivery_deadline,
            "demand_mode": "cobweb",
            "stability_label": config["stability_label"],
            "lagged_price": lagged_price,
            "lagged_price_source": lagged_price_source,
            "planned_supply_quantity": supply_quantity,
            "raw_planned_supply_quantity": raw_theoretical_supply,
            "theoretical_planned_supply_quantity": theoretical_supply_quantity,
            "raw_theoretical_planned_supply_quantity": raw_theoretical_supply,
            "actual_supply_quantity": (
                supply_quantity
                if endogenous_supply_mode
                and str(supply_source.get("source", "")).startswith(("agent_", "scripted_"))
                else None
            ),
            "raw_actual_supply_quantity": (
                actual_supply
                if endogenous_supply_mode
                and str(supply_source.get("source", "")).startswith(("agent_", "scripted_"))
                else None
            ),
            "market_supply_quantity": supply_quantity,
            "market_supply_source": supply_source.get("source"),
            "market_supply_source_detail": supply_source,
            "production_response_mode": config.get("production_response_mode"),
            "raw_market_price": raw_market_price,
            "demand_quantity_at_price": demand_quantity,
            "raw_demand_quantity_at_price": raw_demand_quantity,
            "excess_supply_quantity": supply_quantity - demand_quantity,
            "equations": {
                "demand": "Qd = demand_intercept - demand_slope * P_t",
                "lagged_supply": "Qs_t = supply_intercept + supply_slope * P_(t-lag)",
                "agent_endogenous_price": "P_t = (demand_intercept - actual_or_fallback_supply_t) / demand_slope",
            },
        }
        self.cobweb_history.append(record)
        self._record_external_demand(record)
        if order_quantity <= 0:
            return []
        return [{
            "product_id": product_id,
            "unit_price": unit_price,
            "quantity": order_quantity,
            "source_id": market_id,
            "source_type": source_type,
            "delivery_deadline": delivery_deadline,
            "external_demand_id": external_demand_id,
        }]

    def random_select_product(self,enterprise_id):
        return self.generate_external_orders(enterprise_id)

    def _generate_random_orders(self, enterprise_id, current_day=None):
        """
        Production order requirements based on market demand
                
                """
        # Collection of all products in all markets and their corresponding market ID
        markets = self.get_markets_by_enterprise(enterprise_id)
        if markets == {}:
            return None
        enterprise = self.get_enterprise_instance(enterprise_id)
        if enterprise is None:
            return None
        total_demand = 0
        for market_id, market_info in markets.items():
            raw_quantity_i = market_info["demand_i"] * self.random.uniform(0.8, 1.2)
            total_demand += raw_quantity_i
        # production_manager = enterprise.business_modules.get("ProductionManager")[0]
        # total_capacity = production_manager.get_total_capacity().data.get("total_capacity", 0)
        # load_ratio = total_demand / total_capacity
        gama = 1.0
        # capacity_adjustment =  1 + gama * max(0, load_ratio -1)
        capacity_adjustment = 1
        buffer_ratio = 0.2
        # capacity_limit = total_capacity * (1 + buffer_ratio)
        # scale = min(1, capacity_limit / total_demand)
        scale = 1
        order_list = []
        for market_id, market_info in markets.items():
            raw_quantity_i = market_info["demand_i"] * self.random.uniform(0.8, 1.2)
            final_quantity_i = math.ceil(raw_quantity_i * scale)
            products = market_info["products"]
            # product = random.choice(products)
            # product_id = product.get("id")
            delivery_deadline = market_info["base_lead_time_i"] * market_info["sales_adjustment"] * capacity_adjustment * self.random.uniform(0.9,1.1)
            delivery_deadline = self.cap_delivery_deadline(delivery_deadline)
            # Increased sales prices and changes in profits
            unit_price = 180 * self.random.uniform(1.3,1.5)
            final_quantity_i = math.ceil(raw_quantity_i * scale)
            product_info = {
                'product_id': "beer",
                'unit_price': unit_price,
                'quantity': final_quantity_i,
                'source_id': market_id,
                'source_type': 'market',
                'delivery_deadline': math.ceil(delivery_deadline)
            }
            order_list.append(product_info)
            self._record_external_demand({
                "round": current_day,
                "enterprise_id": enterprise_id,
                "market_id": market_id,
                "product_id": "beer",
                "quantity": final_quantity_i,
                "unit_price": unit_price,
                "delivery_deadline": math.ceil(delivery_deadline),
                "demand_mode": "random"
            })
        return order_list
