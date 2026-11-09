"""Read-only FD follow-up metrics. Missing observations never become zero outcomes."""

from collections import Counter
from pathlib import Path
import hashlib
import json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def orders(observation):
    return {o["order_id"]: o for rows in observation["sales"]["sales_orders"].values() for o in rows}


def progress(observation):
    plans = observation["production"].get("production_plans", {}).get("in_progress", [])
    produced = sum(p.get("progress", {}).get("quantity_produced", 0) for p in plans)
    remaining = sum(max(0, p["quantity"] - p.get("progress", {}).get("quantity_produced", 0)) for p in plans)
    return produced, remaining


def handover_signature(o):
    """Compare economic fields, excluding generated order/plan identities."""
    state = {"finance": o["finance"], "staffing": o["hr"]["department_staffing"],
             "inventory": sorted(o["inventory"]["inventory_items"], key=lambda i: i["item_id"]),
             "capacity": {k: o["production"][k] for k in ("total_capacity", "available_capacity", "occupied_capacity")}}
    state["orders"] = sorted([{k: v for k, v in a.items() if k != "order_id"} for a in orders(o).values()],
                             key=lambda a: json.dumps(a, sort_keys=True))
    state["active_plans"] = [{k: v for k, v in a.items() if k not in {"plan_id", "assigned_lines"}}
                             for a in o["production"].get("production_plans", {}).get("in_progress", [])]
    raw = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def repair_records(actions):
    records = []
    by_id = {a["action_id"]: a for a in actions}
    referenced = set()
    for a in actions:
        parents = a.get("parent_action_ids", [])
        if not parents:
            continue
        referenced.update(parents)
        outcome = ("safe_withdrawal" if a["status"] == "success" and a["proposed"]["action_name"] == "action_pass"
                   else "replacement_action_succeeded" if a["status"] == "success" else a["status"])
        records.append({"parent_action_ids": parents, "replacement_action_id": a["action_id"],
                        "outcome": outcome, "original_proposals": [by_id[p]["proposed"] for p in parents if p in by_id],
                        "replacement": a["proposed"], "response": a.get("response"), "source": a["source"]})
    for a in actions:
        if a["status"] in {"rejected", "normalized_noop", "partial", "unknown"} and a["action_id"] not in referenced:
            records.append({"parent_action_ids": [a["action_id"]], "replacement_action_id": None,
                            "outcome": "unknown" if a["status"] == "unknown" else "unresolved", "source": a["source"]})
    return records


