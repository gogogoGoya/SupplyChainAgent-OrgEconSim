"""Read-only review projection shared by the viewer and JSON exports."""
from pathlib import Path
from core.run_integrity import read_json, inspect_run, observer_stage, prewarm_boundary
from persistence.review_io import ReviewReader
from persistence.execution_review import summarize_execution

DEPARTMENTS=("finance","production","sales","inventory","procurement","hr")


def number(value):
    import math
    try:
        return float(value) if value is not None and math.isfinite(float(value)) else None
    except (TypeError,ValueError):return None


def metric(value, unit, source, numerator=None, denominator=None):
    value=number(value)
    return dict(value=value,unit=unit,source=source,numerator=numerator,denominator=denominator,
                availability="available" if value is not None else "missing")


def records(value):
    if isinstance(value,list):return [r for item in value for r in records(item)]
    if not isinstance(value,dict):return []
    if any(k in value for k in ("order_id","record_id","line_id","plan_id")):return [value]
    return [r for item in value.values() for r in records(item)]


def build_review(root, enterprise=None, round_id=None, offset=0, limit=100, status=None,
                 start=None, end=None, decision_phase="all", include_details=True):
    root=Path(root);reader=ReviewReader(root);meta=reader.read("run_meta.json",{})
    if not meta: raise ValueError("Archive metadata is unavailable or changed since publication")
    integrity=reader.manifest.get("inspection") or inspect_run(root,meta)
    enterprises=sorted(p.name for p in (root/"enterprises").iterdir() if p.is_dir()) if (root/"enterprises").is_dir() else []
    enterprise=enterprise or next(iter(enterprises),None)
    if enterprise not in enterprises and enterprise is not None:raise ValueError("Unknown enterprise")
    round_id=integrity["last_available_round"] if round_id is None else int(round_id)
    if round_id>=0 and round_id not in integrity["available_rounds"]:raise ValueError("No snapshot was recorded for this round")
    stage = observer_stage(meta, round_id)
    phase = "prewarm_end_of_day" if stage == "prewarm_end_of_day" else "archived_department_snapshot"
    observer_relative = f"public/observer_state/day{round_id}/{stage}/{enterprise}.json"
    observer = reader.read(observer_relative, {}) if phase == "prewarm_end_of_day" else {}
    observation = observer.get("observation", {}) if isinstance(observer, dict) else {}
    states={};departments={}
    for dept in DEPARTMENTS:
        relative=f"enterprises/{enterprise}/department/{dept}/day{round_id}/{dept}.json"
        payload=reader.read(relative)
        availability = reader.states[relative]
        availability = "missing" if availability == "not_published" else availability
        state=payload.get("self_state",payload.get("data",{})) if isinstance(payload,dict) else {}
        source = relative
        if availability == "missing" and isinstance(observation.get(dept), dict):
            payload = observation[dept]
            state = payload.get("self_state", payload)
            availability, source = "available", observer_relative
        state = state if isinstance(state,dict) else {}
        states[dept]=state
        base=root/Path(relative).parent
        proposals=[];receipts=[]
        for prefix in ((f"pre_{dept}",dept) if include_details else ()):
            action=reader.read(str((base/f"{prefix}_action.json").relative_to(root)),[])
            result=reader.read(str((base/f"{prefix}_result.json").relative_to(root)))
            if action:proposals.append({"source":str((base/f"{prefix}_action.json").relative_to(root)),"value":action})
            if result is not None:receipts.append({"source":str((base/f"{prefix}_result.json").relative_to(root)),"value":result})
        rows=records(state.get("sales_orders") or state.get("orders") or state.get("production_plans") or (state.get("recruitment_status") or {}).get("recruitment_history") or [])
        if status:rows=[r for r in rows if str(r.get("status",""))==status]
        rows.sort(key=lambda r:(str(r.get("status","")) not in {"breached","failed","overdue"},str(r.get("order_id",r.get("record_id","")))))
        departments[dept]={"availability":availability,"source":source,"phase":phase,
            "records":rows[offset:offset+limit],"record_count":len(rows),"offset":offset,
            "proposals":proposals,"receipts":receipts,"execution_status":"receipts_recorded" if receipts else "no_receipts_recorded"}
    finance=states.get("finance",{});sales=states.get("sales",{});hr=states.get("hr",{});production=states.get("production",{})
    financial=(finance.get("financial_indicators") or {});service=(sales.get("service_level_summary") or {});sales_metrics=(sales.get("sales_metrics") or {})
    recruitment=(hr.get("recruitment_status") or {});history=(recruitment.get("recruitment_history") or [])
    people=recruitment.get("recruited_people")
    complete_history = (
        recruitment.get("total_recruited") is not None
        and len(history) == recruitment["total_recruited"]
        and len({r.get("record_id") for r in history}) == len(history)
        and all(r.get("status") in {"onboarded", "completed", "已到岗"} and number(r.get("num_people")) is not None for r in history)
    )
    if people is None and complete_history:
        people=sum(r["num_people"] for r in history)
    demand=service.get("total_downstream_demand",sales_metrics.get("total_downstream_demand"))
    fulfilled=service.get("fulfilled_downstream_demand",sales_metrics.get("fulfilled_downstream_demand"))
    fill=service.get("fill_rate")
    if fill is None and number(demand) and number(fulfilled) is not None:fill=fulfilled/demand
    path=lambda d:departments[d]["source"]
    metrics={
      "cash":metric(finance.get("cash"),"SCU",path("finance")),
      "net_profit":metric(financial.get("net_profit"),"SCU",path("finance")),
      "net_profit_margin":metric(financial.get("net_profit_rate"),"percent",path("finance")),
      "gross_profit_margin":metric(financial.get("gross_profit_rate"),"percent",path("finance")),
      "order_completion_rate":metric(sales_metrics.get("order_fulfillment_rate"),"ratio",path("sales"),sales_metrics.get("completed_orders"),sales_metrics.get("total_orders")),
      "quantity_fill_rate":metric(fill,"ratio",path("sales"),fulfilled,demand),
      "backlog":metric(service.get("backlog_quantity",sales_metrics.get("backlog_quantity")),"product units",path("sales")),
      "overdue":metric(service.get("overdue_confirmed_order_quantity"),"product units",path("sales")),
      "recruitment_batches":metric(recruitment.get("completed_recruitment_batches",recruitment.get("total_recruited")),"batches",path("hr")),
      "onboarded_people":metric(people,"people",path("hr")),
      "available_capacity":metric((production.get("production_lines") or {}).get("available_capacity"),"units per round",path("production")),
    }
    for name, value in metrics.items():
        value.update(round=round_id, phase=phase, window={"last_round": round_id})
        if value["availability"] == "missing":
            value["missing_reason"] = "Field absent from the archived snapshot"
            department = next((d for d in DEPARTMENTS if departments[d]["source"] == value["source"]), None)
            if department and departments[department]["availability"] in {"failed_to_load", "changed_since_publication"}:
                value.update(availability=departments[department]["availability"], missing_reason="Snapshot is invalid or changed since publication")
    policy=(meta.get("scenario_config",{}).get("runtime_injection",{}).get("firm_architecture_experiment",{}))
    evidence = reader.projection("execution") if include_details else None
    if include_details and evidence is None and reader.projection_status("execution") != "not_recorded":
        raise ValueError("Execution evidence is unavailable or changed since publication; republish the archive")
    if include_details and evidence is None:
        evidence={"phases":[],"health":[],"traces":[],
                  "legacy_quality":reader.read("firm_architecture_quality.json",{})}
    execution = summarize_execution(evidence, meta, enterprise, start, end, decision_phase) if include_details else None
    trace = next((t for t in (evidence or {}).get("traces", []) if t["enterprise"] == enterprise and t["round"] == round_id), None)
    current_snapshot_complete = all(d["availability"] == "available" for d in departments.values())
    return {"schema_version":"run_review.v1","run_id":meta.get("run_id"),"scenario_id":meta.get("scenario_id"),
        "arm":policy.get("arm",policy.get("architecture")),"enterprise":enterprise,"enterprise_ids":enterprises,
        "round":round_id,"phase":phase,"integrity":integrity,"metrics":metrics,
        "quality":reader.read("firm_architecture_quality.json",{}),"departments":departments,
        "states": states, "execution": execution, "action_trace": trace,
        "windows": {"formal_start": prewarm_boundary(meta),"last_round": integrity["last_available_round"]},
        "current_snapshot_complete": current_snapshot_complete,
        "display_coverage": reader.manifest.get("display_inspection"),
        "comparison":{k:policy.get(k) for k in ("economic_contract_sha256","information_contract_sha256","baseline_group","family")},
        "execution_provenance":meta.get("execution_provenance"),"original_archive_modified":False}


def build_trend(root, enterprise=None, start=None, end=None):
    reader=ReviewReader(root);meta=reader.read("run_meta.json",{})
    firms=meta.get("enterprise_ids") or sorted(p.name for p in (Path(root)/"enterprises").iterdir() if p.is_dir())
    enterprise=enterprise or next(iter(firms), None)
    if enterprise not in firms:raise ValueError("Unknown enterprise")
    projection=reader.projection("trends")
    if not projection:
        return {"availability":reader.projection_status("trends"),"rows":[],"enterprise":enterprise}
    rows=[r for r in projection.get("rows",[]) if r["enterprise"]==enterprise
          and (start is None or r["round"]>=start) and (end is None or r["round"]<=end)]
    return {"availability":"available" if rows else "missing","rows":rows,"enterprise":enterprise,"schema_version":"review_trend.v1"}
