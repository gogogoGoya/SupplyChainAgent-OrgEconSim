"""FD evidence and proposal consistency: no action selection or cross-role enrichment."""

import json
import math


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def describe_operating_evidence(payload, round_id):
    """Summarize only facts already present in this supplied input.

    Independent containers are never added together. Missing reservation,
    task-release or order details remain unknown rather than zero.
    """
    facts = []

    def walk(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}.{index}")
            return
        if not isinstance(value, dict):
            return
        orders = value.get("sales_orders")
        if isinstance(orders, dict):
            seen = set()
            eligible, excluded, unknown, duplicates = [], [], [], []
            for bucket, rows in orders.items():
                if not isinstance(rows, list) or bucket.startswith("recent_"):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    identity = row.get("order_id")
                    if identity and identity in seen:
                        duplicates.append(identity)
                        continue
                    if identity:
                        seen.add(identity)
                    status = str(row.get("status") or bucket).lower()
                    deadline = finite_number(row.get("delivery_deadline"))
                    quantity = finite_number(row.get("quantity"))
                    terminal = status in {"breached", "expired", "cancelled", "canceled",
                                          "completed", "fulfilled", "rejected"}
                    expired = deadline is not None and round_id > deadline
                    record = {"order_id": identity, "status": status,
                              "quantity": quantity, "delivery_deadline": deadline}
                    if terminal or expired:
                        excluded.append(record)
                    elif status in {"accepted", "in_progress", "backlog", "pending"} and quantity is not None and quantity > 0:
                        eligible.append(record)
                    else:
                        unknown.append(record)
            facts.append({"source": f"{path}.sales_orders", "kind": "order_status_evidence",
                          "eligible_committed_records": eligible,
                          "excluded_record_count": len(excluded),
                          "excluded_quantity": sum(max(0, row["quantity"] or 0) for row in excluded),
                          "uncommitted_or_unknown_count": len(unknown),
                          "duplicate_ids": duplicates,
                          "status_eligibility_does_not_prove_delivery_feasibility": True,
                          "not_a_net_production_quantity": True})
        if "product_id" in value and any(key in value for key in (
            "confirmed_order_backlog_quantity", "stale_backlog_quantity", "on_hand",
        )):
            facts.append({"source": path, "kind": "product_coverage_evidence",
                          "values": {key: value[key] for key in (
                              "product_id", "on_hand", "confirmed_order_backlog_quantity",
                              "demand_backlog_quantity", "stale_backlog_quantity", "active_plan_quantity",
                          ) if key in value},
                          "backlog_counters_are_not_independent_demands": True,
                          "reserved_or_unusable_stock": "unknown_unless_explicitly_supplied"})
        if "plan_id" in value and value.get("status") in {"in_progress", "created", "pending", "active"}:
            quantity = finite_number(value.get("quantity"))
            produced = finite_number((value.get("progress") or {}).get("quantity_produced"))
            facts.append({"source": path, "kind": "remaining_plan_evidence",
                          "plan_id": value["plan_id"], "product_id": value.get("product_id"),
                          "remaining_quantity": max(0, quantity - produced)
                          if quantity is not None and produced is not None else None,
                          "completion_time": value.get("completion_time"),
                          "assigned_workers": value.get("assigned_workers")})
        staffing = value.get("department_staffing")
        if isinstance(staffing, dict):
            facts.append({"source": f"{path}.department_staffing", "kind": "staffing_evidence",
                          "departments": {key: {field: row[field] for field in (
                              "count", "allocated", "available", "pending_recruits",
                          ) if field in row} for key, row in staffing.items() if isinstance(row, dict)},
                          "zero_count_is_not_execution_failure": True,
                          "release_time": "unknown_unless_task_completion_is_supplied"})
        # Guard copies often repeat the same candidate. One source remains enough
        # for a semantic index; the untouched original still retains every field.
        for key, item in value.items():
            if key not in {"cash_guard", "margin_guard", "fd_operating_evidence", "sales_orders"}:
                walk(item, f"{path}.{key}")

    walk(payload, "input")
    return {"schema_version": "fd_operating_evidence.v1", "round_id": int(round_id),
            "scope": "supplied_input_only", "action_recommendations": False,
            "facts": facts}


