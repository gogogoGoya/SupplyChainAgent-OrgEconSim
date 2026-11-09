"""Read-only FD4 cohort and financial metrics from sealed snapshots."""

import json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def observation(run, day):
    phase = "prewarm_end_of_day" if day == 9 else "end_of_day"
    return read(run / f"public/observer_state/day{day}/{phase}/Manufacturer.json")["observation"]


def score(run):
    initial, final = observation(run, 9), observation(run, 19)
    cohort = {row["order_id"]: row["quantity"]
              for row in initial["sales"]["sales_orders"].get("accepted", [])}
    trajectory = []
    for day in range(10, 20):
        state = observation(run, day)
        complete = {row["order_id"] for row in state["sales"]["sales_orders"].get("completed", [])}
        staffing = state["hr"]["department_staffing"]
        trajectory.append({"day": day,
                           "cohort_unmet": sum(q for identity, q in cohort.items() if identity not in complete),
                           "production": state["production"]["production_metrics"]["total_production"],
                           "production_staff": staffing.get("Production", staffing.get("PRODUCTION", {}))})
    total = sum(cohort.values())
    inventory = {row["item_id"]: row["quantity"] for row in final["inventory"]["inventory_items"]}
    return {
        "cohort": cohort, "cohort_quantity": total,
        "cohort_fill": (total - trajectory[-1]["cohort_unmet"]) / total if total else None,
        "cohort_unmet_area": sum(row["cohort_unmet"] for row in trajectory),
        "formal_net_profit": final["finance"]["financial_indicators"]["net_profit"]
        - initial["finance"]["financial_indicators"]["net_profit"],
        "formal_cost": final["finance"]["total_cost"] - initial["finance"]["total_cost"],
        "finished_goods": inventory.get("PRODUCT_1"),
        "recruit_cost": final["hr"]["recruit_cost_total"],
        "trajectory": trajectory,
    }
