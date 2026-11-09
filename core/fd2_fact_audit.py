"""Nonblocking FD2 fact checks over supplied inputs; never select an action."""

import math

from core.fd_decision_evidence import finite_number


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [row for rows in value.values() if isinstance(rows, list)
                for row in rows if isinstance(row, dict)]
    return []


def describe_fd2_coverage(payload, round_id):
    """Keep independent views separate; missing records never imply zero."""
    payload = _object(payload)
    views = []

    def view(product, demand, stock, wip, source, *, exact=False):
        values = [finite_number(v) for v in (demand, stock, wip)]
        values = [v if v is not None and v >= 0 else None for v in values]
        gap = max(0, values[0] - values[1] - values[2]) if all(v is not None for v in values) else None
        views.append({"product_id": product, "source": source, "order_level": exact,
                      "demand": values[0], "finished_goods": values[1], "remaining_wip": values[2],
                      "uncovered_committed_quantity": gap,
                      "buffer_and_unaccepted_demand_not_included": True})

    sales, inventory, production = [_object(payload.get(key)) for key in ("sales", "inventory", "production")]
    orders = sales.get("sales_orders")
    if isinstance(orders, dict) and isinstance(inventory.get("inventory_items"), list):
        stock = {row["item_id"]: row.get("quantity") for row in _rows(inventory["inventory_items"])
                 if isinstance(row.get("item_id"), str)}
        recipes = _object(production.get("product_recipes"))
        products = set(recipes) | {row["product_id"] for row in _rows(orders) if isinstance(row.get("product_id"), str)}
        products |= {row["item_id"] for row in _rows(inventory["inventory_items"])
                     if isinstance(row.get("item_id"), str) and row.get("item_type") == "product"}
        demand, deadlines, seen, unknown = {}, {}, set(), set()
        for bucket, rows in orders.items():
            if not isinstance(rows, list) or bucket.startswith("recent_"):
                if bucket in {"accepted", "in_progress", "backlog"} and not isinstance(rows, list):
                    unknown.update(products)
                continue
            if any(not isinstance(row, dict) for row in rows):
                unknown.update(products)
            for row in _rows(rows):
                product = row.get("product_id")
                status = str(row.get("status") or bucket).lower()
                if status not in {"accepted", "in_progress", "backlog"}:
                    continue
                identity = row.get("order_id")
                if not isinstance(identity, str) or not isinstance(product, str):
                    unknown.update(products)
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                deadline = finite_number(row.get("delivery_deadline"))
                quantity = finite_number(row.get("quantity"))
                if deadline is None or quantity is None or quantity < 0:
                    unknown.add(product)
                elif deadline >= round_id:
                    demand[product] = demand.get(product, 0) + quantity
                    deadlines.setdefault(product, []).append(deadline)
        plans = production.get("production_plans")
        for product in sorted(products):
            containers = list(plans.values()) if isinstance(plans, dict) else [plans]
            complete_records = all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
                                   for rows in containers)
            wip = 0 if complete_records else None
            for plan in _rows(plans):
                if wip is None:
                    break
                if not isinstance(plan.get("product_id"), str) or not isinstance(plan.get("status"), str):
                    wip = None
                    break
                if plan.get("product_id") != product or plan.get("status") not in {"in_progress", "active", "created", "pending"}:
                    continue
                quantity = finite_number(plan.get("quantity"))
                produced = finite_number(_object(plan.get("progress")).get("quantity_produced"))
                completion = finite_number(plan.get("completion_time"))
                # A late/undated plan cannot be credited against every deadline.
                if (quantity is None or produced is None or completion is None
                        or completion < round_id
                        or (deadlines.get(product) and completion > min(deadlines[product]))):
                    wip = None
                    break
                wip += max(0, quantity - produced)
            view(product, None if product in unknown else demand.get(product, 0), stock.get(product), wip,
                 "sales.sales_orders + inventory.inventory_items + production.production_plans", exact=True)
    else:
        state = _object(payload.get("self_state"))
        guard = _object(state.get("recovery_guard"))
        for row in _rows(guard.get("candidates")):
            if isinstance(row.get("product_id"), str):
                demand = row.get("confirmed_order_backlog_quantity") if finite_number(row.get("stale_backlog_quantity")) == 0 else None
                view(row["product_id"], demand, row.get("on_hand"),
                     0 if finite_number(row.get("active_plan_count")) == 0 else None,
                     "self_state.recovery_guard.candidates (summary, not independent demand)")
        departments = _object(payload.get("departments"))
        stocks = _object(_object(departments.get("inventory")).get("products"))
        # A global sales counter cannot be attributed to individual products.
        if len(stocks) == 1:
            product, quantity = next(iter(stocks.items()))
            sales_summary = _object(departments.get("sales"))
            demand = sales_summary.get("confirmed_order_backlog_quantity") if finite_number(sales_summary.get("stale_backlog_quantity")) == 0 else None
            view(product, demand, quantity,
                 0 if finite_number(_object(departments.get("production")).get("total_production_plans")) == 0 else None,
                 "departments.sales + inventory + production (single-product summary)")
    return {"schema_version": "fd2_coverage_facts.v1", "round_id": int(round_id),
            "scope": "supplied_input_only", "action_recommendations": False,
            "independent_views_must_not_be_added": True, "views": views}


