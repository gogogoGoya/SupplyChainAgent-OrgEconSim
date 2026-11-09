"""
Operation of the Environmental Configuration Centre.

This file will only be configured for " cross-module sharing, running-time " and will consist mainly of:
- Simulation of time advance rules.
- Local model and agent gateway endpoints
-AgentSDK Default connection parameters

Not here:
- Four enterprise, Beer Game requirements, initialization actions, daily fixes
  These are placed in `simulation_preset_config.py`
- Operational modules own thresholds and time consumption
  These are placed in `module_config.py`
"""

import json
import os
import re
from pathlib import Path
from config.platform_access import MODE_ENV, TOKEN_ENV, get_platform_access


def _load_runtime_env_file() -> None:
    """Load non-empty values from scripts/.env without overriding shell env."""
    if not get_platform_access().execution_enabled:
        return
    env_path = Path(__file__).resolve().parents[1] / "scripts" / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key not in {MODE_ENV, TOKEN_ENV} and key and value and key not in os.environ:
            os.environ[key] = value


def _env_value(name: str) -> str:
    if name.startswith("ANTHROPIC_") or name == "AGENT_GATEWAY_BASE_URL":
        if not get_platform_access().execution_enabled:
            return ""
    return (os.getenv(name) or "").strip()


def _first_env_value(*names: str) -> str:
    for name in names:
        value = _env_value(name)
        if value:
            return value
    return ""


def _clean_model_name(value: object) -> str:
    """Return a plain model id, dropping malformed JSON/list punctuation."""
    text = str(value or "").strip().strip('"').strip("'").strip()
    text = text.strip("[]{}()").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    if any(char in text for char in "[]{}\"'"):
        return ""
    return text


def _parse_model_list_env(raw_value: str) -> list:
    """Parse ANTHROPIC_MODEL_LIST from JSON, comma list, or lightly malformed .env."""
    raw = str(raw_value or "").strip()
    if not raw:
        return []

    candidates = []
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None

    if isinstance(parsed, list):
        candidates = parsed
    elif isinstance(parsed, str):
        candidates = re.split(r"[,;\s]+", parsed)
    else:
        normalized = raw.strip().strip('"').strip("'").strip()
        if normalized.startswith("[") and normalized.endswith("]"):
            normalized = normalized[1:-1]
        normalized = normalized.replace('"', "").replace("'", "")
        candidates = re.split(r"[,;\s]+", normalized)

    result = []
    seen = set()
    for candidate in candidates:
        model_name = _clean_model_name(candidate)
        if model_name and model_name not in seen:
            result.append(model_name)
            seen.add(model_name)
    return result


_load_runtime_env_file()


class EnvironmentConfig:
    """
    A cross-module operating environment configuration.

    Scope of use:
    - parameter : Read the time boost constant
    - `agent/*`: Read service addresses, model gateway addresses and default model interface parameters
    - `simulate/*`: docking local simulation services through environmental services URL

    Design boundary:
    - Only the "run environment level" shared configuration is maintained here
    - parameter / `workspace_multi` such directory names are kept in their respective modules as currently agreed,
      We don't need any extra to do it here.
        """

    # Simulation of the beginning. At present, "0th day 09:00 " is the opening time.
    START_TICK = 9 * 60 * 60
    # The number of picks per day. Current 24-hour conversion.
    TICKS_PER_DAY = 24 * 60 * 60
    # Work begins. `AsyncTimeManager` will then jump to the next working day.
    ON_WORK_PER_DAY = 9 * 60 * 60
    # End of working day. Used to determine whether or not it was off duty.
    OFF_WORK_PER_DAY = 17 * 60 * 60

    # Backwards compatible with the old name so as not to affect the already existing call.
    OnWork_PER_DAY = ON_WORK_PER_DAY
    OffWork_PER_DAY = OFF_WORK_PER_DAY

    # Simulation HTTP service address. parameter / parameter Through it request interfaces parameter, parameter .
    SIMULATION_API_BASE_URL = _env_value("SIMULATION_API_BASE_URL")
    # Agent/ Model Gateway address. multi-enterprise and single-enterpriseAgent are connected to the Claude Agent SDK gateway.
    LOCAL_AGENT_GATEWAY_BASE_URL = _first_env_value("ANTHROPIC_BASE_URL", "AGENT_GATEWAY_BASE_URL")
    # The proxy bypasses the configuration, provided by `scripts/.env` or the host environment.
    LOCAL_NO_PROXY = _first_env_value("NO_PROXY", "no_proxy")

    # Model names and authentication information are read only from the Unified Environmental Access and the old version of the hard-coding fallsback is not retained.
    DEFAULT_AGENT_MODEL = _env_value("ANTHROPIC_MODEL")
    DEFAULT_CEO_MODEL = _env_value("ANTHROPIC_CEO_MODEL") or DEFAULT_AGENT_MODEL
    DEFAULT_AGENT_MODEL_LIST = _parse_model_list_env(_env_value("ANTHROPIC_MODEL_LIST"))
    DEFAULT_AGENT_AUTH_TOKEN = _first_env_value("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

    @classmethod
    def get_agent_env(cls) -> dict:
        """Return non-empty Agent SDK env values from the unified config."""
        if not get_platform_access().execution_enabled:
            return {}
        values = {
            "ANTHROPIC_BASE_URL": cls.LOCAL_AGENT_GATEWAY_BASE_URL,
            "ANTHROPIC_MODEL": cls.DEFAULT_AGENT_MODEL,
            "ANTHROPIC_AUTH_TOKEN": cls.DEFAULT_AGENT_AUTH_TOKEN,
            "ANTHROPIC_API_KEY": cls.DEFAULT_AGENT_AUTH_TOKEN,
            "NO_PROXY": cls.LOCAL_NO_PROXY,
            "no_proxy": cls.LOCAL_NO_PROXY,
        }
        return {key: value for key, value in values.items() if value}

    @classmethod
    def missing_agent_env_keys(cls) -> list:
        """Keys required for Agent Skill mode to connect to Agent Environment."""
        required = {
            "ANTHROPIC_BASE_URL": cls.LOCAL_AGENT_GATEWAY_BASE_URL,
            "ANTHROPIC_MODEL": cls.DEFAULT_AGENT_MODEL,
            "ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY": cls.DEFAULT_AGENT_AUTH_TOKEN,
        }
        return [key for key, value in required.items() if not value]
