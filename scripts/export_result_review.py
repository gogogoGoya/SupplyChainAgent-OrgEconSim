"""Export the same metric projection used by the browser, without changing a run."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from persistence.review_projection import build_review

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive",type=Path)
    parser.add_argument("--enterprise")
    parser.add_argument("--round",type=int)
    args=parser.parse_args()
    print(json.dumps(build_review(args.archive,args.enterprise,args.round),indent=2,ensure_ascii=False))
