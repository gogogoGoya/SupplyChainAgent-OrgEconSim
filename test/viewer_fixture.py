"""Synthetic viewer input for UI regression checks, never experimental evidence."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.result_publication import publish_result

def create_fixture(destination):
    source=Path(destination)/'synthetic-source';source.mkdir(parents=True,exist_ok=True)
    def write(name,value):
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    write('run_meta.json',{'run_id':'synthetic-viewer-check','scenario_id':'firm_ablation_fd4_ma_1',
        'status':'completed','completed_steps':2,'planned_total_steps':2,'total_steps':2,
        'scenario_config':{'enterprise_specs':[{'enterprise_id':'Manufacturer','enterprise_name':'Manufacturer'}],
                           'simulation':{'total_steps':2},'runtime_injection':{'firm_architecture_experiment':{'arm':'ma','family':'fd4'}}},
        'data_notice':'Synthetic UI regression input; not experimental evidence.'})
    states={
      'finance':{'cash':12345,'financial_indicators':{'net_profit':999,'net_profit_rate':79.90799,'gross_profit_rate':95.87996}},
      'hr':{'recruitment_status':{'total_recruited':4,'recruitment_history':[{'num_people':n,'record_id':f'REC_{i}','status':'onboarded'} for i,n in enumerate((2,2,1,1))]}},
      'sales':{'sales_metrics':{'completed_orders':48,'total_orders':50,'order_fulfillment_rate':.96},'service_level_summary':{'total_downstream_demand':4720,'fulfilled_downstream_demand':3160,'backlog_quantity':1560,'fill_rate':3160/4720}},
      'inventory':{},'production':{'production_lines':{'available_capacity':200}},'procurement':{}}
    for day in (0,1):
        write(f'enterprises/Manufacturer/records/day{day}/observation.json',{'synthetic':True})
        write(f'public/exchange/day{day}/exchange.json',{})
        for dept,state in states.items():write(f'enterprises/Manufacturer/department/{dept}/day{day}/{dept}.json',{'self_state':state})
    return publish_result(source,Path(destination)/'public/runs/synthetic-viewer-check')

if __name__=='__main__':create_fixture(sys.argv[1])
