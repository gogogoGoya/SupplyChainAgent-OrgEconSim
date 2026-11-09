"""Read process identity to distinguish exited workers from reused PIDs."""
import os
from pathlib import Path


def process_identity(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].strip().split()
        return {"start_ticks": fields[19], "state": fields[0]}
    except (OSError, ValueError, TypeError, IndexError):
        return None


def process_alive(pid, expected=None):
    if not pid:
        return False
    actual = process_identity(pid)
    if actual and (actual["state"] == "Z" or (expected and actual["start_ticks"] != expected.get("start_ticks"))):
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
