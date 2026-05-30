# db_access_guard.py
#
# Prevent accidental direct PostgreSQL access from worker-side scripts.
#
# Normal worker scripts should call the central DB-manager HTTP API.
# Only the central DB-manager process should connect directly to PostgreSQL.

from __future__ import annotations

import os
import sys


def direct_postgres_allowed() -> bool:
    return os.getenv("WF_ALLOW_DIRECT_POSTGRES", "").strip() == "1"


def assert_not_direct_db_script(script_name: str | None = None) -> None:
    """
    Raise immediately unless WF_ALLOW_DIRECT_POSTGRES=1.

    Use this in old modules/scripts that still contain direct PostgreSQL
    connection code, before they open a connection.
    """
    if direct_postgres_allowed():
        return

    name = script_name or sys.argv[0]

    raise RuntimeError(
        f"Direct PostgreSQL access is forbidden in {name}. "
        f"Use DB_MANAGER_URL and the central DB-manager HTTP API instead. "
        f"Only the central DB-manager may set WF_ALLOW_DIRECT_POSTGRES=1."
    )
