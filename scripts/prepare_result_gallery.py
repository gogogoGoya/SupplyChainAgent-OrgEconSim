#!/usr/bin/env python3
"""Check and publish explicitly selected existing archives for visitor browsing."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.project_paths import PUBLIC_RESULTS_ROOT
from core.result_gallery import inspect_gallery_run
from core.result_publication import publish_result


def prepare_gallery(archives, destination_root, *, check_only=False, report=None, progress=None):
    sources = [Path(p).resolve() for p in archives]
    if len(set(sources)) != len(sources) or len({p.name for p in sources}) != len(sources):
        raise ValueError("Select distinct archives with distinct directory names")
    destination_root = Path(destination_root).resolve()
    for source in sources:
        if source == destination_root or source in destination_root.parents:
            raise ValueError("Publication root must be outside every source archive")
        if not check_only and (destination_root / "runs" / source.name).exists():
            raise ValueError(f"Publication already exists: {source.name}")
    if report:
        report = Path(report).resolve()
        for source in sources:
            if report == source or source in report.parents:
                raise ValueError("Report must be outside every source archive")
        if report == destination_root or destination_root in report.parents:
            raise ValueError("Report must be outside the public results root")
    rows = [dict(name=p.name, **inspect_gallery_run(p)) for p in sources]
    ready = all(row["display_ready"] for row in rows)
    result = {"schema_version": "result_gallery.v1", "run_count": len(rows),
              "all_display_ready": bool(rows) and ready, "published": False, "runs": rows}
    if not check_only and ready and rows:
        for source, row in zip(sources, rows):
            manifest = publish_result(source, destination_root / "runs" / source.name,
                                      display_inspection={k: v for k, v in row.items() if k != "name"})
            row["published_files"] = len(manifest["files"])
            row["source_meta_sha256"] = manifest["source_meta_sha256"]
            if progress:
                progress(source.name, row["published_files"])
        result["published"] = True
    if report:
        path = Path(report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--destination-root", type=Path, default=PUBLIC_RESULTS_ROOT)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = prepare_gallery(args.archives, args.destination_root,
                                 check_only=args.check_only, report=args.report,
                                 progress=lambda name, count: print(
                                     f"Published {name}: {count} files", file=sys.stderr, flush=True))
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}))
    return 0 if result["all_display_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
