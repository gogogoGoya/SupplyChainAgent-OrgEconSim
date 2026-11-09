import json
from types import SimpleNamespace

import enterprise.modules.procurement_manager as procurement_manager_module
from config.module_config import ProcurementConfig
from enterprise.modules.hr_manager import HRManager
from enterprise.modules.inventory_manager import InventoryManager
from enterprise.modules.procurement_manager import ProcurementManager
from enterprise.modules.sales_manager import SalesManager
from agent.scripted_rule_runner import ScriptedRuleRunner
from agent.static_utils import StaticUtils


def test_procurement_runtime_config_prefers_enterprise_active_config():
    manager = ProcurementManager.__new__(ProcurementManager)
    manager.enterprise = type(
        "EnterpriseStub",
        (),
        {
            "runtime_injection_config": {
                "profit_objective_policy": {"enabled": True, "target_enterprise_ids": ["Retailer"]}
            },
            "controller": type(
                "ControllerStub",
                (),
                {"runtime_injection_config": {"profit_objective_policy": {"enabled": False}}},
            )(),
            "config": {},
        },
    )()

    active_config = manager._get_active_runtime_injection_config()

    assert active_config["profit_objective_policy"]["enabled"] is True


def test_e1_manual_purchase_demand_is_capped_to_covered_need():
    manager = ProcurementManager.__new__(ProcurementManager)
    manager.enterprise = SimpleNamespace(
        id="Manufacturer",
        role_tags=["finished_goods_manufacturer"],
        runtime_injection_config={
            "long_run_experiment_policy": {
                "enabled": True,
                "target_enterprise_ids": ["Manufacturer"],
                "execution_guard": {
                    "cap_manual_purchase_demand": True,
                    "manual_purchase_demand_cap_multiplier": 1.2,
                },
            }
        },
    )
    manager.calculate_replenishment_quantity = lambda **kwargs: SimpleNamespace(
        success=True,
        data={"suggested_order_quantity": 100},
    )

    guarded = manager._cap_long_run_manual_purchase_demand("Malt", 1000)

    assert guarded["applied"] is True
    assert guarded["quantity"] == 120


def test_e1_procurement_commitment_share_is_a_hard_guard():
    manager = ProcurementManager.__new__(ProcurementManager)
    manager._get_cash_guard_status = lambda: {
        "cash_summary": {"current_cash": 1_000_000, "warning_threshold": 100_000},
        "committed_pending_cost": 200_000,
        "remaining_cash_after_commitments": 800_000,
    }
    manager._get_long_run_execution_guard = lambda: {
        "max_pending_procurement_cash_share": 0.24,
        "minimum_procurement_commitment_budget": 25_000,
    }

    guarded = manager._evaluate_cash_commitment(100_000)

    assert guarded["long_run_commitment_guard"]["policy_budget"] == 240_000
    assert guarded["long_run_commitment_guard"]["is_hard_blocked"] is True
    assert guarded["is_hard_blocked"] is True


def test_e1_sales_margin_floor_is_resolved_from_enterprise_role():
    manager = SalesManager.__new__(SalesManager)
    manager.enterprise = SimpleNamespace(
        id="Distributor",
        role_tags=["intermediate_distributor"],
        runtime_injection_config={
            "long_horizon_profitability_policy": {
                "enabled": True,
                "target_enterprise_ids": ["Distributor"],
                "b2b_margin_floor_by_role_tag": {
                    "intermediate_distributor": 1.18,
                },
            }
        },
    )

    assert manager._long_horizon_margin_floor() == 1.18


def test_e1_buyer_price_floor_keeps_bid_ask_band_open():
    manager = ProcurementManager.__new__(ProcurementManager)
    manager.config = ProcurementConfig()
    manager.enterprise = SimpleNamespace(
        id="Manufacturer",
        role_tags=["finished_goods_manufacturer"],
        runtime_injection_config={
            "long_horizon_profitability_policy": {
                "enabled": True,
                "target_enterprise_ids": ["Manufacturer"],
                "b2b_max_price_floor_by_role_tag": {
                    "finished_goods_manufacturer": 1.36,
                },
            }
        },
    )
    manager._get_inventory_item = lambda material_id: {
        "unit_price": 100,
        "quantity": 500,
        "reorder_point": 100,
        "safety_stock": 50,
    }
    manager._get_on_hand_quantity = lambda material_id: 500
    manager._get_current_day = lambda: 10

    default_pricing = manager._resolve_b2b_max_price("Malt")
    conservative_agent_pricing = manager._resolve_b2b_max_price(
        "Malt",
        explicit_max_price=110,
    )

    assert default_pricing["max_price"] == 136
    assert default_pricing["strategy"] == "base_band_with_trade_floor"
    assert conservative_agent_pricing["max_price"] == 136
    assert conservative_agent_pricing["strategy"] == "agent_explicit_max_price_with_trade_floor"


def test_long_horizon_cost_multipliers_are_target_scoped():
    runtime_config = {
        "long_horizon_cost_policy": {
            "enabled": True,
            "target_enterprise_ids": ["Supplier"],
            "salary_cost_multiplier": 0.85,
            "warehouse_operating_cost_multiplier": 0.50,
        }
    }
    targeted_enterprise = SimpleNamespace(
        id="Supplier",
        runtime_injection_config=runtime_config,
    )
    unrelated_enterprise = SimpleNamespace(
        id="SingleEnterprise",
        runtime_injection_config=runtime_config,
    )

    targeted_hr = HRManager.__new__(HRManager)
    targeted_hr.enterprise = targeted_enterprise
    unrelated_hr = HRManager.__new__(HRManager)
    unrelated_hr.enterprise = unrelated_enterprise
    targeted_inventory = InventoryManager.__new__(InventoryManager)
    targeted_inventory.enterprise = targeted_enterprise
    unrelated_inventory = InventoryManager.__new__(InventoryManager)
    unrelated_inventory.enterprise = unrelated_enterprise

    assert targeted_hr._long_horizon_salary_cost_multiplier() == 0.85
    assert unrelated_hr._long_horizon_salary_cost_multiplier() == 1.0
    assert targeted_inventory._long_horizon_cost_multiplier(
        "warehouse_operating_cost_multiplier"
    ) == 0.50
    assert unrelated_inventory._long_horizon_cost_multiplier(
        "warehouse_operating_cost_multiplier"
    ) == 1.0


