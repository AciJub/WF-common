# db_client.py
#
# Shared HTTP client for the central WF DB-manager.
#
# Worker-side scripts should use this module instead of hardcoding:
#   http://localhost:8787
#
# Example:
#
#   from Common.db_client import db_post
#
#   result = db_post("/v1/words/search_by_pattern", {
#       "lang": "DA",
#       "pattern": "A??E",
#   })

from __future__ import annotations

import time
from typing import Any

import requests

from Common.wf_config import get_db_manager_url


class DBManagerError(RuntimeError):
    pass


def _build_url(path: str) -> str:
    base = get_db_manager_url()
    return f"{base}/{path.lstrip('/')}"


def _response_text(response: requests.Response, limit: int = 800) -> str:
    try:
        text = response.text
    except Exception:
        return "<unable to read response text>"

    if len(text) > limit:
        return text[:limit] + "...<truncated>"

    return text


def _decode_json(response: requests.Response, url: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise DBManagerError(
            f"DB-manager returned non-JSON response: {url}: "
            f"HTTP {response.status_code}: {_response_text(response)}"
        ) from exc


def db_get(
    path: str,
    *,
    timeout: float = 30.0,
    retries: int = 0,
    retry_sleep: float = 0.25,
    diag: bool = False,
) -> Any:
    url = _build_url(path)
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        started = time.perf_counter()

        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc

            if diag:
                print(
                    f"[DB_CLIENT] GET attempt={attempt + 1}/{retries + 1} "
                    f"url={url} failed: {exc}",
                    flush=True,
                )

            if attempt < retries:
                time.sleep(retry_sleep)
                continue

            raise DBManagerError(f"DB-manager GET failed: {url}: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if diag:
            print(
                f"[DB_CLIENT] GET {url} -> HTTP {response.status_code} "
                f"{elapsed_ms:.1f} ms",
                flush=True,
            )

        if response.status_code >= 400:
            if attempt < retries and response.status_code >= 500:
                time.sleep(retry_sleep)
                continue

            raise DBManagerError(
                f"DB-manager GET failed: {url}: "
                f"HTTP {response.status_code}: {_response_text(response)}"
            )

        return _decode_json(response, url)

    raise DBManagerError(f"DB-manager GET failed: {url}: {last_exc}")


def db_post(
    path: str,
    payload: dict[str, Any] | list[Any] | None = None,
    *,
    timeout: float = 60.0,
    retries: int = 0,
    retry_sleep: float = 0.25,
    diag: bool = False,
) -> Any:
    url = _build_url(path)
    json_payload = {} if payload is None else payload
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        started = time.perf_counter()

        try:
            response = requests.post(url, json=json_payload, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc

            if diag:
                print(
                    f"[DB_CLIENT] POST attempt={attempt + 1}/{retries + 1} "
                    f"url={url} failed: {exc}",
                    flush=True,
                )

            if attempt < retries:
                time.sleep(retry_sleep)
                continue

            raise DBManagerError(f"DB-manager POST failed: {url}: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if diag:
            size_hint = len(json_payload) if hasattr(json_payload, "__len__") else "?"
            print(
                f"[DB_CLIENT] POST {url} payload_size={size_hint} "
                f"-> HTTP {response.status_code} {elapsed_ms:.1f} ms",
                flush=True,
            )

        if response.status_code >= 400:
            if attempt < retries and response.status_code >= 500:
                time.sleep(retry_sleep)
                continue

            raise DBManagerError(
                f"DB-manager POST failed: {url}: "
                f"HTTP {response.status_code}: {_response_text(response)}"
            )

        return _decode_json(response, url)

    raise DBManagerError(f"DB-manager POST failed: {url}: {last_exc}")


def check_db_manager_health(timeout: float = 5.0, *, fatal: bool = False) -> bool:
    """
    Checks /health on the configured DB-manager.

    If fatal=True, raises SystemExit(2) on failure.
    """
    try:
        result = db_get("/health", timeout=timeout)
    except Exception as exc:
        print(f"[Startup] DB-manager health check FAILED: {exc}", flush=True)

        if fatal:
            raise SystemExit(2)

        return False

    print(f"[Startup] DB-manager health check OK: {result}", flush=True)
    return True
