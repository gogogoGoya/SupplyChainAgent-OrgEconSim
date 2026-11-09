from dataclasses import dataclass
from threading import RLock

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uvicorn
import asyncio
from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path
import pickle
import functools
import inspect
import sys
import math
import uuid
import json
from fastapi import Body
from fastapi.responses import JSONResponse

# Add Item Root Directory to Python Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure Log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Introduce your engine and adaptor.
from simulate.simulation import SupplyChainSimulation
from simulate.SimulationEnvAdapter import SimulationEnvAdapter
from runtime.operations.http_router import create_operations_router
from runtime.platform_access import enforce_platform_access
from config.project_paths import AGENT_ROOT, WORKSPACE_JOBS_ROOT, WORKSPACE_MULTI_ROOT
from core.execution_boundary import assert_execution_ready, ExecutionIndeterminateError, has_pending_action, EXECUTION_PROTOCOL
from runtime.session_boundary import session_scope, async_session_scope

from network.exchange_manager import (
    get_next_id_counter_value,
    restore_next_id_counter_value,
)

INSTANCE_ID = os.environ.setdefault("SUPPLY_CHAIN_INSTANCE_ID", uuid.uuid4().hex)

app = FastAPI(title="SupplyChain Simulation Server", version="1.0")
app.include_router(create_operations_router())
from runtime.artifact_http import create_artifact_router
from runtime.result_access import private_results
from config.platform_access import get_platform_access
app.include_router(create_artifact_router())


@app.middleware("http")
async def enforce_public_viewer(request, call_next):
    # Raw engine endpoints must not bypass the operations router's policy.
    try:
        enforce_platform_access(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                            headers=exc.headers)
    token = private_results.set(get_platform_access().authenticated(request.headers.get("authorization", "")))
    try:
        return await call_next(request)
    finally:
        private_results.reset(token)


