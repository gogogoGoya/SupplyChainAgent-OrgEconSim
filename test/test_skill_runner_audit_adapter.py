import json
from pathlib import Path

from agent.data_config import DepartmentSpec, EnterpriseSpec
from agent.multi_tenant_utils import MultiTenantUtils
from agent.skill_runner import SkillRunner, SkillRunSpec


def _runner(tmp_path):
    runner = SkillRunner.__new__(SkillRunner)
    runner.enterprise_spec = EnterpriseSpec(
        enterprise_id="Enterprise_A",
        enterprise_name="Enterprise_A",
    )
    runner.client_dir = tmp_path
    runner.workspace_dir = tmp_path / "workspace_multi"
    return runner


def test_e1_analysis_requires_auditable_external_response(tmp_path):
    runner = _runner(tmp_path)
    workspace = runner.workspace_dir
    workspace.mkdir(parents=True)
    (workspace / "run_meta.json").write_text(
        json.dumps({
            "orchestrator": "multi_enterprise",
            "scenario_id": "long_horizon_evolution",
            "scenario_config": {
                "meta": {"scenario_id": "long_horizon_evolution"},
                "runtime_injection": {
                    "long_run_experiment_policy": {
                        "enabled": True,
                        "agent_context_compaction": {"enabled": True},
                    }
                },
            },
        }),
        encoding="utf-8",
    )
    compact_path = runner._compact_long_run_analyst_observation_path(40)
    compact_path.parent.mkdir(parents=True)
    compact_path.write_text(
        json.dumps({
            "external_change_review": {
                "latest_event": {
                    "event_id": "upstream_material_price_surge",
                },
                "active_deviating_factors": ["external_material_price"],
            }
        }),
        encoding="utf-8",
    )
    runner._active_analyst_round_id = 40
    payload = {
        "enterprise_name": "Enterprise_A",
        "round_id": 40,
        "enterprise_summarys": "raw material Cost changes need to be reviewed in conjunction with cash and inventory.",
        "department_targets": {
            "sales": {
                "target": "Review the order margin",
                "evaluation": "Monitor gross margin.",
                "reason": "Cost changes",
            },
            "procurement": {
                "target": "Review of procurement tempo",
                "evaluation": "Observation unit cost and cash",
                "reason": "Inventory coverage",
            },
        },
    }

    assert runner._is_valid_analysis_payload(payload) is False
    payload["external_environment_response"] = {
        "observed_event_ids": ["upstream_material_price_surge"],
        "active_factor_assessment": {
            "external_material_price": {
                "observed_value": 1.25,
                "business_implication": "replenishment Increase in unit cost and cash occupancy",
            }
        },
        "impact_assessment": "Cost pressure measured in conjunction with inventory coverage",
        "response_strategy": "Adjust the procurement tempo to cover gaps and marginal conditions",
        "review_criteria": "Unit costs, cash and service rates for the next two rounds",
    }
    assert runner._is_valid_analysis_payload(payload) is True


def test_e1_skill_spec_uses_agent_projection_without_rewriting_full_state(tmp_path):
    runner = _runner(tmp_path)
    workspace = runner.workspace_dir
    state_path = (
        workspace
        / "enterprises"
        / "Enterprise_A"
        / "department"
        / "procurement"
        / "day91"
        / "procurement.json"
    )
    blackboard_path = (
        workspace
        / "enterprises"
        / "Enterprise_A"
        / "department"
        / "blackboard"
        / "day91"
        / "blackboard.json"
    )
    state_path.parent.mkdir(parents=True)
    blackboard_path.parent.mkdir(parents=True)
    state_payload = {
        "department": "procurement",
        "round_id": 91,
        "agent_decision_brief": {},
        "policy_context": {},
        "simulation_context": {"total_steps": 200},
        "self_state": {
            "orders": {
                "pending": [{"order_id": "OPEN"}],
                "received": [{"order_id": f"DONE_{index}"} for index in range(60)],
            },
            "replenishment": {"history": list(range(60))},
        },
    }
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")
    blackboard_path.write_text(
        json.dumps({
            "round_id": 91,
            "simulation_context": {"total_steps": 200},
            "policy_context_by_department": {"procurement": {"large": "x" * 5000}},
            "departments": {"procurement": {"incoming": 10}},
        }),
        encoding="utf-8",
    )
    workspace.mkdir(exist_ok=True)
    (workspace / "run_meta.json").write_text(
        json.dumps({
            "orchestrator": "multi_enterprise",
            "scenario_id": "long_horizon_evolution",
            "scenario_config": {
                "meta": {"scenario_id": "long_horizon_evolution"},
                "runtime_injection": {
                    "long_run_experiment_policy": {
                        "enabled": True,
                        "agent_context_compaction": {
                            "enabled": True,
                            "recent_history_items": 4,
                            "max_open_records": 48,
                        },
                    }
                },
            },
        }),
        encoding="utf-8",
    )
    original_state_text = state_path.read_text(encoding="utf-8")

    spec = runner._build_skill_run_spec(
        DepartmentSpec(
            dept_id="procurement",
            role="Procurement",
            name="Procurement",
            skill_name="procurement",
            type="Agent",
        ),
        91,
    )

    assert spec.compact_context is True
    assert Path(spec.state_path) != state_path
    assert "projections/agent_context/day91" in spec.state_path
    projection = json.loads(Path(spec.state_path).read_text(encoding="utf-8"))
    assert projection["self_state"]["orders"]["pending"][0]["order_id"] == "OPEN"
    assert projection["self_state"]["orders"]["history_summary"]["received"] == 60
    compact_blackboard = json.loads(Path(spec.blackboard_path).read_text(encoding="utf-8"))
    assert "policy_context_by_department" not in compact_blackboard
    assert state_path.read_text(encoding="utf-8") == original_state_text


