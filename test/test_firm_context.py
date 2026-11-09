from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from core.firm_context import LINE_CHARS, PART_BYTES, PART_LINES, pack_context, reconstruct_context
from core.firm_decision_protocol import visible_facts, write_json


def assert_readable(package):
    for path in [*package["required"], *package["optional"]]:
        if path.name.endswith("_index.json") or path.parent.name.endswith("_parts"):
            blob = path.read_bytes()
            assert len(blob) <= PART_BYTES
            assert len(blob.splitlines()) <= PART_LINES
            assert max(map(len, blob.decode().splitlines())) <= LINE_CHARS


def test_lossless_context_parts_keep_zeros_types_unicode_and_pointer_escapes(tmp_path):
    common = {"list": [{"order_id": i, "quantity": 0 if i % 2 else 7} for i in range(500)]}
    value = {"enterprise_id": "E", "snapshot_id": "S", "round_id": 0, "phase": "decision",
             "observation": {"a/b~c": common, "staff": 0, "none": None, "flag": False, "empty": [],
                             "large_text": """\u4e2d\u6587
	\\\"""" * 7000},
             "departments": {"hr": {"coordination": deepcopy(common)}, "sales": {"coordination": deepcopy(common)}},
             "history": [common, common], "charts": {}}
    path = tmp_path / "context.json"
    write_json(path, value)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    package = pack_context(path)
    result = reconstruct_context(package["index_path"])
    assert result == value
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    assert_readable(package)
    entries = [e for p in package["required"] if p.name.startswith("part_") for e in json.loads(p.read_text())["entries"]]
    assert any("alias" in e for e in entries)
    result["departments"]["hr"]["coordination"]["list"][0]["quantity"] = 999
    assert result["departments"]["sales"]["coordination"]["list"][0]["quantity"] == value["departments"]["sales"]["coordination"]["list"][0]["quantity"]


def test_context_parts_preserve_hidden_peer_filter_and_reject_corruption(tmp_path):
    source = tmp_path / "context.json"
    value = visible_facts({"observation": {"peer_summary": {"quantity": 800}, "cash": 0,
        "true_demand_series": [999], "array": list(range(5000))}, "charts": {}}, peer_visible=False)
    write_json(source, value)
    package = pack_context(source)
    assert reconstruct_context(package["index_path"]) == value
    assert_readable(package)
    part = next(p for p in package["required"] if p.name.startswith("part_"))
    part.write_text('{}')
    with pytest.raises(ValueError, match="hash mismatch"):
        reconstruct_context(package["index_path"])


def test_large_part_index_is_paged_and_every_index_page_is_required(tmp_path):
    source = tmp_path / "large.json"
    value = {"observation": [{"id": i, "text": f"{i:04d}" + "a" * 220} for i in range(1000)]}
    write_json(source, value)
    package = pack_context(source, max_bytes=4096)
    index = json.loads(package["index_path"].read_text())
    assert index["index_pages"]
    assert all(Path(page["path"]) in package["required"] for page in index["index_pages"])
    assert all(p.stat().st_size <= 4096 for p in package["required"])
    assert reconstruct_context(package["index_path"]) == value