def test_explicit_procurement_material_catalog_cannot_be_expanded_by_stale_matrix():
    allowed = StaticUtils._get_procurement_allowed_material_ids({
        "self_state": {
            "purchasable_materials_idList": ["Malt", "Hops", "Yeast"],
            "materials_suppliers_matrix": {
                "MATERIAL_1": [{"supplier_name": "Stale_S0_Supplier"}],
            },
            "supplier_candidates": [
                {
                    "supplier_name": "Stale_S0_Supplier",
                    "materials": {"MATERIAL_1": {"unit_price": 10}},
                }
            ],
        }
    })

    assert allowed == {"Malt", "Hops", "Yeast"}


def test_scripted_supplier_selection_uses_deadline_feasible_low_cost_combination():
    runner = ScriptedRuleRunner.__new__(ScriptedRuleRunner)
    matrix = {
        "MATERIAL_1": [
            {
                "supplier_name": "Supplier_Fast",
                "unit_price": 13,
                "min_order_quantity": 300,
                "processing_time": 0,
                "reliability_score": 0.99,
            },
            {
                "supplier_name": "Supplier_Balanced",
                "unit_price": 10,
                "min_order_quantity": 500,
                "processing_time": 1,
                "reliability_score": 0.96,
            },
            {
                "supplier_name": "Supplier_Economy",
                "unit_price": 8,
                "min_order_quantity": 1000,
                "processing_time": 3,
                "reliability_score": 0.9,
            },
        ]
    }

    def state(department, round_id):
        if department == "procurement":
            return {"materials_suppliers_matrix": matrix}
        if department == "sales":
            return {
                "sales_orders": {
                    "accepted": [
                        {"status": "accepted", "delivery_deadline": 13}
                    ]
                }
            }
        return {}

    runner._self_state = state
    option = runner._select_supplier_purchase_option("MATERIAL_1", 2000, 10)

    assert option["supplier_name"] == "Supplier_Balanced"
    assert option["logistics_mode"] == "rail"
    assert option["arrival_round"] == 13
    assert option["deadline_feasible"] is True


def test_candidate_supplier_is_registered_atomically_on_first_purchase():
    candidate = {
        "supplier_name": "Supplier_Balanced",
        "supplier_type": "external",
        "materials": {
            "MATERIAL_1": {
                "unit_price": 10,
                "quantity": 20000,
                "min_order_quantity": 500,
            }
        },
        "processing_time": 1,
        "quality_level": "standard",
        "reliability_score": 0.96,
    }

    class HRStub:
        def get_available_workers(self, department):
            return SimpleNamespace(data={"count": 4})

        def assign_workers(self, department, task_id, workers):
            return SimpleNamespace(success=True)

    class FinanceStub:
        def get_balance(self):
            return SimpleNamespace(data={"balance": 1_000_000})

    manager = ProcurementManager.__new__(ProcurementManager)
    manager.module_id = "procurement_Manufacturer"
    manager.module_type = "ProcurementManager"
    manager.config = ProcurementConfig()
    manager.suppliers = {}
    manager.next_supplier_id = 1
    manager.purchase_orders = []
    manager.next_order_id = 1
    manager.supplier_selection_events = []
    manager.procurement_events = []
    manager.procurement_metrics = {
        "total_orders": 0,
        "total_cost": 0.0,
        "total_quantity": 0.0,
    }
    manager.enterprise = SimpleNamespace(
        id="Manufacturer",
        role_tags=[],
        supplier_name_list=[],
        time_manager=SimpleNamespace(get_day=lambda: 10),
        runtime_injection_config={
            "single_enterprise_supplier_selection_policy": {
                "enabled": True,
                "target_enterprise_ids": ["Manufacturer"],
                "candidate_suppliers": [candidate],
            },
            "top_tier_supply_policy": {"enabled": False},
        },
        business_modules={
            "HRManager": [HRStub()],
            "FinanceManager": [FinanceStub()],
        },
    )
    manager._evaluate_cash_commitment = lambda cost: {
        "commitment_level": "healthy",
        "estimated_cost": cost,
    }

    response = manager.create_purchase_order(
        material_id="MATERIAL_1",
        quantity=2000,
        supplier_name="Supplier_Balanced",
        logistics_mode="rail",
    )

    assert response.success is True
    assert len(manager.suppliers) == 1
    assert manager.enterprise.supplier_name_list == ["Supplier_Balanced"]
    assert manager.purchase_orders[0]["supplier_name"] == "Supplier_Balanced"
    assert manager.purchase_orders[0]["supplier_selected_on_order"] is True
    assert manager.purchase_orders[0]["arrival_time"] == 13
    assert manager.supplier_selection_events[0]["selection_mode"] == "register_on_first_purchase"