def _sanitize_json_payload(value: Any) -> Any:
    """Purge NaN/Inf to JSON sequencable value to avoid /state 500."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_json_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_payload(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_json_payload(v) for v in value]
    return value


def _collect_nonfinite_paths(value: Any, path: str = "root", limit: int = 20) -> List[str]:
    """Collects non-limited floating-point field paths that facilitate the location of observation root causes."""
    results: List[str] = []

    def walk(node: Any, current_path: str) -> None:
        if len(results) >= limit:
            return
        if isinstance(node, float):
            if not math.isfinite(node):
                results.append(current_path)
            return
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{current_path}.{key}")
                if len(results) >= limit:
                    return
            return
        if isinstance(node, (list, tuple)):
            for idx, child in enumerate(node):
                walk(child, f"{current_path}[{idx}]")
                if len(results) >= limit:
                    return

    walk(value, path)
    return results

class ExecuteRequest(BaseModel):
    workflow: List[Dict[str, Any]]
    execute_type: str
    enterprise_name: Optional[str] = None
    dept: Optional[str] = None
    submission_id: Optional[str] = None

class StateRequest(BaseModel):
    enterprise_name: str = "Manufacturer"

class SimulationConfigRequest(BaseModel):
    total_steps: Optional[int] = None
    market_demand_mode: Optional[str] = None
    beer_game_demand_series: Optional[List[float]] = None
    beer_game_customer_delivery_lead_time: Optional[int] = None
    beer_game_unit_price: Optional[float] = None
    beer_game_product_id: Optional[str] = None
    cobweb_config: Optional[Dict[str, Any]] = None
    shared_resource_config: Optional[Dict[str, Any]] = None
    herding_config: Optional[Dict[str, Any]] = None
    enterprise_config: Optional[List[Dict[str, Any]]] = None
    runtime_injection_config: Optional[Dict[str, Any]] = None


class RuntimeCheckpointRequest(BaseModel):
    checkpoint_path: str
    run_id: Optional[str] = None
    scenario_id: Optional[str] = None
    completed_steps: Optional[int] = None
    last_complete_round: Optional[int] = None


RUNTIME_CHECKPOINT_SCHEMA_VERSION = "simulation_runtime_checkpoint.v1"


def _resolve_runtime_checkpoint_path(raw_path: str) -> Path:
    path = Path(str(raw_path or "")).expanduser().resolve()
    allowed_roots = [
        Path(AGENT_ROOT).resolve(),
        Path(WORKSPACE_JOBS_ROOT).resolve(),
        Path(WORKSPACE_MULTI_ROOT).resolve(),
    ]
    if path.suffix not in {".pkl", ".pickle"}:
        raise HTTPException(status_code=400, detail="Runtime checkpoint must use .pkl or .pickle")
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise HTTPException(status_code=400, detail="Runtime checkpoint path is outside allowed roots")
    return path


def _runtime_checkpoint_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

DEFAULT_SIMULATION_SESSION_ID = "default"


@dataclass
class SimulationSession:
    simulation: SupplyChainSimulation
    adapter: SimulationEnvAdapter
    runtime_injection_config: Optional[Dict[str, Any]] = None


simulation_sessions: Dict[str, SimulationSession] = {}
simulation_sessions_lock = RLock()


def _enterprise_config_signature(enterprise_config: Optional[List[Dict[str, Any]]]) -> str:
    return json.dumps(enterprise_config or [], sort_keys=True, ensure_ascii=False, default=str)


def _resolve_session_id(x_simulation_session_id: Optional[str] = None) -> str:
    if isinstance(x_simulation_session_id, str) and x_simulation_session_id:
        return x_simulation_session_id
    return DEFAULT_SIMULATION_SESSION_ID


def _get_session(session_id: str) -> SimulationSession:
    with simulation_sessions_lock:
        session = simulation_sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Simulation session '{session_id}' is not initialized. "
                "Call /simulation_config before using simulation endpoints."
            ),
        )
    session.simulation.controller.execution_session_id = session_id
    try:
        assert_execution_ready(session.simulation.controller)
    except ExecutionIndeterminateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return session

def session_boundary(function):
    signature = inspect.signature(function)
    def session_id(args, kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        return _resolve_session_id(bound.arguments.get("x_simulation_session_id"))
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def async_wrapped(*args, **kwargs):
            async with async_session_scope(session_id(args, kwargs)):
                return await function(*args, **kwargs)
        return async_wrapped
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with session_scope(session_id(args, kwargs)):
            return function(*args, **kwargs)
    return wrapped


@app.on_event("startup")
async def startup_event():
    """Starts parameter service; specific simulation examples are created by / parameter on demand."""
    logger.info("Starting supply chain simulation server...")
    logger.info("Simulation server ready; no scenario is initialized until requested.")

@app.get("/state")
@session_boundary
def get_state(
    req: StateRequest,
    x_simulation_session_id: Optional[str] = Header(default=None),
):
    """Access to Current Environment Watch (JSON/Text)"""
    logger.debug("Received state request")
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        observation = session.adapter._get_observation(req.enterprise_name)
        day = session.simulation.controller.time_manager.get_day()
        nonfinite_paths = _collect_nonfinite_paths(observation)
        if nonfinite_paths:
            logger.warning(
                "Non-finite float detected in observation for %s day %s: %s",
                req.enterprise_name,
                day,
                nonfinite_paths,
            )
        observation = _sanitize_json_payload(observation)
        logger.debug(f"State retrieved successfully for {req.enterprise_name}")
        return {
            "status": "success",
            "data": {
                "current_time": str(day),
                "observation": observation,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting state for {req.enterprise_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/exchange")
@session_boundary
def get_exchange_info(x_simulation_session_id: Optional[str] = Header(default=None)):
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        info = session.simulation.controller.exchange_manager.get_detailed_exchange_info()
        info = _sanitize_json_payload(info)
        logger.debug(f"Exchange info retrieved successfully")
        return {
            "status": "success",
            "data": info
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting exchange info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/simulation_config")
@session_boundary
async def configure_simulation(
    req: SimulationConfigRequest,
    x_simulation_session_id: Optional[str] = Header(default=None),
):
    """Update simulation-level configurations, such as the final round and external demand model."""
    try:
        session_id = _resolve_session_id(x_simulation_session_id)
        with simulation_sessions_lock:
            session = simulation_sessions.get(session_id)
        if session is not None:
            assert_execution_ready(session.simulation.controller)
        should_create = session is None
        if session is not None and req.enterprise_config is not None:
            current_signature = _enterprise_config_signature(
                getattr(session.simulation.controller, "enterprise_configs", None)
            )
            requested_signature = _enterprise_config_signature(req.enterprise_config)
            should_create = requested_signature != current_signature

        if should_create:
            enterprise_config = req.enterprise_config
            if enterprise_config is None:
                raise HTTPException(
                    status_code=400,
                    detail="enterprise_config is required when creating a simulation session",
                )
            logger.info(
                "Initializing simulation session %s with enterprises: %s",
                session_id,
                [item.get("id") for item in enterprise_config],
            )
            simulation = SupplyChainSimulation(enterprise_config=enterprise_config)
            adapter = SimulationEnvAdapter(
                simulation,
                runtime_injection_config=req.runtime_injection_config,
            )
            session = SimulationSession(
                simulation=simulation,
                adapter=adapter,
                runtime_injection_config=req.runtime_injection_config,
            )
            with simulation_sessions_lock:
                simulation_sessions[session_id] = session
            await session.simulation.controller.start_simulation(total_steps=req.total_steps)
        elif req.runtime_injection_config is not None:
            session.runtime_injection_config = req.runtime_injection_config
            session.adapter.set_runtime_injection_config(req.runtime_injection_config)

        config = session.simulation.controller.configure_simulation(
            total_steps=req.total_steps,
            market_demand_mode=req.market_demand_mode,
            beer_game_demand_series=req.beer_game_demand_series,
            beer_game_customer_delivery_lead_time=req.beer_game_customer_delivery_lead_time,
            beer_game_unit_price=req.beer_game_unit_price,
            beer_game_product_id=req.beer_game_product_id,
            cobweb_config=req.cobweb_config,
            shared_resource_config=req.shared_resource_config,
            herding_config=req.herding_config,
        )
        return {
            "status": "success",
            "data": config
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error configuring simulation: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/runtime_checkpoint/save")
@session_boundary
def save_runtime_checkpoint(
    req: RuntimeCheckpointRequest,
    x_simulation_session_id: Optional[str] = Header(default=None),
):
    """Atomically persist the complete in-memory simulation session."""
    session_id = _resolve_session_id(x_simulation_session_id)
    session = _get_session(session_id)
    checkpoint_path = _resolve_runtime_checkpoint_path(req.checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": RUNTIME_CHECKPOINT_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "run_id": req.run_id,
        "scenario_id": req.scenario_id,
        "completed_steps": req.completed_steps,
        "last_complete_round": req.last_complete_round,
        "runtime_injection_config": session.runtime_injection_config or {},
        "simulation": session.simulation,
        "external_environment": session.adapter.external_environment,
        "exchange_next_id": get_next_id_counter_value(),
    }
    temp_path = checkpoint_path.with_suffix(f"{checkpoint_path.suffix}.tmp")
    try:
        with temp_path.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, checkpoint_path)
        digest = _runtime_checkpoint_digest(checkpoint_path)
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in {
                "simulation",
                "external_environment",
                "runtime_injection_config",
            }
        }
        metadata.update({
            "checkpoint_path": str(checkpoint_path),
            "size_bytes": checkpoint_path.stat().st_size,
            "sha256": digest,
        })
        checkpoint_path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"status": "success", "data": metadata}
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.error("Failed to save runtime checkpoint: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/runtime_checkpoint/load")
@session_boundary
def load_runtime_checkpoint(
    req: RuntimeCheckpointRequest,
    x_simulation_session_id: Optional[str] = Header(default=None),
):
    """Restore a trusted runtime checkpoint into the requested session id."""
    session_id = _resolve_session_id(x_simulation_session_id)
    previous = simulation_sessions.get(session_id)
    if previous and has_pending_action(previous.simulation.controller):
        raise HTTPException(status_code=409, detail="Old execution is still draining; checkpoint restore is deferred")
    checkpoint_path = _resolve_runtime_checkpoint_path(req.checkpoint_path)
    if not checkpoint_path.exists():
        raise HTTPException(status_code=404, detail=f"Checkpoint not found: {checkpoint_path}")
    try:
        with checkpoint_path.open("rb") as handle:
            payload = pickle.load(handle)
        if payload.get("schema_version") != RUNTIME_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("Unsupported runtime checkpoint schema")
        if req.run_id and payload.get("run_id") not in {None, req.run_id}:
            raise ValueError("Runtime checkpoint run_id mismatch")
        if req.scenario_id and payload.get("scenario_id") not in {None, req.scenario_id}:
            raise ValueError("Runtime checkpoint scenario_id mismatch")
        if (
            req.last_complete_round is not None
            and payload.get("last_complete_round") != req.last_complete_round
        ):
            raise ValueError("Runtime checkpoint round mismatch")
        simulation = payload.get("simulation")
        if not isinstance(simulation, SupplyChainSimulation):
            raise ValueError("Runtime checkpoint does not contain a simulation")
        simulation.controller.restore_runtime_links_after_checkpoint()
        runtime_config = payload.get("runtime_injection_config") or {}
        exchange_next_id = payload.get("exchange_next_id")
        if exchange_next_id is not None:
            restore_next_id_counter_value(exchange_next_id)
        adapter = SimulationEnvAdapter(simulation, runtime_injection_config=runtime_config)
        external_environment = payload.get("external_environment")
        if external_environment is not None:
            external_environment.controller = simulation.controller
            external_environment.set_runtime_config(runtime_config)
            adapter.external_environment = external_environment
        with simulation_sessions_lock:
            simulation_sessions[session_id] = SimulationSession(
                simulation=simulation,
                adapter=adapter,
                runtime_injection_config=runtime_config,
            )
        return {
            "status": "success",
            "data": {
                "checkpoint_path": str(checkpoint_path),
                "run_id": payload.get("run_id"),
                "scenario_id": payload.get("scenario_id"),
                "completed_steps": payload.get("completed_steps"),
                "last_complete_round": payload.get("last_complete_round"),
                "current_day": simulation.controller.time_manager.get_day(),
                "sha256": _runtime_checkpoint_digest(checkpoint_path),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to load runtime checkpoint: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/market/external_demand")
@session_boundary
def get_external_demand(
    enterprise_id: Optional[str] = None,
    x_simulation_session_id: Optional[str] = Header(default=None),
):
    """Access to external market demand and market order incident records for review and visualization."""
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        return {
            "status": "success",
            "data": _sanitize_json_payload(
                session.simulation.controller.market_manager.get_external_demand_summary(enterprise_id)
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting external demand history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/simulation_context")
@session_boundary
def get_simulation_context(x_simulation_session_id: Optional[str] = Header(default=None)):
    """Gets the current simulation mode and transaction mode for Agent to enter a file to see the context."""
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        return {
            "status": "success",
            "data": _sanitize_json_payload(session.simulation.controller.get_simulation_context())
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting simulation context: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bullwhip")
@session_boundary
def get_bullwhip_metrics(x_simulation_session_id: Optional[str] = Header(default=None)):
    """A layer-by-sequence order-by-sequence magnification indicator for the acquisition of oxen whips."""
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        return {
            "status": "success",
            "data": _sanitize_json_payload(session.simulation.controller.get_bullwhip_metrics())
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bullwhip metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/state/json")
@session_boundary
def get_state_json(x_simulation_session_id: Optional[str] = Header(default=None)):
    """Get complete status in JSON format"""
    logger.debug("Received JSON state request")
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        state_json = session.adapter.get_state_json()
        state_json = _sanitize_json_payload(state_json)
        logger.debug("JSON state retrieved successfully")
        return {
            "status": "success",
            "data": state_json
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting JSON state: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/actions")
@session_boundary
def get_available_actions(x_simulation_session_id: Optional[str] = Header(default=None)):
    """Get a list of available business actions"""
    logger.debug("Received available actions request")
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        actions = session.adapter.get_available_actions()
        logger.debug(f"Returning {len(actions)} available actions")
        return {
            "status": "success",
            "data": actions
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting available actions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/execute")
@session_boundary
async def execute_action(
    req: ExecuteRequest,
    x_simulation_session_id: Optional[str] = Header(default=None),
):
    """Receive and execute Atgent commands"""
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        controller = session.simulation.controller
        ledger = getattr(controller, "submission_receipts", None)
        if ledger is None:
            ledger = controller.submission_receipts = {}
        identity = req.submission_id
        fingerprint = hashlib.sha256(req.model_dump_json(exclude={"submission_id"}).encode()).hexdigest()
        if identity:
            if len(identity) > 160:
                raise HTTPException(400, "Submission identity is too long")
            previous = ledger.get(identity)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Submission identity was reused for a different proposal")
                if previous["status"] != "completed":
                    raise HTTPException(409, "EXECUTION_INDETERMINATE: submission has no final receipt")
                return previous["output"]
            ledger[identity] = {"fingerprint": fingerprint, "status": "pending"}
        try:
            result = await session.adapter.step(req.workflow, req.execute_type, req.enterprise_name, req.dept)
        except BaseException:
            if identity:
                ledger[identity]["status"] = "indeterminate"
            if not getattr(controller, "execution_quarantine", None):
                controller.execution_quarantine = {"code": "EXECUTION_INDETERMINATE", "submission_id": identity}
            raise
        logger.debug("Workflow executed successfully")
        output = {
            "result": result,
            "current_day": str(session.simulation.controller.time_manager.get_day()),
            "submission_id": identity,
            "receipt_id": identity,
            "execution_protocol": EXECUTION_PROTOCOL,
        }
        if identity:
            ledger[identity].update(status="completed", output=output)
        return output
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing workflow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/next_turn")
@session_boundary
async def advance_time(x_simulation_session_id: Optional[str] = Header(default=None)):
    """Move to the next business day."""
    logger.debug("Received advance time request")
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        session.simulation.controller.time_manager.fast_forward_to_next_workday_start()


        logger.debug(f"Time advanced to {session.simulation.controller.time_manager.get_day()}")
        return {
            "status": "success",
            "data": {
                "current_day": str(session.simulation.controller.time_manager.get_day())    
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error advancing time: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/check_orders")
@session_boundary
async def check_orders(x_simulation_session_id: Optional[str] = Header(default=None)):
    """Validation of orders"""
    logger.debug("Received check orders request")
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        session.simulation.controller.handle_enterprises_ordercheck()
        return {
            "status": "success",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/statistics_orders")
@session_boundary
async def statistics_orders(x_simulation_session_id: Optional[str] = Header(default=None)):
    """Statistics on orders"""
    logger.debug("Received statistics orders request")
    try:
        session = _get_session(_resolve_session_id(x_simulation_session_id))
        session.simulation.controller.handle_enterprises_orderstatistics()
        return {
            "status": "success",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error statistics orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/session/status")
@session_boundary
def session_status(x_simulation_session_id: Optional[str] = Header(default=None)):
    session_id = _resolve_session_id(x_simulation_session_id)
    with simulation_sessions_lock:
        session = simulation_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Simulation session is not active")
    controller = session.simulation.controller
    return {"session_id": session_id, "execution_protocol": EXECUTION_PROTOCOL,
            "pending_business_action": has_pending_action(controller),
            "quarantine": _sanitize_json_payload(getattr(controller, "execution_quarantine", None))}


@app.post("/session/close")
@session_boundary
def close_session(x_simulation_session_id: Optional[str] = Header(default=None)):
    session_id = _resolve_session_id(x_simulation_session_id)
    session = simulation_sessions.get(session_id)
    if session and has_pending_action(session.simulation.controller):
        raise HTTPException(status_code=409, detail="Execution is still draining; session remains quarantined")
    with simulation_sessions_lock:
        simulation_sessions.pop(session_id, None)
    return {"status": "success", "session_id": session_id}


@app.get("/health")
def health_check():
    """Health check end"""
    return {
        "status": "healthy",
        "service": "SupplyChain Simulation Server",
        "version": "2026.11.public.1",
        "execution_protocol": EXECUTION_PROTOCOL,
        "instance_id": INSTANCE_ID,
        "deployment_mode": get_platform_access().mode
    }




# The production viewer and the development proxy use the same result APIs.
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.gzip import GZipMiddleware
from config.project_paths import PROJECT_ROOT

app.add_middleware(GZipMiddleware, minimum_size=1000)
_frontend_dist = Path(os.getenv("SUPPLY_CHAIN_FRONTEND_DIST", str(PROJECT_ROOT / "visualization/dist")))
if (_frontend_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="viewer-assets")

@app.get("/", include_in_schema=False)
def viewer_index():
    if (_frontend_dist / "index.html").is_file():
        return FileResponse(_frontend_dist / "index.html")
    return {"service":"SupplyChainAgent", "message":"Build visualization or use the development viewer", "release":"2026.11.public.1"}

@app.get("/favicon.svg", include_in_schema=False)
def viewer_icon():
    icon = _frontend_dist / "favicon.svg"
    if icon.is_file():return FileResponse(icon,media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Viewer icon unavailable")

if __name__ == "__main__":
    logger.info("Starting SupplyChain Simulation Server on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
