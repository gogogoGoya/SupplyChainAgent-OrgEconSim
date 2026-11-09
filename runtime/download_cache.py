"""Bounded process-local gates for concurrent archive download preparation."""
from contextlib import contextmanager
import hashlib
from threading import Lock

_gates = [Lock() for _ in range(32)]


@contextmanager
def download_gate(path, private=False):
    identity=f"{path}:{bool(private)}".encode()
    index=int.from_bytes(hashlib.sha256(identity).digest()[:4],"big") % len(_gates)
    with _gates[index]:
        yield