def fd2_fact_warnings(payload, role, sources, round_id, formal_end=19):
    """Warn about explicit contradictions, never reject a proposal or rewrite it."""
    role = str(role).lower()
    views = [dict(view, input=name) for name, data in sources.items() if name != "analysis"
             for view in describe_fd2_coverage(data, round_id)["views"]]
    warnings = []
    if role == "analyst" and isinstance(payload, dict):
        review = payload.get("coverage_review")
        if not isinstance(review, dict):
            if views:
                warnings.append("Coverage review is absent; optional coverage_review can separate current demand, stock and timely WIP. This is not a required field.")
        else:
            for product, row in review.items():
                if not isinstance(row, dict):
                    warnings.append(f"coverage_review.{product} cannot be inspected; unknown is allowed.")
                    continue
                known = next((v for v in views if v["product_id"] == product and v["order_level"]), None)
                if not known:
                    continue
                for field in ("demand", "finished_goods", "remaining_wip"):
                    claimed = finite_number(row.get(field))
                    actual = known[field]
                    if claimed is not None and actual is not None and not math.isclose(claimed, actual, abs_tol=1e-6):
                        warnings.append(f"coverage_review.{product}.{field}={claimed:g}, supplied {known['input']} evidence={actual:g}; recheck units and duplicates.")
                numbers = {key: finite_number(row.get(key)) for key in
                           ("demand", "finished_goods", "remaining_wip", "buffer", "net_gap")}
                if all(value is not None and value >= 0 for value in numbers.values()):
                    gap = max(0, numbers["demand"] + numbers["buffer"] - numbers["finished_goods"] - numbers["remaining_wip"])
                    if not math.isclose(gap, numbers["net_gap"], abs_tol=1e-6):
                        warnings.append(f"coverage_review.{product}.net_gap contradicts its own coverage arithmetic.")
                if (known["uncovered_committed_quantity"] == 0 and numbers["buffer"] == 0
                        and (row.get("production_required") is True or row.get("procurement_required") is True)):
                    warnings.append(f"{product}: supplied stock/timely WIP covers committed demand; new production/procurement needs a separate evidenced benefit, not the same orders again.")
    actions = payload if isinstance(payload, list) else []
    covered = {v["product_id"] for v in views if v["uncovered_committed_quantity"] == 0}
    covered -= {v["product_id"] for v in views if v["uncovered_committed_quantity"] not in {0, None}}
    for item in actions:
        action = _object(_object(item).get("action"))
        params = _object(action.get("action_param"))
        if role == "production" and action.get("action_name") == "create_production_plan":
            if isinstance(params.get("product_id"), str) and params["product_id"] in covered:
                warnings.append(f"{params['product_id']}: proposed new plan although supplied coverage already covers known commitments; explain any real buffer/other benefit. This is not a pass command.")
        elif role == "procurement" and action.get("action_name") == "create_purchase_order":
            material = params.get("material_id")
            if not isinstance(material, str):
                continue
            linked = set()
            for source in sources.values():
                source = _object(source)
                roots = [_object(source.get("self_state")), _object(source.get("production")),
                         _object(_object(source.get("departments")).get("procurement"))]
                for root in roots:
                    signal = _object(_object(root.get("recipe_recovery_signal_by_material")).get(material))
                    links = signal.get("linked_products")
                    if isinstance(links, list):
                        linked.update(p for p in links if isinstance(p, str))
                    for product, recipe in _object(root.get("product_recipes")).items():
                        if material in _object(_object(recipe).get("raw_materials")):
                            linked.add(product)
            if linked and linked <= covered:
                warnings.append(f"{material}: linked known commitments are already covered by finished goods/timely WIP; no pending order is not itself a material shortage. Check any separate need.")
        elif role == "sales" and action.get("action_name") == "develop_market":
            for source in sources.values():
                signal = _object(_object(_object(source).get("agent_decision_brief")).get("market_growth_signal"))
                lead = finite_number(signal.get("development_lead_time_rounds"))
                if lead is not None and round_id + lead > formal_end:
                    warnings.append("Market development completes outside the evaluation window; distinguish current recovery from evidenced long-term investment. This legal action is not blocked.")
                    break
    return list(dict.fromkeys(warnings))[:8]