def evaluate(root):
    root = Path(root)
    meta = read(root / "run_meta.json")
    firms = meta.get("enterprise_ids") or [p.name for p in (root / "enterprises").iterdir() if p.is_dir()]
    if len(firms) != 1:
        raise ValueError("FD metrics require exactly one enterprise")
    firm = firms[0]
    source_paths = []
    states = {}
    for day in range(9, 20):
        phase = "prewarm_end_of_day" if day == 9 else "end_of_day"
        p = root / f"public/observer_state/day{day}/{phase}/{firm}.json"
        states[day] = read(p)["observation"]
        source_paths.append(str(p.relative_to(root)))
    initial, final = states[9], states[19]
    cohort = {o["order_id"]: o["quantity"] for o in initial["sales"]["sales_orders"].get("accepted", [])}
    total = sum(cohort.values())
    trajectory, capacity_events = [], []
    for day in range(10, 20):
        o = states[day]
        current = orders(o)
        complete = {identity for identity, order in current.items() if order.get("status") == "completed"}
        unmet = sum(q for identity, q in cohort.items() if identity not in complete)
        inventory = {i["item_id"]: i["quantity"] for i in o["inventory"]["inventory_items"]}
        f = o["finance"]
        cash = f.get("cash_summary", {}).get("current_cash", f.get("cash"))
        if not isinstance(cash, (int, float)):
            raise ValueError(f"Missing numerical cash at R{day}")
        threshold = f.get("cash_summary", {}).get("warning_threshold")
        capacity = o["production"]["total_capacity"]
        previous = states[day-1]["production"]["total_capacity"]
        if capacity != previous:
            capacity_events.append({"round_id": day, "previous_capacity": previous, "capacity": capacity})
        trajectory.append({"round_id": day, "cohort_unmet": unmet, "cash": cash,
            "warning_threshold": threshold, "inventory": inventory, "capacity": capacity,
            "production_cumulative": o["production"]["production_metrics"]["total_production"],
            "physical_production_cumulative": o["production"]["production_metrics"]["total_production"] + progress(o)[0],
            "revenue_cumulative": f["total_revenue"], "cost_cumulative": f["total_cost"],
            "remaining_wip": progress(o)[1]})
    final_orders = {}
    for o in states.values():
        final_orders.update(orders(o))
    new = [o for o in final_orders.values() if 10 <= o.get("created_time", -1) <= 19]
    completed_new = [o for o in new if o.get("status") == "completed"]
    new_quantity = sum(o["quantity"] for o in new)
    completed_quantity = sum(o["quantity"] for o in completed_new)
    hires = [o for o in final["hr"]["recruitment_status"].get("recruitment_history", [])
             if 10 <= o.get("start_time", -1) <= 19]
    purchases = [o for rows in final["procurement"].get("orders", {}).values() for o in rows
                 if 10 <= o.get("order_time", -1) <= 19]
    finance0, finance1 = initial["finance"], final["finance"]
    actions = []
    for p in sorted(root.glob("enterprises/*/firm_agent/day*/decision/phase_summary.json")):
        phase = read(p)
        for dept, records in phase.get("dispatch_records", {}).items():
            for record in records:
                proposed = record["proposed"]["action"]
                normalized = [v["action"] for v in (record.get("normalized") or [])]
                actions.append({"round_id": phase["round_id"], "department": dept,
                    "task_id": record.get("task_id"), "attempt_number": record.get("attempt_number"),
                    "parent_action_ids": record.get("parent_action_ids", []),
                    "action_id": record["action_id"], "proposed": proposed, "normalized": normalized,
                    "status": record["status"], "substantively_changed": normalized != [proposed],
                    "source": str(p.relative_to(root)), "response": record.get("response")})
    repairs = repair_records(actions)
    positive = [a for a in actions if a["proposed"]["action_name"] != "action_pass"]
    original = [a for a in positive if not a["parent_action_ids"]]
    plan_rounds = [a["round_id"] for a in positive if a["proposed"]["action_name"] == "create_production_plan" and a["status"] == "success"]
    initial_output = initial["production"]["production_metrics"]["total_production"] + progress(initial)[0]
    output_rounds = [r["round_id"] for r in trajectory if r["physical_production_cumulative"] > initial_output]
    thresholds = [r for r in trajectory if isinstance(r["warning_threshold"], (int, float))]
    output = (final["production"]["production_metrics"]["total_production"] + progress(final)[0]
              - initial["production"]["production_metrics"]["total_production"] - progress(initial)[0])
    summary = {
        "cohort_quantity": total, "cohort_completed_quantity": total - trajectory[-1]["cohort_unmet"],
        "cohort_fill": (total - trajectory[-1]["cohort_unmet"]) / total if total else None,
        "cohort_unmet_area": sum(r["cohort_unmet"] for r in trajectory) if total else None,
        "new_demand_quantity": new_quantity, "new_completed_quantity": completed_quantity,
        "new_demand_fill": completed_quantity / new_quantity if new_quantity else None,
        "new_completed_revenue": sum(o["total_amount"] for o in completed_new),
        "formal_revenue": finance1["total_revenue"] - finance0["total_revenue"],
        "formal_cost": finance1["total_cost"] - finance0["total_cost"],
        "formal_net_profit": finance1["financial_indicators"]["net_profit"] - finance0["financial_indicators"]["net_profit"],
        "formal_physical_output": output, "final_inventory": trajectory[-1]["inventory"],
        "first_new_plan_round": min(plan_rounds) if plan_rounds else None,
        "first_new_output_round": min(output_rounds) if output_rounds else None,
        "remaining_wip": progress(final)[1], "minimum_cash": min(r["cash"] for r in trajectory),
        "final_cash": trajectory[-1]["cash"], "negative_cash_rounds": sum(r["cash"] < 0 for r in trajectory),
        "below_warning_rounds": sum(r["cash"] < r["warning_threshold"] for r in thresholds) if thresholds else None,
        "recruited_headcount": sum(o["num_people"] for o in hires),
        "formal_recruit_cost": final["hr"]["recruit_cost_total"] - initial["hr"]["recruit_cost_total"],
        "positive_proposals": len(positive), "action_status_counts": dict(Counter(a["status"] for a in positive)),
        "original_positive_proposals": len(original),
        "repair_outcome_counts": dict(Counter(r["outcome"] for r in repairs)),
        "original_action_status_counts": dict(Counter(a["status"] for a in original)),
        "original_unmodified_successful_proposals": sum(a["status"] == "success" and not a["substantively_changed"] for a in original),
        "unmodified_successful_proposals": sum(a["status"] == "success" and not a["substantively_changed"] for a in positive),
    }
    return {"status": "complete", "schema_version": "fd_ablation_metrics.v1", "run_id": meta["run_id"],
        "handover_economic_sha256": handover_signature(initial),
        "contract": {"window": [10, 19], "cohort": "R9 accepted IDs; completed orders only; breached remains unmet",
                     "financials": "R19 minus R9; commitments are separate", "source_paths": source_paths,
                     "action_counts_include_business_repair_proposals": True,
                     "new_demand": "Union of observed order records created R10-R19; verify external opportunity coverage separately"},
        "summary": summary, "trajectory": trajectory, "orders": list(final_orders.values()),
        "purchases": purchases, "hires": hires, "capacity_events": capacity_events,
        "final_finance": finance1, "actions": actions, "repairs": repairs}
