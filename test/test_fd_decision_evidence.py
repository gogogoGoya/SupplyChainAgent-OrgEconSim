from copy import deepcopy
import json
from pathlib import Path

import pytest

from core.fd_decision_evidence import describe_operating_evidence


def test_overlapping_orders_and_counters_do_not_generate_action_answers():
    payload = {"sales_orders": {
        "accepted": [{"order_id": "a", "quantity": 80, "delivery_deadline": 20}],
        "backlog": [{"order_id": "a", "quantity": 80, "delivery_deadline": 20}],
        "breached": [{"order_id": "b", "quantity": 360, "delivery_deadline": 12}],
    }, "recovery_guard": {"candidates": [{"product_id": "p", "on_hand": 881,
        "confirmed_order_backlog_quantity": 520, "stale_backlog_quantity": 360}]}}
    before = deepcopy(payload)
    result = describe_operating_evidence(payload, 15)
    assert payload == before
    assert result["action_recommendations"] is False
    orders, stock = result["facts"]
    assert len(orders["eligible_committed_records"]) == 1
    assert orders["duplicate_ids"] == ["a"]
    assert orders["excluded_quantity"] == 360
    assert stock["values"]["on_hand"] == 881
    assert "net_quantity" not in stock
    assert stock["reserved_or_unusable_stock"].startswith("unknown")


def test_remaining_wip_is_not_original_plan_quantity():
    result = describe_operating_evidence({"production_plans": {"in_progress": [
        {"plan_id": "p", "status": "in_progress", "quantity": 500,
         "progress": {"quantity_produced": 300}, "completion_time": 16},
        {"plan_id": "q", "status": "in_progress", "quantity": 500},
    ]}}, 15)
    assert [row["remaining_quantity"] for row in result["facts"]] == [200, None]


def test_no_shared_input_cannot_gain_hidden_operating_facts():
    result = describe_operating_evidence({"departments": {}, "coordination_available": False}, 15)
    assert result["facts"] == []


def test_zero_staff_is_not_proof_of_business_failure():
    result = describe_operating_evidence({"department_staffing": {"HR": {"count": 0}}}, 13)
    assert result["facts"][0]["zero_count_is_not_execution_failure"] is True
    assert result["facts"][0]["release_time"].startswith("unknown")