def _reference(ref, sources):
    """Resolve an RFC 6901 pointer only within the supplied role inputs."""
    if (not isinstance(ref, dict) or not isinstance(ref.get("input"), str)
            or ref["input"] not in sources):
        names = ", ".join(sorted(sources))
        example = {"input": next(iter(sources), "supplied_input"), "pointer": "/actual/key"}
        if isinstance(ref, dict) and isinstance(ref.get("input"), str):
            raise ValueError(
                f"unknown evidence input name {ref['input']!r}; allowed input names: [{names}]. "
                "The supplied blackboard file is named coordination in this contract; "
                "changing the input name does not make staffing counts into workload evidence."
            )
        raise ValueError(
            "evidence reference must name a supplied input: expected an object "
            f"like {json.dumps(example)}, not a bare string or file path; "
            f"allowed input names: [{names}]; received {str(ref)[:160]!r}"
        )
    pointer = ref.get("pointer")
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"evidence pointer must start with /; input={ref['input']!r}, pointer={pointer!r}")
    value = sources[ref["input"]]
    try:
        for token in pointer[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not key.isdigit():
                    raise ValueError("invalid list index")
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            "evidence pointer does not exist in supplied input: "
            f"input={ref['input']!r}, pointer={pointer!r}; "
            "pointer starts inside that JSON, not with its input name"
        ) from exc
    return value


def _refs_errors(refs, sources, field="basis_refs", *, empty_plans_are_zero=False):
    if not isinstance(refs, list) or not refs:
        return [f"{field} must contain an input/pointer reference object"]
    errors = []
    for index, ref in enumerate(refs):
        try:
            value = _reference(ref, sources)
            known_empty_plans = (
                empty_plans_are_zero
                and (ref["input"], ref["pointer"]) in {
                    ("observation", "/production/production_plans"),
                    ("state", "/self_state/production_plans"),
                }
                and (sources[ref["input"]].get("fd_input_projection") or {}).get(
                    "all_open_records_retained") is not False
            )
            if value is None or ((value == {} or value == []) and not known_empty_plans):
                errors.append(f"{field}[{index}] cannot cite missing or empty evidence")
        except ValueError as exc:
            errors.append(f"{field}[{index}]: {exc}")
    return errors


def _coverage_errors(check, sources, *, require_known=False, allow_empty_records=False):
    if not isinstance(check, dict):
        return ["decision_check must be an object"]
    fields = ("demand", "finished_goods", "remaining_wip", "buffer", "net_gap")
    values = {key: finite_number(check.get(key)) for key in fields}
    errors = _refs_errors(
        check.get("basis_refs"), sources,
        empty_plans_are_zero=allow_empty_records and values["remaining_wip"] == 0,
    )
    for key in fields:
        if key not in check or (check[key] is not None and values[key] is None):
            errors.append(f"decision_check.{key} must be finite nonnegative or null")
        elif values[key] is not None and values[key] < 0:
            errors.append(f"decision_check.{key} cannot be negative")
    known = all(values[key] is not None for key in fields[:4])
    if known:
        gap = max(0, values["demand"] + values["buffer"]
                  - values["finished_goods"] - values["remaining_wip"])
        if values["net_gap"] is None or not math.isclose(gap, values["net_gap"], abs_tol=1e-6):
            errors.append("net_gap contradicts max(0,demand+buffer-finished_goods-remaining_wip)")
    elif check.get("net_gap") is not None:
        errors.append("unknown coverage cannot be reported as a precise net_gap")
    if require_known and (not known or values["net_gap"] is None):
        errors.append("new production needs quantified coverage; do not invent unknown quantities")
    if values["buffer"] is not None and values["buffer"] > 0:
        refs = check.get("buffer_refs")
        errors.extend(_refs_errors(refs, sources, "buffer_refs"))
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict) or str(ref.get("pointer") or "").split("/")[-1] not in {
                    "safety_stock", "target_finished_goods_buffer", "safety_stock_quantity",
                }:
                    errors.append("buffer_refs must cite an explicit stock policy, not spare cash/capacity")
    return errors


