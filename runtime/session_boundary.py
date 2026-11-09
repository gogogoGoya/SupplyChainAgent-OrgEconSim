"""One command boundary per simulation session, shared by reads and writes."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
from threading import Lock
import time

from network.exchange_manager import bind_exchange_counter, reset_exchange_counter

_registry_lock = Lock()
_gates = {}


def gate_for(session_id):
    with _registry_lock:
        return _gates.setdefault(session_id, {"lock": Lock(), "counter": [1], "last_access": time.monotonic()})


@contextmanager
def session_scope(session_id):
    gate = gate_for(session_id)
    with gate["lock"]:
        token = bind_exchange_counter(gate["counter"])
        gate["last_access"] = time.monotonic()
        try:
            yield
        finally:
            reset_exchange_counter(token)


@asynccontextmanager
async def async_session_scope(session_id):
    gate = gate_for(session_id)
    # Nonblocking acquisition avoids blocking the event loop or leaking a lock
    # acquired by an executor after its awaiting coroutine has been cancelled.
    while not gate["lock"].acquire(blocking=False):
        await asyncio.sleep(0.005)
    token = bind_exchange_counter(gate["counter"])
    gate["last_access"] = time.monotonic()
    try:
        yield
    finally:
        reset_exchange_counter(token)
        gate["lock"].release()
