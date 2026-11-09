#!/usr/bin/env python3
"""Export an existing run for the public viewer; never execute a simulation."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.result_publication import publish_result
from config.project_paths import PUBLIC_RESULTS_ROOT

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive",type=Path)
    parser.add_argument("--destination",type=Path)
    args=parser.parse_args()
    destination=args.destination or PUBLIC_RESULTS_ROOT/"runs"/args.archive.name
    result=publish_result(args.archive,destination)
    print(json.dumps({"destination":str(destination),"run_id":result["run_id"],"files":len(result["files"])}))
