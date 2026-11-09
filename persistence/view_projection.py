"""Small read models for existing charts; no simulation calculations are changed."""
from pathlib import Path
from core.run_integrity import read_json,completed_count


def obj(value):return value if isinstance(value,dict) else {}
def take(value,keys):return {k:obj(value)[k] for k in keys if k in obj(value)}
def state(root,firm,dept,r):return obj(obj(read_json(root/f"enterprises/{firm}/department/{dept}/day{r}/{dept}.json",{})).get("self_state"))
def order_buckets(value,keys):
    return {k:[take(v,keys) for v in (items if isinstance(items,list) else [items]) if isinstance(v,dict)]
            for k,items in obj(value).items()}
def latest_materials(value):
    result={}
    for item in value if isinstance(value,list) else []:
        if isinstance(item,dict) and item.get("material_id"):result[item["material_id"]]=item
    return list(result.values())


def build_views(root):
    root=Path(root);meta=read_json(root/"run_meta.json",{});count=completed_count(meta) or 0
    firms=meta.get("enterprise_ids") or (sorted(p.name for p in (root/"enterprises").iterdir() if p.is_dir()) if (root/"enterprises").is_dir() else [])
    global_rows=[];operations=[];exchanges=[];evolution=[]
    material_fields=("material_id","on_hand_quantity","incoming_quantity","backlog_quantity","inventory_position","safety_stock","reorder_point","suggested_order_quantity","expected_due_round","created_request","request_id")
    for r in range(count):
        for firm in firms:
            finance=state(root,firm,"finance",r);production=state(root,firm,"production",r)
            inventory=state(root,firm,"inventory",r);procurement=state(root,firm,"procurement",r);sales=state(root,firm,"sales",r)
            stock=[take(item,("item_id","quantity")) for item in inventory.get("inventory_items") or [] if isinstance(item,dict)]
            row={"day":r,"company":firm,"finance":take(finance,("cash",)),
                 "production":take(production,("production_metrics",)),"inventory":{"inventory_items":stock},"sales":{},"procurement":{}}
            if r==count-1:
                row["procurement"]={"orders":order_buckets(procurement.get("orders"),("order_id","supplier_id","order_time","quantity","material_id")),
                                    "suppliers_detail":[take(v,("supplier_id","supplier_type")) for v in procurement.get("suppliers_detail") or []],
                                    "suppliers":[take(v,("supplier_id","supplier_type")) for v in procurement.get("suppliers") or []]}
                row["sales"]={"sales_orders":order_buckets(sales.get("sales_orders"),("order_id","source_type","created_time","quantity","product_id"))}
            global_rows.append(row)
            replenishment=obj(procurement.get("replenishment"))
            operations.append({"day":r,"company":firm,
                "sales":{**take(sales,("service_level_summary","sales_metrics")),"demand_backlog":{"by_product":{k:take(v,("received_quantity","fulfilled_quantity","backlog_quantity","lost_sales_quantity")) for k,v in obj(obj(sales.get("demand_backlog")).get("by_product")).items()}}},
                "inventory":{"inventory_items":stock,**take(inventory,("policy_by_item",))},
                "procurement":{**take(procurement,("operational_summary","purchasable_materials_idList")),
                               "replenishment":{**take(replenishment,("pending_by_material",)),
                                  "history":[take(v,material_fields) for v in latest_materials(replenishment.get("history"))],
                                  "requests":[take(v,material_fields) for v in latest_materials(replenishment.get("requests"))]}}})
        raw=obj(read_json(root/f"public/exchange/day{r}/exchange.json",{}))
        slim={}
        for key,value in obj(obj(raw.get("data")).get("exchanges")).items():
            orders=obj(obj(value).get("orders"));proposals=obj(obj(value).get("proposals"))
            slim[key]={"orders":{**take(orders,("count","total_value")),"list":[take(o,("order_id","quantity","product_id","seller_company_id","buyer_company_id")) for o in orders.get("list") or []]},"proposals":take(proposals,("count",))}
        exchanges.append({"day":r,"data":{"data":{"exchanges":slim}}})
        metrics=obj(read_json(root/f"projections/run_metrics/day{r}/run_metrics_projection.json",{}))
        daily=obj(read_json(root/f"projections/multi_enterprise/day{r}/enterprise_daily_metrics_projection.json",{}))
        env=obj(read_json(root/f"public/exchange/day{r}/external_environment.json",{}))
        end=obj(read_json(root/f"public/exchange/day{r}/end_of_day/external_environment.json",{}))
        environment=env if env.get("enabled") else end if end.get("enabled") else obj(metrics.get("external_environment"))
        health=obj(read_json(root/f"runtime_health/day{r}_agent_timeout.json",{}))
        evolution.append({"turn":r,"environment":take(environment,("enabled","active_factors","triggered_events")),
            "metrics":{**take(metrics,("completed_steps","planned_total_steps")),
               "enterprise_metrics":{f:{"finance":take(obj(v).get("finance"),("total_revenue",)),"sales":take(obj(v).get("sales"),("fill_rate",))} for f,v in obj(metrics.get("enterprise_metrics")).items()},
               "external_response_observability":{"round_actions":take(obj(metrics.get("external_response_observability")).get("round_actions"),("total_action_counts",))},
               "round_integrity":take(metrics.get("round_integrity"),("totals",))},
            "enterpriseDaily":{"enterprise_metrics":{f:{"order_participation":take(obj(v).get("order_participation"),("sold_order_count","sold_quantity","sold_value"))} for f,v in obj(daily.get("enterprise_metrics")).items()}},
            "runtimeHealth":take(health,("all_agent_calls_timed_out",))})
    result={"global":{"schema_version":"chart_view.v1","rows":global_rows,"exchanges":exchanges},
            "operations":{"schema_version":"chart_view.v1","rows":operations},
            "evolution":{"schema_version":"chart_view.v1","rows":evolution}}
    if any(name in meta.get("scenario_id","") for name in ("bullwhip","beer_game")):
        result["bullwhip"]=build_bullwhip(root,count,firms)
    return result


def build_bullwhip(root, count, firms):
    """Project fields consumed by the bullwhip viewer without recomputing indicators."""
    root=Path(root);files={};last=count-1
    for name in ('bullwhip_metrics','external_demand','exchange'):
        key=f'public/exchange/day{last}/{name}.json';files[key]=read_json(root/key)
    for r in range(count):
        for firm in firms:
            key=f'public/observer_state/day{r}/end_of_day/{firm}.json'
            raw=read_json(root/key)
            observation=obj(obj(raw).get('observation'))
            files[key]={'observation':{'inventory':take(observation.get('inventory'),('inventory_items',)),
                                      'sales':take(observation.get('sales'),('sales_metrics',))}} if raw is not None else None
            key=f'enterprises/{firm}/department/procurement/day{r}/procurement.json'
            value=state(root,firm,'procurement',r)
            slim=take(value,('top_tier_supply_plan',))
            if r==last:
                slim.update({'orders':order_buckets(value.get('orders'),('order_id','supplier_id','order_time','quantity','material_id','actual_arrival_time','status')),
                             **take(value,('suppliers','suppliers_detail'))})
            files[key]={'self_state':slim}
            if r==last:
                sales=state(root,firm,'sales',r)
                files[f'enterprises/{firm}/department/sales/day{r}/sales.json']={'self_state':{'sales_orders':order_buckets(sales.get('sales_orders'),('order_id','source_type','created_time','quantity','product_id'))}}
    return {'schema_version':'chart_files.v1','files':files}