def _workload_present(value, department, round_id):
    if isinstance(value, list):
        return any(_workload_present(item, department, round_id) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("order_id"):
        status = str(value.get("status") or "").lower()
        deadline = finite_number(value.get("delivery_deadline"))
        return (department in {"PRODUCTION", "SALES"}
                and status in {"accepted", "in_progress", "backlog", "available"}
                and (finite_number(value.get("quantity")) or 0) > 0
                and (deadline is None or deadline >= round_id))
    if department == "PRODUCTION":
        confirmed = finite_number(value.get("confirmed_order_backlog_quantity"))
        stale = finite_number(value.get("stale_backlog_quantity"))
        if confirmed is not None and stale is not None and confirmed > stale >= 0:
            return True
        if (finite_number(value.get("net_gap")) or 0) > 0:
            return True
    # A concrete personnel failure is evidence; staffing/cash/inventory alone is not.
    message = str(value.get("message") or "").lower()
    if (str(value.get("department") or "").upper() == department
            and (value.get("success") is False or value.get("status") == "error")
            and any(term in message for term in ('staff shortage', "We're short-staffed.", "insufficient staff"))):
        return True
    return any(_workload_present(item, department, round_id) for item in value.values()
               if isinstance(item, (dict, list)))


def _workload_reference_present(ref, sources, department, round_id, *, allow_leaves=False):
    value = _reference(ref, sources)
    if not allow_leaves:
        return _workload_present(value, department, round_id)
    pointer = ref["pointer"]
    analysis_check = "/department_targets/production/decision_check"
    if ref["input"] == "analysis" and pointer in {analysis_check, analysis_check + "/net_gap"}:
        check = _reference({"input": "analysis", "pointer": analysis_check}, sources)
        if department != "PRODUCTION" or not isinstance(check, dict):
            return False
        numbers = {key: finite_number(check.get(key)) for key in (
            "demand", "finished_goods", "remaining_wip", "buffer", "net_gap")}
        if any(number is None or number < 0 for number in numbers.values()):
            return False
        gap = max(0, numbers["demand"] + numbers["buffer"]
                  - numbers["finished_goods"] - numbers["remaining_wip"])
        return (check.get("new_production_required") is True and gap > 0
                and math.isclose(gap, numbers["net_gap"], abs_tol=1e-6))
    if ref["input"] == "analysis":
        return False
    if isinstance(value, (dict, list)):
        return _workload_present(value, department, round_id)
    if ref["input"] not in {"state", "coordination", "blackboard"}:
        return False
    parent_pointer, _, leaf = pointer.rpartition("/")
    if not parent_pointer or leaf not in {"confirmed_order_backlog_quantity", "quantity"}:
        return False
    parent = _reference({"input": ref["input"], "pointer": parent_pointer}, sources)
    if not isinstance(parent, dict):
        return False
    if leaf == "confirmed_order_backlog_quantity":
        confirmed = finite_number(parent.get(leaf))
        stale = finite_number(parent.get("stale_backlog_quantity"))
        return (department == "PRODUCTION" and confirmed is not None and stale is not None
                and confirmed > stale >= 0)
    return bool(parent.get("order_id") and _workload_present(parent, department, round_id))


def workload_reference_candidates(sources, department, round_id):
    """Index existing typed workload records, never supply staffing/action quantities."""
    candidates = []

    def walk(value, source, pointer):
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, source, pointer + "/" + str(index))
        elif isinstance(value, dict):
            relevant = ("order_id" in value or "confirmed_order_backlog_quantity" in value
                        or "net_gap" in value or "message" in value)
            if relevant and pointer and len(candidates) < 4:
                ref = {"input": source, "pointer": pointer}
                if _workload_reference_present(ref, sources, department, round_id, allow_leaves=True):
                    candidates.append(ref)
            for key, item in value.items():
                token = str(key).replace("~", "~0").replace("/", "~1")
                walk(item, source, pointer + "/" + token)

    for source, value in sources.items():
        if source != "blackboard":
            walk(value, source, "")
    return candidates