def test_candidate_supplier_execution_recovers_run_meta_catalog(tmp_path, monkeypatch):
    candidate = {
        "supplier_name": "Supplier_Fast",
        "supplier_type": "external",
        "materials": {
            "MATERIAL_1": {
                "unit_price": 13,
                "quantity": 12000,
                "min_order_quantity": 300,
            }
        },
        "processing_time": 0,
        "quality_level": "high",
        "reliability_score": 0.99,
    }
    jobs_root = tmp_path / "workspace_jobs"
    run_root = jobs_root / "run_single_supplier_selection"
    run_root.mkdir(parents=True)
    (run_root / "run_meta.json").write_text(
        json.dumps(
            {
                "scenario_config": {
                    "runtime_injection": {
                        "single_enterprise_supplier_selection_policy": {
                            "enabled": True,
                            "target_enterprise_ids": ["Manufacturer"],
                            "selection_mode": "register_on_first_purchase",
                            "candidate_suppliers": [candidate],
                        },
                        "top_tier_supply_policy": {"enabled": False},
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(procurement_manager_module, "AGENT_ROOT", tmp_path)
    monkeypatch.setattr(procurement_manager_module, "WORKSPACE_JOBS_ROOT", jobs_root)

    class HRStub:
        def get_available_workers(self, department):
            return SimpleNamespace(data={"count": 4})

        def assign_workers(self, department, task_id, workers):
            return SimpleNamespace(success=True)

    class FinanceStub:
        def get_balance(self):
            return SimpleNamespace(data={"balance": 1_000_000})

    manager = ProcurementManager.__new__(ProcurementManager)
    manager.module_id = "procurement_Manufacturer"
    manager.module_type = "ProcurementManager"
    manager.config = ProcurementConfig()
    manager.suppliers = {}
    manager.next_supplier_id = 1
    manager.purchase_orders = []
    manager.next_order_id = 1
    manager.supplier_selection_events = []
    manager.procurement_events = []
    manager.procurement_metrics = {
        "total_orders": 0,
        "total_cost": 0.0,
        "total_quantity": 0.0,
    }
    manager.enterprise = SimpleNamespace(
        id="Manufacturer",
        role_tags=[],
        policy_tags=[],
        supplier_name_list=[],
        time_manager=SimpleNamespace(get_day=lambda: 10),
        runtime_injection_config={},
        controller=SimpleNamespace(runtime_injection_config={}),
        config={},
        business_modules={
            "HRManager": [HRStub()],
            "FinanceManager": [FinanceStub()],
        },
    )
    manager._evaluate_cash_commitment = lambda cost: {
        "commitment_level": "healthy",
        "estimated_cost": cost,
    }

    response = manager.create_purchase_order(
        material_id="MATERIAL_1",
        quantity=500,
        supplier_name="Supplier_Fast",
        logistics_mode="air",
    )

    assert response.success is True
    assert len(manager.suppliers) == 1
    assert manager.purchase_orders[0]["supplier_name"] == "Supplier_Fast"
    assert manager.purchase_orders[0]["supplier_selected_on_order"] is True


def test_single_case_scripted_procurement_caps_to_top_tier_single_order_limit():
    runner = ScriptedRuleRunner.__new__(ScriptedRuleRunner)
    runner._policy = lambda: {"mode": "single_case"}
    runner._rules = lambda: {
        "max_procurement_orders_per_round": 1,
        "procurement_min_budget": 5000,
        "procurement_budget_cash_share": 0.35,
        "preferred_logistics_mode": "road",
    }
    runner._procurement_available_workers = lambda round_id: 1
    runner._supplier_min_order_by_material = lambda suppliers, round_id=None: {}
    runner._current_cash = lambda round_id: 10_000_000
    runner._supplier_price_by_material = lambda round_id: {"MATERIAL_1": 10}
    runner._self_state = lambda dept, round_id: {
        "top_tier_supply_guard": {
            "enabled": True,
            "max_single_order_amount": 22500,
            "available_credit": 50000,
            "external_logistics_cost_multiplier": 0.1,
        }
    } if dept == "procurement" else {}

    selected = runner._filter_procurement_orders([("MATERIAL_1", 5520)], 10, [])

    assert selected
    material_id, quantity = selected[0]
    assert material_id == "MATERIAL_1"
    assert quantity < 5520
    estimated_total = runner._estimate_procurement_order_cost(material_id, quantity, 10)
    assert estimated_total <= 22500


def test_single_case_hr_zero_staff_recruitment_survives_normalization(tmp_path):
    run_root = tmp_path / "run_single_case_hr_zero"
    action_dir = run_root / "enterprises" / "Manufacturer" / "department" / "hr" / "day12"
    action_dir.mkdir(parents=True)
    (run_root / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "handoff_day": 10,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (action_dir / "hr.json").write_text(
        json.dumps(
            {
                "self_state": {
                    "department_staffing": {
                        "PRODUCTION": {"count": 3, "available": 3, "pending_recruits": 0},
                        "PROCUREMENT": {"count": 2, "available": 2, "pending_recruits": 0},
                        "SALES": {"count": 4, "available": 2, "pending_recruits": 0},
                        "INVENTORY": {"count": 1, "available": 1, "pending_recruits": 0},
                        "HR": {"count": 0, "available": 0, "pending_recruits": 0},
                        "FINANCE": {"count": 0, "available": 0, "pending_recruits": 0},
                    },
                    "recruitment_status": {"pending_by_department": {}},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "handle_recruitment",
                    "action_param": {"department": "HR", "num_people": 2},
                },
                "module_type": "HRManager",
                "executor_id": "Manufacturer",
                "action_reason": "HR count=0 and available=0.",
            }
        ],
        department="hr",
        enterprise_name="Manufacturer",
        round_id=12,
        action_dir=action_dir,
    )

    assert normalized[0]["action"]["action_name"] == "handle_recruitment"
    assert normalized[0]["action"]["action_param"] == {"department": "HR", "num_people": 2}


def test_production_workflow_filters_infeasible_material_plan(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {"allow_create_production_plan": True}},
        "self_state": {
            "recovery_guard": {
                "candidates": [
                    {
                        "product_id": "beer",
                        "material_feasible_quantity": 0,
                        "recommended_plan_quantity": 0,
                        "recommended_daily_capacity": 0,
                        "blocking_reasons": ["NO_MATERIAL_FEASIBILITY", "MATERIAL_SHORTAGE"],
                        "raw_material_snapshots": {"Yeast": {"on_hand": 0, "units_per_product": 0.5}},
                    }
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_production_plan",
                    "action_param": {"product_id": "beer", "quantity": 10, "daily_capacity": 5},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=12,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"


def test_production_workflow_caps_plan_to_recovery_feasible_quantity(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {"allow_create_production_plan": True}},
        "self_state": {
            "recovery_guard": {
                "candidates": [
                    {
                        "product_id": "beer",
                        "material_feasible_quantity": 12,
                        "recommended_plan_quantity": 8,
                        "recommended_daily_capacity": 4,
                        "blocking_reasons": ["MATERIAL_SHORTAGE"],
                        "raw_material_snapshots": {"Yeast": {"on_hand": 6, "units_per_product": 0.5}},
                    }
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_production_plan",
                    "action_param": {"product_id": "beer", "quantity": 50, "daily_capacity": 20},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=12,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"]["quantity"] == 8
    assert action["action_param"]["daily_capacity"] == 4


def test_production_workflow_does_not_treat_empty_recipe_as_material_block(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {"allow_create_production_plan": True}},
        "self_state": {
            "recovery_guard": {
                "candidates": [
                    {
                        "product_id": "crop",
                        "material_feasible_quantity": 0,
                        "recommended_plan_quantity": 6,
                        "recommended_daily_capacity": 3,
                        "blocking_reasons": ["NO_MATERIAL_FEASIBILITY"],
                        "raw_material_snapshots": {},
                    }
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_production_plan",
                    "action_param": {"product_id": "crop", "quantity": 10, "daily_capacity": 5},
                },
                "module_type": "ProductionManager",
                "executor_id": "Producer",
            }
        ],
        department="production",
        enterprise_name="Producer",
        round_id=12,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"]["quantity"] == 6
    assert action["action_param"]["daily_capacity"] == 3


def test_c3_shared_resource_production_caps_to_sustainable_share(monkeypatch):
    state = {
        "policy_context": {
            "active_modes": {"shared_resource": True, "profit_objective": True},
            "action_constraints": {
                "allow_create_production_plan": True,
                "shared_resource_acquisition_plan_enabled": True,
                "profit_objective_enabled": True,
            },
            "relevant_policies": {
                "shared_resource": {
                    "resource": {
                        "product_id": "copper_ore",
                        "target_enterprise_ids": ["Miner_A", "Miner_B", "Miner_C", "Miner_D"],
                        "sustainable_acquisition_per_round": 200,
                        "warning_threshold_ratio": 0.65,
                        "collapse_threshold_ratio": 0.25,
                        "resource_quality_floor": 0.15,
                    }
                }
            },
        },
        "simulation_context": {
            "shared_resource_state": {
                "product_id": "copper_ore",
                "resource_stock_ratio": 0.16,
                "resource_quality": 0.15,
                "warning_level": "collapse",
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_production_plan",
                    "action_param": {
                        "product_id": "copper_ore",
                        "quantity": 1000,
                        "daily_capacity": 1000,
                    },
                },
                "module_type": "ProductionManager",
                "executor_id": "Miner_A",
                "action_reason": "old minimum batch target",
            }
        ],
        department="production",
        enterprise_name="Miner_A",
        round_id=28,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"]["quantity"] == 12.5
    assert action["action_param"]["daily_capacity"] == 12.5
    assert "c3_shared_resource_guard" in normalized[0]["action_reason"]


def test_shared_resource_production_cap_is_c3_only(monkeypatch):
    state = {
        "policy_context": {
            "active_modes": {"shared_resource": True},
            "action_constraints": {
                "allow_create_production_plan": True,
                "shared_resource_acquisition_plan_enabled": True,
            },
            "relevant_policies": {
                "shared_resource": {
                    "resource": {
                        "product_id": "copper_ore",
                        "target_enterprise_ids": ["Miner_A", "Miner_B", "Miner_C", "Miner_D"],
                        "sustainable_acquisition_per_round": 200,
                    }
                }
            },
        },
        "simulation_context": {
            "shared_resource_state": {
                "product_id": "copper_ore",
                "resource_stock_ratio": 0.16,
                "resource_quality": 0.15,
                "warning_level": "collapse",
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_production_plan",
                    "action_param": {
                        "product_id": "copper_ore",
                        "quantity": 1000,
                        "daily_capacity": 1000,
                    },
                },
                "module_type": "ProductionManager",
                "executor_id": "Miner_A",
            }
        ],
        department="production",
        enterprise_name="Miner_A",
        round_id=28,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"]["quantity"] == 1000
    assert action["action_param"]["daily_capacity"] == 1000


def test_production_workflow_filters_build_line_without_capacity_candidate(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {"allow_build_production_line": True}},
        "self_state": {
            "margin_guard": {
                "summary": {
                    "has_capacity_expansion_candidate": False,
                    "capacity_expansion_candidate_count": 0,
                }
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "build_production_line",
                    "action_param": {"line_type": "standard", "capacity": 10},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=12,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"


def test_single_case_prewarm_procurement_supplier_id_resolves_from_run_meta(tmp_path):
    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "procurement"
        / "day4"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_01_market_insufficient",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
                "scenario_config": {
                    "enterprise_specs": [
                        {
                            "enterprise_id": "Manufacturer",
                            "purchasable_materials_idList": ["MATERIAL_1"],
                            "initial_suppliers": [
                                {
                                    "supplier_id": "SUPPLIER_1",
                                    "supplier_name": "Supplier_A",
                                    "materials": {
                                        "MATERIAL_1": {
                                            "unit_price": 10,
                                            "quantity": 15000,
                                        }
                                    },
                                }
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "single_case_prewarm_seed": True,
                "action": {
                    "action_name": "create_purchase_order",
                    "action_param": {
                        "material_id": "MATERIAL_1",
                        "supplier_id": "SUPPLIER_1",
                        "quantity": 3500,
                        "logistics_mode": "rail",
                    },
                },
                "module_type": "ProcurementManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="procurement",
        enterprise_name="Manufacturer",
        round_id=4,
        action_dir=action_dir,
    )

    assert len(normalized) == 1
    assert normalized[0]["action"]["action_param"] == {
        "material_id": "MATERIAL_1",
        "quantity": 3500,
        "supplier_name": "Supplier_A",
        "logistics_mode": "rail",
    }


def test_single_case_prewarm_seed_can_build_line_without_capacity_candidate(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {"action_constraints": {"allow_build_production_line": True}},
        "self_state": {
            "margin_guard": {
                "summary": {
                    "has_capacity_expansion_candidate": False,
                    "capacity_expansion_candidate_count": 0,
                }
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "production"
        / "day1"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "single_case_prewarm_seed": True,
                "action": {
                    "action_name": "build_production_line",
                    "action_param": {"line_type": "small"},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=1,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "build_production_line"
    assert action["action_param"] == {
        "line_type": "small",
        "single_case_prewarm_seed": True,
    }


def test_single_case_prewarm_seed_marker_is_ignored_outside_prewarm_context(
    monkeypatch,
):
    state = {
        "policy_context": {"action_constraints": {"allow_build_production_line": True}},
        "self_state": {
            "margin_guard": {
                "summary": {
                    "has_capacity_expansion_candidate": False,
                    "capacity_expansion_candidate_count": 0,
                }
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "single_case_prewarm_seed": True,
                "action": {
                    "action_name": "build_production_line",
                    "action_param": {"line_type": "small"},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=10,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"


def test_single_case_prewarm_sales_accept_remaps_missing_seed_order(
    monkeypatch,
    tmp_path,
):
    state = {
        "self_state": {
            "sales_orders": {
                "available": [
                    {
                        "order_id": "SALE_ORDER_9",
                        "status": "available",
                        "quantity": 20,
                        "total_amount": 16000,
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day8"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_05_cash_pressure",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "single_case_prewarm_seed": True,
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_10"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=8,
        action_dir=action_dir,
    )

    assert normalized[0]["action"]["action_name"] == "accept_order"
    assert normalized[0]["action"]["action_param"]["order_id"] == "SALE_ORDER_9"


def test_single_case_prewarm_sales_accept_missing_order_passes_when_none_available(
    monkeypatch,
    tmp_path,
):
    state = {"self_state": {"sales_orders": {"available": []}}}
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day8"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_05_cash_pressure",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "single_case_prewarm_seed": True,
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_10"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=8,
        action_dir=action_dir,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"


def test_single_case_prewarm_sales_accept_survives_when_snapshot_missing(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: {})

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day2"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_03_capacity_bottleneck",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "single_case_prewarm_seed": True,
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_1"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=2,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "accept_order"
    assert action["action_param"]["order_id"] == "SALE_ORDER_1"


def test_single_case_capacity_bottleneck_build_line_survives_margin_filter(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {"action_constraints": {"allow_build_production_line": True}},
        "self_state": {
            "margin_guard": {
                "summary": {
                    "has_capacity_expansion_candidate": False,
                    "capacity_expansion_candidate_count": 0,
                }
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "production"
        / "day10"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_03_capacity_bottleneck",
                    "primary_issue": "capacity_bottleneck",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "build_production_line",
                    "action_param": {"line_type": "small", "line_id": "line_2"},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=10,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "build_production_line"
    assert action["action_param"] == {
        "line_type": "small",
        "single_enterprise_capacity_recovery": True,
    }


def test_single_case_capacity_bottleneck_build_line_is_not_rewritten_after_expansion(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {"action_constraints": {"allow_build_production_line": True}},
        "self_state": {
            "production_lines": {
                "total": 2,
                "available_capacity": 500,
                "by_status": {"idle": 1, "working": 1, "under_construction": 0},
            },
            "recovery_guard": {
                "candidates": [
                    {
                        "product_id": "PRODUCT_1",
                        "material_feasible_quantity": 13000,
                        "blocking_reasons": [],
                    }
                ]
            },
            "margin_guard": {
                "summary": {
                    "has_capacity_expansion_candidate": False,
                    "capacity_expansion_candidate_count": 0,
                }
            },
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "production"
        / "day12"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_03_capacity_bottleneck",
                    "primary_issue": "capacity_bottleneck",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "build_production_line",
                    "action_param": {"line_type": "small", "line_id": "line_3"},
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=12,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "build_production_line"
    assert action["action_param"] == {
        "line_type": "small",
        "single_enterprise_capacity_recovery": True,
    }


def test_single_case_capacity_bottleneck_sales_rejects_overdue_or_uncovered_order(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {"action_constraints": {"allow_accept_order": True}},
        "self_state": {
            "sales_orders": {
                "available": [
                    {
                        "order_id": "SALE_ORDER_OVERDUE",
                        "status": "available",
                        "product_id": "PRODUCT_1",
                        "quantity": 1500,
                        "total_amount": 750000,
                        "delivery_deadline": 12,
                    }
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day14"
    )
    inventory_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "inventory"
        / "day14"
    )
    action_dir.mkdir(parents=True)
    inventory_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_03_capacity_bottleneck",
                    "primary_issue": "capacity_bottleneck",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    (inventory_dir / "inventory.json").write_text(
        json.dumps(
            {
                "self_state": {
                    "inventory_items": [
                        {"item_id": "PRODUCT_1", "item_type": "product", "quantity": 2000}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_OVERDUE"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=14,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "reject_order"
    assert action["action_param"]["order_id"] == "SALE_ORDER_OVERDUE"
    assert "delivery deadline has passed" in action["action_param"]["reason"]


def test_single_case_staff_shortage_sales_reserves_inventory_across_accepts(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {"action_constraints": {"allow_accept_order": True}},
        "self_state": {
            "sales_orders": {
                "available": [
                    {
                        "order_id": "SALE_ORDER_6",
                        "product_id": "PRODUCT_1",
                        "quantity": 780,
                        "total_amount": 624000,
                        "delivery_deadline": 11,
                    },
                    {
                        "order_id": "SALE_ORDER_5",
                        "product_id": "PRODUCT_1",
                        "quantity": 360,
                        "total_amount": 288000,
                        "delivery_deadline": 12,
                    },
                    {
                        "order_id": "SALE_ORDER_7",
                        "product_id": "PRODUCT_1",
                        "quantity": 360,
                        "total_amount": 288000,
                        "delivery_deadline": 12,
                    },
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day15"
    )
    inventory_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "inventory"
        / "day15"
    )
    action_dir.mkdir(parents=True)
    inventory_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_04_staff_shortage",
                    "primary_issue": "staff_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    (inventory_dir / "inventory.json").write_text(
        json.dumps(
            {
                "self_state": {
                    "inventory_items": [
                        {"item_id": "PRODUCT_1", "item_type": "product", "quantity": 961}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_6"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            },
            {
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_5"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            },
            {
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_7"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            },
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=15,
        action_dir=action_dir,
    )

    actions = [item["action"] for item in normalized]
    assert actions[0]["action_name"] == "accept_order"
    assert actions[0]["action_param"]["order_id"] == "SALE_ORDER_6"
    assert actions[1]["action_name"] == "reject_order"
    assert actions[1]["action_param"]["order_id"] == "SALE_ORDER_5"
    assert actions[2]["action_name"] == "reject_order"
    assert actions[2]["action_param"]["order_id"] == "SALE_ORDER_7"
    assert "reserved_this_round=780" in actions[1]["action_param"]["reason"]


def test_single_case_sales_accepts_are_reordered_by_order_quality_before_guard(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {"action_constraints": {"allow_accept_order": True}},
        "agent_decision_brief": {
            "order_quality_reviews": [
                {
                    "order_id": "SALE_ORDER_11",
                    "quantity": 620,
                    "estimated_unit_margin": 20,
                    "estimated_gross_margin": 12400,
                    "breach_penalty_exposure": 186000,
                    "quantity_to_uncommitted_inventory_ratio": 1.18,
                    "risk_flags": [
                        "low_margin_vs_reference_cost",
                        "penalty_exposure_exceeds_gross_margin",
                        "large_commitment_vs_uncommitted_inventory",
                    ],
                },
                {
                    "order_id": "SALE_ORDER_12",
                    "quantity": 180,
                    "estimated_unit_margin": 480,
                    "estimated_gross_margin": 86400,
                    "breach_penalty_exposure": 37800,
                    "quantity_to_uncommitted_inventory_ratio": 0.34,
                    "risk_flags": [],
                },
            ]
        },
        "self_state": {
            "sales_orders": {
                "available": [
                    {
                        "order_id": "SALE_ORDER_11",
                        "status": "available",
                        "product_id": "PRODUCT_1",
                        "quantity": 620,
                        "unit_price": 520,
                        "total_amount": 322400,
                        "delivery_deadline": 19,
                    },
                    {
                        "order_id": "SALE_ORDER_12",
                        "status": "available",
                        "product_id": "PRODUCT_1",
                        "quantity": 180,
                        "unit_price": 980,
                        "total_amount": 176400,
                        "delivery_deadline": 19,
                    },
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_quality"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day17"
    )
    inventory_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "inventory"
        / "day17"
    )
    action_dir.mkdir(parents=True)
    inventory_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_04_staff_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    (inventory_dir / "inventory.json").write_text(
        json.dumps(
            {
                "self_state": {
                    "inventory_items": [
                        {"item_id": "PRODUCT_1", "item_type": "product", "quantity": 785}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_11"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            },
            {
                "action": {
                    "action_name": "accept_order",
                    "action_param": {"order_id": "SALE_ORDER_12"},
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            },
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=17,
        action_dir=action_dir,
    )

    actions = [item["action"] for item in normalized]
    assert actions[0]["action_name"] == "accept_order"
    assert actions[0]["action_param"]["order_id"] == "SALE_ORDER_12"
    assert actions[1]["action_name"] == "reject_order"
    assert actions[1]["action_param"]["order_id"] == "SALE_ORDER_11"
    assert "reserved_this_round=180" in actions[1]["action_param"]["reason"]


def test_single_case_staff_shortage_recovers_production_from_pass_after_hr_recruitment(
    monkeypatch,
    tmp_path,
):
    production_state = {
        "policy_context": {"action_constraints": {"allow_create_production_plan": True}},
        "self_state": {
            "production_lines": {
                "total": 2,
                "available_capacity": 1000,
                "by_status": {"idle": 2, "working": 0, "under_construction": 0},
            },
            "recovery_guard": {
                "candidates": [
                    {
                        "product_id": "PRODUCT_1",
                        "material_feasible_quantity": 15950,
                        "recommended_plan_quantity": 750,
                        "recommended_daily_capacity": 750,
                        "confirmed_order_backlog_quantity": 1800,
                        "blocking_reasons": [],
                        "should_recover_now": True,
                    }
                ]
            },
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: production_state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "production"
        / "day11"
    )
    hr_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "hr"
        / "day11"
    )
    action_dir.mkdir(parents=True)
    hr_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_04_staff_shortage",
                    "primary_issue": "staff_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    (hr_dir / "hr.json").write_text(
        json.dumps(
            {
                "self_state": {
                    "employees": [
                        {"department": "PRODUCTION", "count": 3, "allocated": 0}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "action_pass",
                    "action_param": "allocated=0 means no worker",
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=11,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"] == {
        "product_id": "PRODUCT_1",
        "quantity": 750,
        "daily_capacity": 750,
    }
    assert "bounded PRODUCT_1 recovery plan" in normalized[0]["action_reason"]


def test_single_case_agent_procurement_caps_purchase_to_top_tier_limit(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {
                "allow_external_purchase_order": True,
                "top_tier_credit_policy_enabled": True,
            }
        },
        "self_state": {
            "top_tier_supply_guard": {
                "enabled": True,
                "max_single_order_amount": 22500,
                "available_credit": 50000,
                "external_logistics_cost_multiplier": 0.1,
            },
            "suppliers": [
                {
                    "supplier_id": "SUP_TOP",
                    "supplier_name": "TopTierSupplier",
                    "supplier_type": "top_tier_external",
                    "materials": {
                        "MATERIAL_1": {
                            "unit_price": 10,
                            "min_order_quantity": 0,
                        }
                    },
                }
            ],
            "materials_suppliers_matrix": {
                "MATERIAL_1": [
                    {
                        "supplier_id": "SUP_TOP",
                        "supplier_name": "TopTierSupplier",
                        "supplier_type": "top_tier_external",
                        "unit_price": 10,
                        "min_order_quantity": 0,
                    }
                ]
            },
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "procurement"
        / "day10"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_02_material_shortage",
                    "primary_issue": "material_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_purchase_order",
                    "action_param": {
                        "material_id": "MATERIAL_1",
                        "quantity": 5520,
                        "supplier_id": "SUP_TOP",
                        "logistics_mode": "road",
                    },
                },
                "module_type": "ProcurementManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="procurement",
        enterprise_name="Manufacturer",
        round_id=10,
        action_dir=action_dir,
    )

    action_param = normalized[0]["action"]["action_param"]
    assert action_param["supplier_name"] == "TopTierSupplier"
    assert action_param["logistics_mode"] == "road"
    assert action_param["quantity"] < 5520
    estimated_total = action_param["quantity"] * 10 + (100 + action_param["quantity"] * 2) * 0.1
    assert estimated_total <= 22500


def test_single_case_material_shortage_blocks_repeated_covered_purchase(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {"allow_external_purchase_order": True},
        },
        "self_state": {
            "orders": {
                "pending": [
                    {
                        "order_id": "PROCUREMENT_ORDER_1",
                        "material_id": "MATERIAL_1",
                        "quantity": 2623,
                        "status": "pending",
                    }
                ]
            },
            "replenishment": {"pending_by_material": {"MATERIAL_1": 2623}},
            "operational_summary": {
                "inventory_position_by_material": {
                    "MATERIAL_1": {
                        "on_hand": 354,
                        "incoming": 2623,
                        "inventory_position": 2977,
                    }
                }
            },
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "procurement"
        / "day11"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_02_material_shortage",
                    "primary_issue": "material_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_purchase_order",
                    "action_param": {
                        "material_id": "MATERIAL_1",
                        "quantity": 2623,
                        "supplier_name": "Supplier_A",
                        "logistics_mode": "road",
                    },
                },
                "module_type": "ProcurementManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="procurement",
        enterprise_name="Manufacturer",
        round_id=11,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "action_pass"
    assert "Do not duplicate create_purchase_order" in normalized[0]["action_reason"]


def test_single_case_material_shortage_allows_purchase_after_prior_order_consumed(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {"allow_external_purchase_order": True},
        },
        "self_state": {
            "orders": {
                "received": [
                    {
                        "order_id": "PROCUREMENT_ORDER_1",
                        "material_id": "MATERIAL_1",
                        "quantity": 3546,
                        "status": "received",
                    }
                ]
            },
            "replenishment": {"pending_by_material": {"MATERIAL_1": 0}},
            "operational_summary": {
                "inventory_position_by_material": {
                    "MATERIAL_1": {
                        "on_hand": 0,
                        "incoming": 0,
                        "inventory_position": 0,
                    }
                }
            },
            "suppliers": [
                {
                    "supplier_name": "Supplier_Fast",
                    "supplier_type": "external",
                    "materials": {
                        "MATERIAL_1": {
                            "unit_price": 13,
                            "min_order_quantity": 300,
                        }
                    },
                }
            ],
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "procurement"
        / "day16"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_02_material_shortage",
                    "primary_issue": "material_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "create_purchase_order",
                    "action_param": {
                        "material_id": "MATERIAL_1",
                        "quantity": 400,
                        "supplier_name": "Supplier_Fast",
                        "logistics_mode": "rail",
                    },
                },
                "module_type": "ProcurementManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="procurement",
        enterprise_name="Manufacturer",
        round_id=16,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_purchase_order"
    assert action["action_param"]["supplier_name"] == "Supplier_Fast"
    assert action["action_param"]["logistics_mode"] == "rail"


def test_single_case_material_shortage_recovers_partial_production_from_pass(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {"allow_create_production_plan": True},
        },
        "self_state": {
            "production_lines": {
                "total_capacity": 500,
                "available_capacity": 500,
            },
            "recovery_guard": {
                "candidates": [
                    {
                        "product_id": "PRODUCT_1",
                        "material_feasible_quantity": 1177,
                        "recommended_plan_quantity": 0,
                        "recommended_daily_capacity": 0,
                        "blocking_reasons": [],
                        "raw_material_snapshots": {"MATERIAL_1": {"on_hand": 2354}},
                    }
                ]
            },
            "margin_guard": {
                "candidates": [
                    {
                        "product_id": "PRODUCT_1",
                        "guard_level": "hard_blocked",
                        "estimated_sale_unit_price": 0,
                        "allow_service_recovery": False,
                    }
                ]
            },
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "production"
        / "day14"
    )
    action_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_02_material_shortage",
                    "primary_issue": "material_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "action_pass",
                    "action_param": "wait until full backlog coverage",
                },
                "module_type": "ProductionManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="production",
        enterprise_name="Manufacturer",
        round_id=14,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"] == {
        "product_id": "PRODUCT_1",
        "quantity": 500,
        "daily_capacity": 500,
    }


def test_single_case_material_shortage_recovers_safe_sales_from_pass(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {"allow_accept_order": True},
        },
        "self_state": {
            "sales_orders": {
                "available": [
                    {
                        "order_id": "SALE_ORDER_BIG",
                        "status": "available",
                        "product_id": "PRODUCT_1",
                        "quantity": 600,
                        "total_amount": 300000,
                    },
                    {
                        "order_id": "SALE_ORDER_SMALL",
                        "status": "available",
                        "product_id": "PRODUCT_1",
                        "quantity": 50,
                        "total_amount": 25000,
                    },
                ]
            }
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)

    run_dir = tmp_path / "run_single_case_01"
    action_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "sales"
        / "day14"
    )
    inventory_dir = (
        run_dir
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "inventory"
        / "day14"
    )
    action_dir.mkdir(parents=True)
    inventory_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "orchestrator": "single_enterprise",
                "enterprise_ids": ["Manufacturer"],
                "single_enterprise_case": {
                    "enabled": True,
                    "case_id": "single_case_02_material_shortage",
                    "primary_issue": "material_shortage",
                    "handoff_day": 10,
                    "prewarm_rounds": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    (inventory_dir / "inventory.json").write_text(
        json.dumps(
            {
                "self_state": {
                    "inventory_items": [
                        {
                            "item_id": "PRODUCT_1",
                            "item_type": "product",
                            "quantity": 100,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "action_pass",
                    "action_param": "wait until all orders are covered",
                },
                "module_type": "SalesManager",
                "executor_id": "Manufacturer",
            }
        ],
        department="sales",
        enterprise_name="Manufacturer",
        round_id=14,
        action_dir=action_dir,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "accept_order"
    assert action["action_param"] == {"order_id": "SALE_ORDER_SMALL"}


def test_pending_proposal_helpers_exclude_expired_proposals():
    state = {
        "self_state": {
            "proposals_list": [
                {"proposal_id": "proposal_pending", "product_id": "beer", "status": "pending"},
                {"proposal_id": "proposal_expired", "product_id": "beer", "status": "expired"},
            ]
        }
    }

    assert StaticUtils._get_pending_proposal_ids(state) == {"proposal_pending"}
    assert StaticUtils._get_pending_proposal_material_ids(state) == {"proposal_pending": "beer"}


def test_procurement_workflow_filters_items_outside_purchasable_set(monkeypatch):
    state = {
        "policy_context": {
            "action_constraints": {
                "allow_create_replenishment_order": True,
                "allow_external_purchase_order": True,
            }
        },
        "self_state": {
            "purchasable_materials_idList": ["beer"],
            "operational_summary": {
                "inventory_position_by_material": {
                    "beer": {"item_type": "product"},
                    "smart_device": {"item_type": None},
                }
            },
            "proposals_list": [
                {"proposal_id": "proposal_beer", "product_id": "beer", "status": "pending"},
                {"proposal_id": "proposal_sensor", "product_id": "smart_device", "status": "pending"},
            ],
        },
    }
    monkeypatch.setattr(
        StaticUtils,
        "_load_department_state_snapshot",
        lambda **_: state,
    )
    monkeypatch.setattr(
        StaticUtils,
        "_get_already_responded_proposal_ids",
        lambda *_: set(),
    )

    payload = [
        {
            "action": {
                "action_name": "create_replenishment_order",
                "action_param": {"material_id": "smart_device"},
            },
            "module_type": "ProcurementManager",
            "executor_id": "Retailer",
        },
        {
            "action": {
                "action_name": "create_replenishment_order",
                "action_param": {"material_id": "beer"},
            },
            "module_type": "ProcurementManager",
            "executor_id": "Retailer",
        },
        {
            "action": {
                "action_name": "accept_proposal_order",
                "action_param": {"proposal_id": "proposal_sensor"},
            },
            "module_type": "ProcurementManager",
            "executor_id": "Retailer",
        },
        {
            "action": {
                "action_name": "accept_proposal_order",
                "action_param": {"proposal_id": "proposal_beer"},
            },
            "module_type": "ProcurementManager",
            "executor_id": "Retailer",
        },
    ]

    normalized = StaticUtils._normalize_department_workflow(
        payload,
        department="procurement",
        enterprise_name="Retailer",
        round_id=5,
    )

    actions = [item["action"] for item in normalized]
    assert actions == [
        {"action_name": "create_replenishment_order", "action_param": {"material_id": "beer"}},
        {"action_name": "accept_proposal_order", "action_param": {"proposal_id": "proposal_beer"}},
    ]


def test_trade_workflow_filters_action_not_in_current_actionable_snapshot(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {}},
        "self_state": {
            "proposals_list": [
                {"proposal_id": "proposal_old", "product_id": "beer", "status": "confirmed"},
            ],
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)
    monkeypatch.setattr(StaticUtils, "_get_exchange_proposal_index", lambda *_: {})
    monkeypatch.setattr(StaticUtils, "_get_exchange_buy_request_index", lambda *_: {})
    monkeypatch.setattr(StaticUtils, "_get_already_responded_proposal_ids", lambda *_: set())

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "accept_proposal_order",
                    "action_param": {"proposal_id": "proposal_old"},
                },
                "module_type": "SalesManager",
                "executor_id": "Distributor",
                "action_reason": "stale proposal should not be executable",
            }
        ],
        department="sales",
        enterprise_name="Distributor",
        round_id=8,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"


def test_trade_workflow_filters_superseded_buy_request(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {}},
        "self_state": {
            "proposals_list": [
                {
                    "proposal_id": "proposal_pending",
                    "product_id": "beer",
                    "status": "pending",
                    "buy_request_id": "buy_superseded",
                },
            ],
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)
    monkeypatch.setattr(
        StaticUtils,
        "_get_exchange_proposal_index",
        lambda *_: {
            "proposal_pending": {
                "proposal_id": "proposal_pending",
                "product_id": "beer",
                "status": "pending",
                "buy_request_id": "buy_superseded",
            }
        },
    )
    monkeypatch.setattr(
        StaticUtils,
        "_get_exchange_buy_request_index",
        lambda *_: {
            "buy_superseded": {
                "request_id": "buy_superseded",
                "lifecycle_status": "superseded",
            }
        },
    )
    monkeypatch.setattr(StaticUtils, "_get_already_responded_proposal_ids", lambda *_: set())

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "accept_proposal_order",
                    "action_param": {"proposal_id": "proposal_pending"},
                },
                "module_type": "SalesManager",
                "executor_id": "Distributor",
                "action_reason": "buy request has already been superseded",
            }
        ],
        department="sales",
        enterprise_name="Distributor",
        round_id=8,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"


def test_trade_workflow_filters_exchange_expired_even_when_state_is_pending(monkeypatch):
    state = {
        "policy_context": {"action_constraints": {}},
        "self_state": {
            "proposals_list": [
                {
                    "proposal_id": "proposal_expired_in_exchange",
                    "product_id": "beer",
                    "status": "pending",
                    "buy_request_id": "buy_active",
                },
            ],
        },
    }
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **_: state)
    monkeypatch.setattr(
        StaticUtils,
        "_get_exchange_proposal_index",
        lambda *_: {
            "proposal_expired_in_exchange": {
                "proposal_id": "proposal_expired_in_exchange",
                "product_id": "beer",
                "status": "expired",
                "buy_request_id": "buy_active",
            }
        },
    )
    monkeypatch.setattr(StaticUtils, "_get_exchange_buy_request_index", lambda *_: {})
    monkeypatch.setattr(StaticUtils, "_get_already_responded_proposal_ids", lambda *_: set())

    normalized = StaticUtils._normalize_department_workflow(
        [
            {
                "action": {
                    "action_name": "accept_proposal_order",
                    "action_param": {"proposal_id": "proposal_expired_in_exchange"},
                },
                "module_type": "SalesManager",
                "executor_id": "Distributor",
                "action_reason": "state snapshot was stale",
            }
        ],
        department="sales",
        enterprise_name="Distributor",
        round_id=22,
    )

    assert normalized[0]["action"]["action_name"] == "action_pass"
