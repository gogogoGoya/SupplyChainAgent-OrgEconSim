"""Lossless, bounded JSON parts for one enterprise Agent's authorized facts.

Repeated subtrees are aliases to earlier JSON pointers. This changes packaging,
not information rights, economic policy, or the number of decision agents.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from core.firm_decision_protocol import read_json

CONTEXT_VERSION = "firm_context_parts.v1"
PART_BYTES = 16 * 1024
PART_LINES = 1000
LINE_CHARS = 1400


def _blob(value):
    return (json.dumps(value, ensure_ascii=False, indent=0, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _fits(value, max_bytes):
    blob = _blob(value)
    lines = blob.decode().splitlines()
    return len(blob) <= max_bytes and len(lines) <= PART_LINES and all(len(line) <= LINE_CHARS for line in lines)


def _write(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_blob(value))
    temporary.replace(path)


def _escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _section(pointer):
    keys = pointer.split("/")[1:]
    if keys and keys[0] == "departments" and len(keys) > 1:
        return "/".join(keys[:2])
    return keys[0] if keys and keys[0] in {"observation", "history", "charts", "departments"} else "identity"


def pack_context(source, *, max_bytes=PART_BYTES):
    """Return mandatory/optional read paths; retain the original input verbatim."""
    if max_bytes < 2048:
        raise ValueError("Context part limit must be at least 2048 bytes")
    source = Path(source).resolve()
    value = read_json(source)
    if not isinstance(value, dict):
        raise ValueError("Authorized context must be a JSON object")
    directory = source.with_name(source.stem + "_parts")
    directory.mkdir(parents=True, exist_ok=True)
    parts, entries, current_section, seen = [], [], None, {}

    def payload(items, section):
        return {"schema_version": CONTEXT_VERSION, "section": section, "entries": items}

    def flush():
        nonlocal entries
        if not entries:
            return
        path = directory / f"part_{len(parts) + 1:03d}.json"
        _write(path, payload(entries, current_section))
        blob = path.read_bytes()
        if not _fits(payload(entries, current_section), max_bytes):
            raise ValueError("Generated context part exceeds its read limits")
        parts.append({"path": str(path), "section": current_section, "bytes": len(blob),
                      "sha256": hashlib.sha256(blob).hexdigest(), "entry_count": len(entries), "lines": len(blob.splitlines()),
                      "required": current_section not in {"history", "charts"}})
        entries = []

    def put(entry):
        nonlocal current_section
        section = _section(entry["pointer"])
        if entries and (section != current_section or not _fits(payload([*entries, entry], section), max_bytes)):
            flush()
        current_section = section
        if not _fits(payload([entry], section), max_bytes):
            raise ValueError("Unsplittable context entry exceeds its byte limit")
        entries.append(entry)

    def walk(node, pointer):
        encoded = _blob(node)
        if isinstance(node, (dict, list)) and len(encoded) > 256:
            identity = hashlib.sha256(json.dumps(node, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            if identity in seen:
                put({"pointer": pointer, "alias": seen[identity]})
                return
            seen[identity] = pointer
        entry = {"pointer": pointer, "value": node}
        if _fits(payload([entry], _section(pointer)), max_bytes):
            put(entry)
        elif isinstance(node, dict):
            put({"pointer": pointer, "container": "object"})
            # Keep optional history/chart aliases dependent on current facts,
            # never the other way round.
            keys = sorted(node, key=lambda k: k in {"history", "charts"}) if pointer == "" else node
            for key in keys:
                walk(node[key], pointer + "/" + _escape(key))
        elif isinstance(node, list):
            put({"pointer": pointer, "container": "array"})
            for index, child in enumerate(node):
                walk(child, pointer + "/" + str(index))
        elif isinstance(node, str):
            put({"pointer": pointer, "container": "string"})
            remaining = node
            while remaining:
                stop = min(len(remaining), max_bytes // 8)
                chunk = remaining[:stop]
                # JSON escaping may expand control characters sixfold.
                while not _fits(payload([{"pointer": pointer, "string_append": chunk}], _section(pointer)), max_bytes):
                    stop //= 2
                    if not stop:
                        raise ValueError("Context pointer too long for bounded parts")
                    chunk = remaining[:stop]
                put({"pointer": pointer, "string_append": chunk})
                remaining = remaining[stop:]
        else:
            raise ValueError("Context scalar cannot fit in a bounded part")

    walk(value, "")
    flush()
    index_path = source.with_name(source.stem + "_index.json")
    index = {"schema_version": CONTEXT_VERSION,
             "identity": {k: value[k] for k in ("enterprise_id", "round_id", "phase", "snapshot_id", "expected_functions") if k in value},
             "source": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "bytes": source.stat().st_size},
             "read_contract": "Read this index, all index pages and all required parts. Each entry uses a JSON pointer. An alias is the identical earlier subtree, not another quantity to add. History/charts remain available as optional reference.",
             "parts": parts}
    pages = []
    if not _fits(index, max_bytes):
        index.pop("parts")
        group = []
        for part in parts:
            if group and not _fits({"parts": [*group, part]}, max_bytes):
                page = directory / f"index_page_{len(pages) + 1:03d}.json"
                _write(page, {"parts": group})
                pages.append(page)
                group = []
            group.append(part)
        if group:
            page = directory / f"index_page_{len(pages) + 1:03d}.json"
            _write(page, {"parts": group})
            pages.append(page)
        index["index_pages"] = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in pages]
    _write(index_path, index)
    if not _fits(index, max_bytes):
        raise ValueError("Context index exceeds its read limits")
    if reconstruct_context(index_path) != value:
        raise ValueError("Context packaging changed authorized facts")
    return {"index_path": index_path, "required": [index_path, *pages, *(Path(p["path"]) for p in parts if p["required"])],
            "optional": [source, *(Path(p["path"]) for p in parts if not p["required"])],
            "manifest": {"schema_version": CONTEXT_VERSION, "index_path": str(index_path),
                         "source_sha256": index["source"]["sha256"], "part_count": len(parts),
                         "required_part_count": sum(p["required"] for p in parts), "part_byte_limit": max_bytes,
                         "part_line_limit": PART_LINES, "part_line_char_limit": LINE_CHARS,
                         "total_part_bytes": sum(p["bytes"] for p in parts)}}


def reconstruct_context(index_path):
    """Validate hashes and reconstruct all values, including optional reference."""
    index = read_json(index_path)
    parts = list(index.get("parts", []))
    for page in index.get("index_pages", []):
        blob = Path(page["path"]).read_bytes()
        if hashlib.sha256(blob).hexdigest() != page["sha256"]:
            raise ValueError("Context index-page hash mismatch")
        parts.extend(json.loads(blob)["parts"])
    values = {}

    def assign(pointer, value):
        values[pointer] = value
        if pointer == "":
            return
        parent, key = pointer.rsplit("/", 1)
        key = key.replace("~1", "/").replace("~0", "~")
        target = values[parent]
        if isinstance(target, list):
            position = int(key)
            if position == len(target):
                target.append(value)
            else:
                target[position] = value
        else:
            target[key] = value

    for part in parts:
        blob = Path(part["path"]).read_bytes()
        if hashlib.sha256(blob).hexdigest() != part["sha256"]:
            raise ValueError("Context part hash mismatch")
        for entry in json.loads(blob)["entries"]:
            pointer = entry["pointer"]
            if "value" in entry:
                assign(pointer, entry["value"])
            elif "alias" in entry:
                assign(pointer, deepcopy(values[entry["alias"]]))
            elif "container" in entry:
                assign(pointer, {"object": {}, "array": [], "string": ""}[entry["container"]])
            else:
                assign(pointer, values[pointer] + entry["string_append"])
    return values[""]