def validate_fd4_decision(payload, role, sources, round_id, *, allow_empty_records=False,
                          allow_source_aliases=False, allow_workload_leaves=False):
    """Reject explicit proposal/evidence contradictions, never select an action."""
    role = str(role).lower()
    if allow_source_aliases and "coordination" in sources:
        # A filename alias points to the same supplied object, never another file.
        sources = {**sources, "blackboard": sources["coordination"]}
    errors = []
    if role == "analyst":
        target = (payload.get("department_targets") or {}).get("production") or {}
        check = target.get("decision_check")
        errors.extend(_coverage_errors(check, sources, allow_empty_records=allow_empty_records))
        if isinstance(check, dict):
            if type(check.get("new_production_required")) is not bool:
                errors.append("production decision_check.new_production_required must be boolean")
            if check.get("new_production_required") and finite_number(check.get("net_gap")) == 0:
                errors.append("zero net_gap cannot require new production")
            for field in ("trigger", "stop_condition"):
                if not isinstance(check.get(field), str) or not check[field].strip():
                    errors.append(f"production decision_check.{field} must state a condition")
        return ["department_targets.production.decision_check: " + error for error in errors]
    committed = {}
    declared_gaps = {}
    for index, item in enumerate(payload if isinstance(payload, list) else []):
        action = item.get("action") or {}
        params = action.get("action_param") or {}
        check = item.get("decision_check")
        item_errors = []
        if role == "production" and action.get("action_name") == "create_production_plan":
            operating_sources = {key: value for key, value in sources.items() if key != "analysis"}
            item_errors.extend(_coverage_errors(check, operating_sources, require_known=True,
                                                allow_empty_records=allow_empty_records))
            if isinstance(check, dict):
                gap = finite_number(check.get("net_gap"))
                quantity = finite_number(params.get("quantity"))
                product = params.get("product_id")
                committed[product] = committed.get(product, 0) + (quantity or 0)
                if product in declared_gaps and gap != declared_gaps[product]:
                    item_errors.append("same-product actions must share one coverage calculation")
                declared_gaps[product] = gap
                if gap is not None and (gap <= 0 or committed[product] > gap + 1e-6):
                    item_errors.append("cumulative new production exceeds the declared uncovered demand")
        elif role == "hr" and action.get("action_name") == "handle_recruitment":
            if not isinstance(check, dict):
                item_errors.append("recruitment requires decision_check with staffing and workload references")
            else:
                department = params.get("department")
                if check.get("department") != department:
                    item_errors.append("recruitment evidence department does not match action")
                fields = ("required_workers", "available_workers", "timely_pending_workers",
                          "timely_releasing_workers", "uncovered_workers")
                numbers = {key: finite_number(check.get(key)) for key in fields}
                if any(value is None or value < 0 for value in numbers.values()):
                    item_errors.append("worker coverage must be finite and nonnegative")
                else:
                    gap = max(0, numbers["required_workers"] - numbers["available_workers"]
                              - numbers["timely_pending_workers"] - numbers["timely_releasing_workers"])
                    quantity = finite_number(params.get("num_people"))
                    committed[department] = committed.get(department, 0) + (quantity or 0)
                    if department in declared_gaps and gap != declared_gaps[department]:
                        item_errors.append("same-department recruitment must share one coverage calculation")
                    declared_gaps[department] = gap
                    if not math.isclose(gap, numbers["uncovered_workers"], abs_tol=1e-6):
                        item_errors.append("uncovered_workers contradicts staffing coverage")
                    if quantity is None or gap <= 0 or committed[department] > gap:
                        item_errors.append("recruitment exceeds the declared uncovered workers")
                    for key in ("timely_pending_workers", "timely_releasing_workers"):
                        if numbers[key] > 0:
                            item_errors.extend(_refs_errors(check.get(key + "_refs"), sources, key + "_refs"))
                try:
                    staffing = _reference(check.get("staffing_ref"), sources)
                    labels = {"PRODUCTION": 'Production', "PROCUREMENT": 'Procurement',
                              "SALES": 'Sales', "INVENTORY": 'Inventory',
                              "FINANCE": 'Finance', "HR": 'Human Resources'}
                    ref = check.get("staffing_ref") or {}
                    label = ref.get("pointer", "").split("/")[-1].replace("~1", "/").replace("~0", "~")
                    if (label not in {department, labels.get(department)}
                            and (not isinstance(staffing, dict) or staffing.get("department") != department)):
                        item_errors.append("staffing_ref does not identify the requested department")
                    if (not isinstance(staffing, dict)
                            or finite_number(staffing.get("available")) != numbers["available_workers"]):
                        item_errors.append("available_workers does not match the referenced staffing record")
                    if isinstance(staffing, dict) and numbers["timely_pending_workers"] is not None:
                        pending = finite_number(staffing.get("pending_recruits"))
                        if pending is not None and numbers["timely_pending_workers"] > pending:
                            item_errors.append("timely_pending_workers exceeds actual pending recruitment")
                except ValueError as exc:
                    item_errors.append(str(exc))
                try:
                    if not _workload_reference_present(check.get("workload_ref"), sources, department,
                                                       round_id, allow_leaves=allow_workload_leaves):
                        item_errors.append(
                            "workload_ref has no live demand or concrete personnel failure; "
                            "zero_staff_departments, staffing counts and Analyst prose are not workload. "
                            "Use a department-specific live task/failure, or reconsider recruitment "
                            "against the actual operating need, not just its reference format."
                        )
                except ValueError as exc:
                    item_errors.append(str(exc))
        errors.extend(f"item[{index}]: {error}" for error in item_errors)
    return errors
