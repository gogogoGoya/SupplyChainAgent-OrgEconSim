"""Conditional near-term events from supplied observations, not future simulation data."""

from core.fd_decision_evidence import finite_number


OPERATING_CAPABILITIES = {
    "recruitment_requires_hr_headcount": False,
    "production_plan_assigns_workers": True,
    "manual_hr_assignment_action_available": False,
    "future_workers_are_not_available_for_current_execution": True,
}


def _ref(pointer):
    return {"input": "observation", "pointer": pointer}


def _token(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _records(buckets, pointer):
    if isinstance(buckets, list):
        for index, row in enumerate(buckets):
            if isinstance(row, dict):
                yield row, pointer + "/" + str(index), ""
    elif isinstance(buckets, dict):
        for bucket, rows in buckets.items():
            if not bucket.startswith("recent_") and isinstance(rows, list):
                for index, row in enumerate(rows):
                    if isinstance(row, dict):
                        yield row, pointer + "/" + _token(bucket) + "/" + str(index), bucket


def describe_forward_evidence(observation, round_id, horizon=3):
    """Keep present workers, pending arrivals and task releases as separate evidence."""
    hr = observation.get("hr") or {}
    production = observation.get("production") or {}
    events, unknowns, seen = [], [], set()
    staffing = hr.get("department_staffing") or {}
    pending = (hr.get("recruitment_status") or {}).get("recruitment_history") or []

    def event(kind, identity, day, pointer, **values):
        key = (kind, identity or pointer)
        if key in seen:
            return
        seen.add(key)
        expected = finite_number(day)
        events.append({"kind": kind, "identity": identity, "expected_round": expected,
            "timing": ("unknown" if expected is None else "overdue_requires_confirmation"
                       if expected < round_id else "within_horizon"
                       if expected <= round_id + horizon else "beyond_horizon"),
            "conditional_not_guaranteed": True, "source_ref": _ref(pointer), **values})

    for row, pointer, _ in _records(pending, "/hr/recruitment_status/recruitment_history"):
        if str(row.get("status") or "").lower() in {"\u8fdb\u884c\u4e2d", "pending", "in_progress", "created"}:
            event("recruitment_arrival", row.get("record_id"), row.get("complete_time"), pointer,
                  department=row.get("department"), workers=finite_number(row.get("num_people")))

    for row, pointer, bucket in _records(production.get("production_plans"), "/production/production_plans"):
        status = str(row.get("status") or bucket).lower()
        if status not in {"in_progress", "active", "created", "pending"}:
            continue
        quantity = finite_number(row.get("quantity"))
        progress = row.get("progress") or {}
        produced = finite_number(progress.get("quantity_produced")) if isinstance(progress, dict) else None
        remaining = max(0, quantity - produced) if quantity is not None and produced is not None else None
        event("production_completion", row.get("plan_id"), row.get("completion_time"), pointer,
              department="PRODUCTION", product_id=row.get("product_id"), remaining_quantity=remaining,
              releasing_workers=finite_number(row.get("assigned_workers"))
              if status in {"in_progress", "active"} else None)

    lines = production.get("production_lines") or {}
    details = lines.get("details") if isinstance(lines, dict) else lines
    for row, pointer, _ in _records(details, "/production/production_lines/details"):
        if str(row.get("status") or "").lower() in {"under_construction", "building"}:
            event("line_completion", row.get("line_id"), row.get("completion_time"), pointer,
                  department="PRODUCTION", releasing_workers=finite_number(row.get("assigned_workers")),
                  capacity=finite_number(row.get("capacity")))

    procurement = observation.get("procurement") or {}
    for row, pointer, bucket in _records(procurement.get("orders"), "/procurement/orders"):
        if str(row.get("status") or bucket).lower() in {"pending", "confirmed", "ordered", "in_transit", "in_progress"}:
            event("material_arrival", row.get("order_id"), row.get("arrival_time"), pointer,
                  department="PROCUREMENT", material_id=row.get("material_id"),
                  quantity=finite_number(row.get("quantity")), releasing_workers=finite_number(row.get("assigned_workers")))

    deadlines = []
    sales = observation.get("sales") or {}
    order_ids = set()
    for row, pointer, bucket in _records(sales.get("sales_orders"), "/sales/sales_orders"):
        if str(row.get("status") or bucket).lower() not in {"accepted", "in_progress", "backlog", "pending"}:
            continue
        identity = row.get("order_id")
        if identity and identity in order_ids:
            continue
        order_ids.add(identity)
        deadline = finite_number(row.get("delivery_deadline"))
        deadlines.append({"order_id": identity, "product_id": row.get("product_id"),
            "quantity": finite_number(row.get("quantity")), "deadline": deadline,
            "already_expired": deadline is not None and deadline < round_id, "source_ref": _ref(pointer)})

    # Never add pending headcount to present headcount, or credit more releases
    # than the explicitly occupied workers in the current snapshot.
    labels = {"PRODUCTION": 'Production', "PROCUREMENT": 'Procurement', "SALES": 'Sales',
              "INVENTORY": 'Inventory', "HR": 'Human Resources', "FINANCE": 'Finance'}
    projections = {}
    for name, row in staffing.items():
        if not isinstance(row, dict):
            continue
        code = next((key for key, label in labels.items() if name in {key, label}), name)
        available = finite_number(row.get("available"))
        allocated = finite_number(row.get("allocated"))
        pending_workers = finite_number(row.get("pending_recruits"))
        arrivals = [e for e in events if e["kind"] == "recruitment_arrival"
                    and e.get("department") in {name, code, labels.get(code)}]
        releases = [e for e in events if e["kind"] in {"production_completion", "line_completion", "material_arrival"}
                    and e.get("department") in {name, code, labels.get(code)}]
        known_workers = sum(max(0, e["workers"]) for e in arrivals if e.get("workers") is not None)
        unlocated = pending_workers is None or pending_workers > known_workers
        if unlocated:
            unknowns.append({"department": code, "reason": "Pending worker count or dated records are incomplete."})
        days = []
        for day in range(round_id + 1, round_id + horizon + 1):
            due = [e for e in events if e.get("department") in {name, code, labels.get(code)}
                   and e["expected_round"] is not None and round_id <= e["expected_round"] <= day]
            arriving = sum(max(0, e["workers"]) for e in due
                           if e["kind"] == "recruitment_arrival" and e.get("workers") is not None)
            release_rows = [e for e in due if e["kind"] in {"production_completion", "line_completion", "material_arrival"}]
            releasing = sum(max(0, e["releasing_workers"]) for e in release_rows
                            if e.get("releasing_workers") is not None)
            unknown = (unlocated or any(e["expected_round"] is None or e["expected_round"] < round_id
                       or (e["kind"] == "recruitment_arrival" and e.get("workers") is None)
                       for e in arrivals)
                       or any(e["expected_round"] is None or e["expected_round"] < round_id
                              or (e["expected_round"] <= day and e.get("releasing_workers") is None)
                              for e in releases))
            release_credit = min(releasing, max(0, allocated)) if allocated is not None else None
            expected_available = (None if available is None or unknown
                                  or (releasing > 0 and release_credit is None)
                                  else max(0, available) + arriving + (release_credit or 0))
            days.append({"round_id": day, "expected_arriving_workers": arriving,
                         "expected_releasing_workers": release_credit,
                         "available_if_events_complete": expected_available})
        projections[code] = {"current": {field: row.get(field) for field in (
            "count", "allocated", "available", "pending_recruits")},
            "source_ref": _ref("/hr/department_staffing/" + _token(name)),
            "conditional_next_rounds": days}
    return {"schema_version": "fd_forward_evidence.v1", "current_round": round_id,
            "horizon_rounds": horizon, "scope": "supplied_observation_only",
            "operating_capabilities": OPERATING_CAPABILITIES.copy(), "events": events,
            "staffing": projections, "committed_order_deadlines": deadlines, "unknowns": unknowns,
            "forecast_is_not_execution_capacity": True, "action_recommendations": False}
