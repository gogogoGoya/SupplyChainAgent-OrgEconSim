#!/usr/bin/env python3
"""Prepare small, sanitized review projections for an existing published archive."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.result_publication import sanitize_public
from core.run_integrity import completed_count, read_json
from persistence.execution_review import build_execution_evidence, numeric
from persistence.review_projection import build_review
from persistence.view_projection import build_views


def prepare_review(source, published, output, views_only=False):
    source, published, output = map(lambda p: Path(p).resolve(), (source, published, output))
    if output == source or source in output.parents or output == published or published in output.parents:
        raise ValueError("Prepare projections outside the original and published archives")
    if output.exists():raise ValueError("Choose a new output directory")
    manifest = read_json(published / "public_manifest.json", {})
    source_hash = hashlib.sha256((source / "run_meta.json").read_bytes()).hexdigest()
    if manifest.get("source_meta_sha256") != source_hash:
        raise ValueError("Source metadata does not match the publication")
    meta = read_json(source / "run_meta.json", {})
    evidence = sanitize_public(build_execution_evidence(source)) if not views_only else {}
    rows = []
    firms=meta.get("enterprise_ids") or (sorted(p.name for p in (source/"enterprises").iterdir() if p.is_dir()) if (source/"enterprises").is_dir() else [])
    for firm in ([] if views_only else firms):
        for r in range(completed_count(meta) or 0):
            review = build_review(source, firm, r, include_details=False)
            # States for trend fields use the same source/stage adapter as review cards.
            states = review["states"]
            finance,production,sales,inventory,procurement = [states.get(d,{}) for d in ("finance","production","sales","inventory","procurement")]
            capacity, used = numeric(inventory.get("warehouse_capacity")), numeric(inventory.get("used_capacity"))
            items = inventory.get("inventory_items")
            inventory_quantity = sum(numeric(item.get("quantity")) or 0 for item in items) if isinstance(items,list) else None
            get=lambda key: review["metrics"][key]["value"]
            series = {"day":r,"dayLabel":f"Round {r}","cash":get("cash"),"netProfit":get("net_profit"),
                      "revenue":numeric(finance.get("total_revenue")),"cost":numeric(finance.get("total_cost")),
                      "production":numeric((production.get("production_metrics") or {}).get("total_production")),
                      "capacityUtilization":numeric((production.get("production_metrics") or {}).get("capacity_utilization")),
                      "salesRevenue":numeric((sales.get("sales_metrics") or {}).get("total_revenue")),
                      "salesOrders":numeric((sales.get("sales_metrics") or {}).get("total_orders")),
                      "procurementOrders":numeric((procurement.get("procurement_metrics") or {}).get("total_orders")),
                      "inventoryQuantity":inventory_quantity,"warehouseUtilization":used/capacity if capacity and used is not None else None,
                      "actionCounts":{},"actionDetails":{},"errorCounts":{},"departmentStates":{}}
            trace = next((t for t in evidence.get("traces",[]) if t["enterprise"]==firm and t["round"]==r),None)
            for dept in review["departments"]:
                if trace:
                    actions=[a for a in trace["actions"] if a["department"]==dept and not a["is_pass"]]
                    series["actionCounts"][dept]=len(actions)
                    series["errorCounts"][dept]=sum(a["status"] in {"rejected","partial","unknown","normalized_noop"} for a in actions)
                    series["actionDetails"][dept]=[{"actionType":a["action_name"],"success":a["outcome"]=="executed",
                                                   "message":a["proposal"].get("action_reason","")} for a in actions]
                else:
                    actions=[]
                    for prefix in (f"pre_{dept}",dept):
                        data=read_json(source/f"enterprises/{firm}/department/{dept}/day{r}/{prefix}_action.json",[])
                        def flatten(value):
                            if isinstance(value,list):return [a for v in value for a in flatten(v)]
                            return [value] if isinstance(value,dict) else []
                        actions.extend(a for a in flatten(data) if (a.get("action") or {}).get("action_name")!="action_pass" and not a.get("pass_reason"))
                    series["actionCounts"][dept]=len(actions)
                    series["errorCounts"][dept]=None
                    series["actionDetails"][dept]=[{"actionType":(a.get("action") or {}).get("action_name","Unknown action"),"success":None,"message":a.get("action_reason","")} for a in actions]
            rows.append({"enterprise":firm,"round":r,"phase":review["phase"],"metrics":review["metrics"],"series":series})
    token=uuid.uuid4().hex[:12]
    output.mkdir(parents=True)
    projections=build_views(source)
    if not views_only:projections.update({"execution":evidence,"trends":{"schema_version":"review_trend.v1","rows":rows}})
    members={}
    for key,payload in projections.items():
        relative=f"projections/review/{token}/{key}.json"
        path=output/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(sanitize_public(payload),ensure_ascii=False),encoding="utf-8")
        members[key]={"path":relative,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"bytes":path.stat().st_size}
    result={"schema_version":"review_enrichment.v1","run_id":meta.get("run_id"),
            "source_meta_sha256":source_hash,"base_manifest_sha256":hashlib.sha256((published/"public_manifest.json").read_bytes()).hexdigest(),
            "members":members}
    (output/"enrichment.json").write_text(json.dumps(result,indent=2))
    return result


def install_review(prepared, published):
    """Publish new versioned members, then atomically switch the manifest."""
    import os
    import shutil
    prepared,published=Path(prepared),Path(published)
    descriptor=read_json(prepared/"enrichment.json",{})
    manifest_path=published/"public_manifest.json"
    original=manifest_path.read_bytes()
    if hashlib.sha256(original).hexdigest()!=descriptor.get("base_manifest_sha256"):
        raise ValueError("Publication changed after review preparation")
    manifest=json.loads(original)
    if manifest.get("source_meta_sha256")!=descriptor.get("source_meta_sha256"):
        raise ValueError("Review source differs from the published archive")
    for entry in descriptor["members"].values():
        relative=entry["path"]
        if not relative.startswith("projections/review/") or ".." in Path(relative).parts:
            raise ValueError("Invalid review member path")
        data=(prepared/relative).read_bytes()
        if hashlib.sha256(data).hexdigest()!=entry["sha256"]:raise ValueError("Prepared projection changed")
        target=published/relative
        try:target.resolve().relative_to(published.resolve())
        except ValueError:raise ValueError("Review destination escapes the publication")
        if target.exists():raise ValueError("Review member already exists")
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(prepared/relative,target)
    for key,entry in descriptor["members"].items():
        manifest["files"][entry["path"]]={k:entry[k] for k in ("sha256","bytes")}
    manifest["review_projections"]={**manifest.get("review_projections",{}),**{key:entry["path"] for key,entry in descriptor["members"].items()}}
    temporary=manifest_path.with_name(".public_manifest.review.tmp")
    with temporary.open("x",encoding="utf-8") as stream:
        json.dump(manifest,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    temporary.replace(manifest_path)
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path);parser.add_argument("published",type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--install",action="store_true",help="Add prepared projections to the publication manifest")
    parser.add_argument("--views-only",action="store_true",help="Keep existing execution evidence and add compact chart views only")
    args=parser.parse_args()
    result=prepare_review(args.source,args.published,args.output,args.views_only)
    if args.install:install_review(args.output,args.published)
    print(json.dumps({"run_id":result["run_id"],"projections":len(result["members"])}))
