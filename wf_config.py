# wf_config.py
#
# Shared WF configuration helpers.
#
# Primary purpose:
#   - Replace hardcoded DB-manager URLs such as http://localhost:8787
#   - Allow desktop, laptop, LAN, and VPN setups via .env/environment config
#
# Recommended .env examples:
#
#   Desktop/server:
#       DB_MANAGER_URL=http://localhost:8787
#
#   Laptop on LAN:
#       DB_MANAGER_URL=http://Office:8787
#
#   Later via VPN:
#       DB_MANAGER_URL=http://office:8787

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DB_MANAGER_URL = "http://localhost:8787"
DEFAULT_DICTAPI_URL = "http://localhost:8788"
DEFAULT_PROCMAN_URL = "http://localhost:8790"


def _repo_root_from_this_file() -> Path:
    # Expected layout:
    #   WF/Common/wf_config.py
    # so parents[1] is WF/
    return Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    """
    Lightweight .env loader.

    Only sets variables that are not already present in the process
    environment. This lets command-line/env overrides win over .env.
    """
    repo_root = _repo_root_from_this_file()

    candidates = [
        Path.cwd() / ".env",
        repo_root / ".env",
        Path(__file__).resolve().parent / ".env",
        Path(r"E:\OneDrive\WF\.env"),
    ]

    for env_path in candidates:
        if not env_path.exists():
            continue

        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for raw_line in lines:
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value

        return


_load_env_file()


def _clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def get_db_manager_url() -> str:
    return _clean_url(os.getenv("DB_MANAGER_URL", DEFAULT_DB_MANAGER_URL))


def get_dictapi_url() -> str:
    return _clean_url(os.getenv("DICTAPI_URL", DEFAULT_DICTAPI_URL))


def get_procman_url() -> str:
    return _clean_url(os.getenv("PROCMAN_URL", DEFAULT_PROCMAN_URL))


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def print_startup_config(component: str, *, include_local_services: bool = False) -> None:
    """
    Print the important URLs used by this component.

    Use include_local_services=True for DictManager/DictAPI/ProcMan-style
    components where local service URLs are relevant.
    """
    print(
        f"[Startup] {component}: DB_MANAGER_URL={get_db_manager_url()}",
        flush=True,
    )

    if include_local_services:
        print(
            f"[Startup] {component}: DICTAPI_URL={get_dictapi_url()}",
            flush=True,
        )
        print(
            f"[Startup] {component}: PROCMAN_URL={get_procman_url()}",
            flush=True,
        )
