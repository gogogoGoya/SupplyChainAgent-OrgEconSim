"""Index supplied FD2 resource facts without computing demand or choosing actions."""

from copy import deepcopy


def describe_fd2_resources(payload, round_id):
    facts = []

    def add(kind, path, value, fields):
        facts.append({"kind": kind, "source_pointer": path,
                      "values": {key: deepcopy(value[key]) for key in fields if key in value}})

    def walk(value, path):
        if isinstance(value, list):
            for index, row in enumerate(value):
                walk(row, path + "/" + str(index))
        elif isinstance(value, dict):
            guard = value.get("recovery_guard")
            if isinstance(guard, dict):
                summary = guard.get("summary")
                if isinstance(summary, dict):
                    add("production_limits", path + "/recovery_guard/summary", summary,
                        ("available_capacity", "capacity_budget", "total_capacity", "blocking_candidate_count"))
                candidates = guard.get("candidates")
                if isinstance(candidates, list):
                    for index, row in enumerate(candidates):
                        if isinstance(row, dict):
                            add("product_and_material_limits", path + "/recovery_guard/candidates/" + str(index), row,
                                ("product_id", "on_hand", "safety_stock", "confirmed_order_backlog_quantity",
                                 "stale_backlog_quantity", "active_plan_quantity", "material_feasible_quantity",
                                 "raw_material_snapshots", "blocking_reasons", "material_shortages"))
            positions = value.get("inventory_position_by_material")
            if isinstance(positions, dict):
                for item_id, row in positions.items():
                    if isinstance(row, dict):
                        token = str(item_id).replace("~", "~0").replace("/", "~1")
                        add("inventory_position", path + "/inventory_position_by_material/" + token, row,
                            ("item_type", "unit", "on_hand", "incoming", "backlog", "inventory_position",
                             "safety_stock", "reorder_point"))
            for key, row in value.items():
                if key not in {"fd_operating_evidence", "fd_resource_evidence", "fd_evidence_contract", "fd_coverage_evidence"}:
                    token = str(key).replace("~", "~0").replace("/", "~1")
                    walk(row, path + "/" + token)

    walk(payload, "")
    return {"schema_version": "fd2_resource_evidence.v1", "round_id": int(round_id),
            "scope": "supplied_input_only", "action_recommendations": False,
            "independent_sources_must_not_be_added": True,
            "missing_values_are_unknown": True,
            "limits_are_not_demand_or_action_quantities": True, "facts": facts}
