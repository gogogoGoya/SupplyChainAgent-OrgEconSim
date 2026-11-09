"""Numbered FD4 constructions; economic execution rules are unchanged."""

from copy import deepcopy


FD4_INSTANCE_SEQUENCE = 2


def fd4_instance(config, sequence=FD4_INSTANCE_SEQUENCE):
    """Materialize a numbered construction without mutating its source."""
    if type(sequence) is not int or sequence not in {1, 2}:
        raise ValueError("FD4 instance_sequence must be 1 or 2")
    result = deepcopy(config)
    case = result["runtime_injection"]["single_enterprise_case_policy"]
    if case.get("case_id") != "single_case_04_staff_shortage":
        raise ValueError("FD4 construction requires the staffing preset")
    previous = result.get("fd_validation_instance") or {}
    if sequence == 1:
        if previous.get("instance_sequence", 1) != 1 or previous.get("instance_id") == "FD4-L1-development":
            raise ValueError("Construct instance 01 from its preset, not from instance 02")
        return result
    return persistent_labor_instance(result)


def persistent_labor_instance(config):
    """Construct a zero-worker handover using legal prewarm actions.

    Removing the day9 bridge plan avoids creating a plan without workers.
    All order, material, cash and line settings otherwise remain unchanged.
    This is a separate instance, not a replacement for historical FD4.
    """
    result = deepcopy(config)
    case = result["runtime_injection"]["single_enterprise_case_policy"]
    if case.get("case_id") != "single_case_04_staff_shortage":
        raise ValueError("Persistent-labor development instance requires the FD4 preset")
    day9 = case["prewarm_execution_plan"]["days"]["9"]
    attrition = day9["hr"]["workflow"][0]
    if attrition["action"]["action_name"] != "process_employee_attrition":
        raise ValueError("Unexpected FD4 prewarm structure; do not silently change another action")
    attrition["action"]["action_param"]["num_people"] = 5
    attrition["action"]["action_param"]["reason"] = "All production operators leave before handover."
    attrition["action_reason"] = "Production operators leave; existing equipment and material remain."
    day9["production"]["workflow"] = [{
        "action": {"action_name": "action_pass", "action_param": "No workers remain to create a bridge plan."},
        "action_reason": "No workers remain to create a bridge plan.",
        "module_type": "ProductionManager", "executor_id": "Manufacturer",
    }]
    # Original takeover messages contain prescriptive hiring explanations.
    # Keep the new intervention factual and common to every controller arm.
    for department in day9.values():
        if isinstance(department, dict):
            department.pop("communications", None)
    signature = case["target_handoff_signature"]
    signature["organization"]["production_headcount_range"] = [0, 0]
    signature["organization"]["production_allocated_range"] = [0, 0]
    signature["production"]["available_capacity_range"] = [1000, 1000]
    signature["production"]["in_progress_plans_range"] = [0, 0]
    case["scene_goal"] = "Recovery with zero production workers and existing equipment/materials."
    if isinstance(result.get("single_enterprise_case"), dict):
        result["single_enterprise_case"] = deepcopy(case)
    result["fd_validation_instance"] = {
        "instance_id": "FD4-02", "instance_sequence": 2,
        "formal_evaluation_ready": False,
        "changed_construction": ["R9 production attrition 4 -> 5", "R9 bridge plan -> action_pass"],
        "original_RB_not_a_matched_baseline": True,
    }
    result["meta"]["name"] = 'Single-enterprise Case 04 Staffing and fulfillment recovery'
    if result["runtime_injection"].get("scripted_rule_policy", {}).get("enabled"):
        result["meta"]["name"] += ' (scripted)'
    result["meta"]["summary"] = 'No production workers; existing equipment, materials and orders permit studying coordinated hiring, production and delivery recovery.'
    return result
