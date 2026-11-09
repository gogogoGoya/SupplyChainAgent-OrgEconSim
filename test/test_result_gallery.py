import json

import pytest

from core.result_gallery import DEPARTMENTS, inspect_gallery_run
from core.result_publication import publication_candidates
from persistence.review_projection import build_review
from scripts.prepare_result_gallery import prepare_gallery


def archive(root, firms=("A", "B")):
    root.mkdir()
    (root / "run_meta.json").write_text(json.dumps({
        "run_id": root.name, "enterprise_ids": list(firms), "status": "completed",
        "planned_total_steps": 2, "completed_steps": 2}))
    for firm in firms:
        for r in range(2):
            for relative in [f"public/observer_state/day{r}/end_of_day/{firm}.json",
                             f"enterprises/{firm}/records/day{r}/analysis.json",
                             *[f"enterprises/{firm}/department/{d}/day{r}/{d}.json"
                               for d in DEPARTMENTS]]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
    return root


def test_missing_firm_round_is_not_hidden_by_other_firm_snapshots(tmp_path):
    source = archive(tmp_path / "run")
    (source / "public/observer_state/day1/end_of_day/B.json").unlink()
    result = inspect_gallery_run(source)
    assert result["integrity"]["complete"]
    assert not result["display_ready"]
    assert result["coverage"]["B"]["end_of_day"]["missing_rounds"] == [1]


def test_department_parse_failure_has_its_own_coverage(tmp_path):
    source = archive(tmp_path / "run")
    (source / "enterprises/A/department/hr/day1/hr.json").write_text("broken")
    result = inspect_gallery_run(source)
    assert result["display_ready"]
    assert not result["snapshot_coverage_complete"]
    assert result["coverage"]["A"]["departments"]["hr"]["invalid_rounds"] == [1]


def test_preflight_failure_does_not_publish_other_selected_runs(tmp_path):
    a, b = archive(tmp_path / "a"), archive(tmp_path / "b")
    (b / "public/observer_state/day1/end_of_day/A.json").unlink()
    result = prepare_gallery([a, b], tmp_path / "public")
    assert not result["published"]
    assert not (tmp_path / "public").exists()


def test_publication_keeps_raw_archives_and_validates_report_before_mutation(tmp_path):
    source = archive(tmp_path / "a")
    with pytest.raises(ValueError, match="Report"):
        prepare_gallery([source], tmp_path / "public", report=source / "report.json")
    assert not (tmp_path / "public").exists()
    original = (source / "run_meta.json").read_bytes()
    result = prepare_gallery([source], tmp_path / "public", report=tmp_path / "report.json")
    assert result["published"]
    assert (tmp_path / "public/runs/a/public_manifest.json").is_file()
    review = build_review(tmp_path / "public/runs/a")
    assert review["display_coverage"]["snapshot_coverage_complete"]
    assert (source / "run_meta.json").read_bytes() == original
    with pytest.raises(ValueError, match="already exists"):
        prepare_gallery([source], tmp_path / "public")


def test_candidates_exclude_private_trees_and_links(tmp_path):
    source = archive(tmp_path / "a")
    for relative in ("enterprises/A/firm_agent/day0/task.json", "sdk/trace.json",
                     "checkpoints/snapshot.json", "public/.private/key.json"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    (source / "public/linked").symlink_to(source / "enterprises", target_is_directory=True)
    names = {str(p.relative_to(source)) for p in publication_candidates(source)}
    assert "run_meta.json" in names
    assert "public/observer_state/day1/end_of_day/B.json" in names
    assert not any(any(part in name for part in ("firm_agent", "sdk", "checkpoints", "linked", ".private"))
                   for name in names)


def test_warmup_coverage_and_metrics_use_recorded_observer_stage(tmp_path):
    source = archive(tmp_path / "run", firms=("A",))
    meta = json.loads((source / "run_meta.json").read_text())
    meta["prewarm_rounds"] = 1
    (source / "run_meta.json").write_text(json.dumps(meta))
    observer = source / "public/observer_state/day0"
    (observer / "end_of_day").rename(observer / "prewarm_end_of_day")
    (observer / "prewarm_end_of_day/A.json").write_text(json.dumps({
        "observation": {"finance": {"self_state": {"cash": 123}}}}))
    for dept in DEPARTMENTS:
        (source / f"enterprises/A/department/{dept}/day0/{dept}.json").unlink()
    result = inspect_gallery_run(source)
    assert result["display_ready"] and result["snapshot_coverage_complete"]
    assert result["coverage"]["A"]["departments"]["finance"]["expected"] == 1
    review = build_review(source, round_id=0)
    assert review["phase"] == "prewarm_end_of_day"
    assert review["metrics"]["cash"]["value"] == 123
    assert "prewarm_end_of_day" in review["metrics"]["cash"]["source"]