def test_e1_analyst_uses_bounded_observation_without_rewriting_archive(tmp_path):
    runner = _runner(tmp_path)
    workspace = runner.workspace_dir
    workspace.mkdir(parents=True)
    (workspace / "run_meta.json").write_text(
        json.dumps({
            "orchestrator": "multi_enterprise",
            "scenario_id": "long_horizon_evolution",
            "scenario_config": {
                "meta": {"scenario_id": "long_horizon_evolution"},
                "runtime_injection": {
                    "long_run_experiment_policy": {
                        "enabled": True,
                        "agent_context_compaction": {
                            "enabled": True,
                            "recent_history_items": 4,
                            "max_open_records": 48,
                        },
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    raw_path = runner._raw_analyst_observation_path(92)
    raw_path.parent.mkdir(parents=True)
    raw_payload = {
        "enterprise_id": "Enterprise_A",
        "finance": {"cash": 500000, "total_revenue": 1000, "total_cost": 800},
        "production": {
            "production_plans": {
                "in_progress": [{"plan_id": "OPEN_PLAN"}],
                "completed": [{"plan_id": f"DONE_PLAN_{index}"} for index in range(100)],
            },
        },
        "procurement": {
            "orders": {
                "pending": [{"order_id": "OPEN_PURCHASE"}],
                "received": [{"order_id": f"OLD_PO_{index}"} for index in range(100)],
            },
            "proposal_history": [
                {"proposal_id": f"OLD_BUY_PROPOSAL_{index}"}
                for index in range(100)
            ],
            "proposals_list": [{"proposal_id": "OPEN_BUY_PROPOSAL"}],
            "replenishment": {"history": list(range(100))},
        },
        "inventory": {"inventory_items": [{"item_id": "beer", "quantity": 80}]},
        "sales": {
            "sales_orders": {
                "available": [{"order_id": "OPEN_SALE", "delivery_deadline": 100}],
                "completed": [{"order_id": f"OLD_SO_{index}"} for index in range(100)],
            },
            "proposal_history": [
                {"proposal_id": f"OLD_SELL_PROPOSAL_{index}"}
                for index in range(100)
            ],
            "proposals_list": [{"proposal_id": "OPEN_SELL_PROPOSAL"}],
            "demand_backlog": {"history": list(range(100))},
        },
        "hr": {"employees": [{"employee_id": f"E{index}"} for index in range(80)]},
        "simulation_context": {
            "total_steps": 200,
            "external_environment": {"current_turn": 92, "active_factors": {}},
            "irrelevant_history": ["x" * 1000 for _ in range(100)],
        },
        "enterprise_policy_context": {
            "scenario_id": "long_horizon_evolution",
            "enabled_modes": ["long_run_experiment"],
        },
        "policy_context_by_department": {
            "finance": {
                "active_modes": {"long_run_experiment": True},
                "decision_weights": {"net_profit": 0.35},
                "priority_rules": ["protect_cash"],
                "large_duplicate": "x" * 100000,
            },
        },
    }
    raw_path.write_text(json.dumps(raw_payload), encoding="utf-8")
    original_raw = raw_path.read_text(encoding="utf-8")
    history_path = runner._history_projection_path(92)
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps({
            "source_day_range": [80, 91],
            "trends": {"finance": {"net_profit": {"direction": "up"}}},
            "days": [
                {"round_id": day, "department_metrics": {"finance": {"cash": day}}}
                for day in range(80, 92)
            ],
        }),
        encoding="utf-8",
    )

    spec = runner._build_analyst_run_spec(92)
    compact = json.loads(Path(spec.observation_path).read_text(encoding="utf-8"))
    serialized = json.dumps(compact, ensure_ascii=False)
    prompt = runner._build_analyst_launch_prompt(spec, 92)

    assert spec.compact_observation_kind == "long_horizon_e1"
    assert compact["sales"]["sales_orders"]["available"][0]["order_id"] == "OPEN_SALE"
    assert compact["sales"]["sales_orders"]["history_summary"]["completed"] == 100
    assert compact["procurement"]["orders"]["history_summary"]["received"] == 100
    assert compact["production"]["production_plans"]["history_summary"]["completed"] == 100
    assert len(compact["recent_history"]["latest_days"]) == 4
    assert "OLD_SO_0" not in serialized
    assert "large_duplicate" not in serialized
    assert "irrelevant_history" not in serialized
    assert "Bounded long-horizon input" in prompt
    assert "do not open raw observation_day92.txt" in prompt
    assert len(serialized) < len(original_raw)
    assert raw_path.read_text(encoding="utf-8") == original_raw


def test_skill_runner_reads_profiles_from_run_metadata(tmp_path):
    runner = _runner(tmp_path)
    run_meta = tmp_path / "workspace_multi" / "run_meta.json"
    run_meta.parent.mkdir(parents=True)
    run_meta.write_text(
        json.dumps(
            {
                "run_id": "run-profile",
                "scenario_id": "beer_game",
                "scenario_config": {},
                "integration_profiles": {
                    "simulation": {"profile": "multi_enterprise"},
                    "capabilities": {
                        "analysis": {
                            "profile": "none",
                            "history_days": 5,
                        },
                        "coordination": {
                            "blackboard_enabled": False,
                            "two_phase_enabled": False,
                            "communication_validation": "off",
                        },
                        "skill": {
                            "context_validation": "off",
                            "output_validation": "warn",
                            "max_retries": 2,
                            "audit_enabled": True,
                        },
                        "departments": {"finance_enabled": False},
                    },
                    "infrastructure": {
                        "storage": {"profile": "filesystem"},
                        "operations": {"enabled": False},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    contract = runner._runtime_contract()

    assert contract["run_id"] == "run-profile"
    assert runner._skill_profile()["output_validation"] == "warn"


def test_skill_runner_writes_raw_and_normalized_audit_artifacts(tmp_path):
    runner = _runner(tmp_path)
    workspace = tmp_path / "workspace_multi"
    workspace.mkdir()
    (workspace / "run_meta.json").write_text(
        json.dumps(
            {
                "run_id": "run-audit",
                "scenario_id": "beer_game",
                "scenario_config": {},
            }
        ),
        encoding="utf-8",
    )
    output_path = (
        workspace
        / "enterprises"
        / "Enterprise_A"
        / "department"
        / "sales"
        / "day0"
        / "sales_action.json"
    )
    output_path.parent.mkdir(parents=True)
    output_path.write_text(
        json.dumps(
            [
                {
                    "action": {
                        "action_name": "action_pass",
                        "action_param": {},
                    },
                    "action_reason": "No valid order.",
                    "module_type": "SalesManager",
                    "executor_id": "Enterprise_A",
                }
            ]
        ),
        encoding="utf-8",
    )

    audit_path = Path(
        runner._write_skill_execution_audit(
            role="Sales",
            department="sales",
            round_id=0,
            output_file_path=str(output_path),
            all_messages=[{"role": "Sales", "content": "raw transcript"}],
            attempts=1,
            success=True,
            fallback_used=False,
            elapsed_seconds=1.25,
            context_audit={
                "ok": True,
                "disallowed_policy_names": [],
                "missing_files": [],
            },
            execution_result={"status": "success"},
            phase="decision",
        )
    )
    payload = json.loads(audit_path.read_text(encoding="utf-8"))

    assert payload["run_id"] == "run-audit"
    assert payload["normalized_envelope"]["status"] == "ok"
    assert Path(payload["raw_messages_artifacts"][0]).exists()


def test_skill_runner_rejects_placeholder_analysis_payload(tmp_path):
    runner = _runner(tmp_path)
    runner.enterprise_spec = EnterpriseSpec(
        enterprise_id="Manufacturer",
        enterprise_name="Manufacturer",
    )

    assert not runner._is_valid_analysis_payload(
        {
            "enterprise_name": "string",
            "round_id": "string",
            "enterprise_summarys": "string",
            "department_targets": {
                "production": {"target": "string", "evaluation": "string", "reason": "string"},
                "sales": {"target": "string", "evaluation": "string", "reason": "string"},
                "procurement": {"target": "string", "evaluation": "string", "reason": "string"},
            },
        }
    )


def test_skill_runner_accepts_real_analysis_payload(tmp_path):
    runner = _runner(tmp_path)
    runner.enterprise_spec = EnterpriseSpec(
        enterprise_id="Manufacturer",
        enterprise_name="Manufacturer",
    )

    assert runner._is_valid_analysis_payload(
        {
            "enterprise_name": "Manufacturer",
            "round_id": 10,
            "enterprise_summarys": "There is a backlog of orders, and raw material is currently insufficient in stock and the cash remains healthy.",
            "department_targets": {
                "production": {
                    "target": "Arrange for small-volume production, pending the arrival of supplements.",
                    "evaluation": "The production schedule does not exceed the amount supported by raw material.",
                    "reason": "The shortfall raw material is the main bottleneck.",
                },
                "sales": {
                    "target": "Stop accepting new orders and avoid expanding backlogs.",
                    "evaluation": "Add accepted order to 0 for this round.",
                    "reason": "Existing orders are sufficient to expose bottlenecks.",
                },
                "procurement": {
                    "target": "Supplement MATERIAL_1 at top-tier.",
                    "evaluation": "The value of the purchase order does not exceed the maximum amount of a single letter of credit.",
                    "reason": "There is a shortage of material and a healthy cash.",
                },
            },
        }
    )


def test_e1_uses_bounded_dynamic_timeouts(tmp_path):
    runner = _runner(tmp_path)
    runner.workspace_dir.mkdir(parents=True)
    (runner.workspace_dir / "run_meta.json").write_text(
        json.dumps({
            "orchestrator": "multi_enterprise",
            "scenario_id": "long_horizon_evolution",
            "scenario_config": {
                "meta": {"scenario_id": "long_horizon_evolution"},
                "runtime_injection": {
                    "long_run_experiment_policy": {
                        "enabled": True,
                        "event_turns": [40],
                        "event_follow_up_offsets": [0, 2, 5],
                        "agent_timeout_policy": {
                            "enabled": True,
                            "department_first_attempt_seconds": 150,
                            "department_retry_seconds": 90,
                            "analyst_first_attempt_seconds": 180,
                            "analyst_event_attempt_seconds": 210,
                            "analyst_retry_seconds": 120,
                            "hard_max_seconds": 240,
                        },
                    }
                },
            },
        }),
        encoding="utf-8",
    )

    assert runner._long_run_agent_timeout_seconds(
        role="Procurement", round_id=91, retry_index=0, default_seconds=120
    ) == 150
    assert runner._long_run_agent_timeout_seconds(
        role="Procurement", round_id=91, retry_index=1, default_seconds=120
    ) == 90
    assert runner._long_run_agent_timeout_seconds(
        role="Analyst",
        round_id=42,
        retry_index=0,
        default_seconds=120,
        analyst=True,
    ) == 210


def test_e1_state_fallback_keeps_action_and_hides_scripted_label(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    runner.scripted_rule_runner = type(
        "FallbackRunner",
        (),
        {
            "_build_department_action": lambda self, department, round_id, need_trade: [
                {
                    "action": {
                        "action_name": "create_purchase_demand",
                        "action_param": {
                            "material_id": "beer",
                            "quantity": 70,
                            "logistics_mode": "road",
                        },
                    },
                    "action_reason": "Standard algorithm: scripted rules based on stock gap replenishment.",
                    "module_type": "ProcurementManager",
                    "executor_id": "Enterprise_A",
                }
            ]
        },
    )()
    runner._long_run_agent_failure_continuation_enabled = lambda: True
    runner._long_run_experiment_policy = lambda: {
        "state_driven_fallback_policy": {
            "enabled": True,
            "visible_reason_prefix": "Think over time. Call the bottom logic link.",
        }
    }
    monkeypatch.setattr(
        MultiTenantUtils,
        "_normalize_action_file_if_possible",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        MultiTenantUtils,
        "_is_valid_action_file",
        lambda *args, **kwargs: True,
    )
    output_path = tmp_path / "procurement_action.json"
    spec = SkillRunSpec(
        role="Procurement",
        name="Procurement",
        skill_name="buyer",
        skill_args="",
        output_file_name="procurement_action.json",
        output_file_path=str(output_path),
        analysis_path="",
        state_path="",
        blackboard_path="",
        trade_decision_card_path=None,
        communication_file_path="",
        action_template_dir="",
    )

    assert runner._write_long_run_state_driven_fallback_file(
        spec,
        "procurement",
        91,
        need_trade=False,
    ) is True
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert payload[0]["action"]["action_name"] == "create_purchase_demand"
    assert "Think over time. Call the bottom logic link." in payload[0]["action_reason"]
    assert "scripted" not in serialized
    assert "Standard algorithm" not in serialized
