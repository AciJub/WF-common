from __future__ import annotations

"""
PassOutGames.py

Standalone utility:
- Finds all currently active games between two bot labels.
- Repeatedly passes with the player currently in turn until each game ends.
- Intended location:
    E:\OneDrive\WF\Scripts\PassOutGames.py

Default players:
    WillOrange -> Munin

Expected project layout:
    E:\OneDrive\WF\Scripts\PassOutGames.py
    E:\OneDrive\WF\Scripts\Dbuild\DictGame.py
    E:\OneDrive\WF\Scripts\Dbuild\DictManager.py
    E:\OneDrive\WF\Scripts\API\...
    E:\OneDrive\WF\Dbuild\Settings\bots.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ------------------------
# Paths / imports
# ------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
WF_ROOT = SCRIPT_DIR.parent.parent

# Actual layout:
# E:\OneDrive\WF\Dbuild
DBUILD_DIR = WF_ROOT / "apps" / "Builder"
API_DIR = WF_ROOT / "apps" / "API"
BOTS_JSON = WF_ROOT / "config" / "bots.json"

for p in (str(SCRIPT_DIR), str(DBUILD_DIR), str(API_DIR), str(WF_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common.wf_config import get_dictapi_url, print_startup_config  # type: ignore

from DictGame import (  # type: ignore
    DictAPIClient,
    DictAPIConfig,
    _extract_current_turn_slot,
    _extract_matching_active_game_ids,
    _unwrap_game_details_from_rec,
)
from api.wf_client import WordfeudClientLite

LANG_TO_RULESET = {
    "US": 0,
    "NB": 1,
    "NL": 2,
    "DA": 3,
    "SE": 4,
    "EI": 5,
    "ES": 6,
    "FR": 7,
    "SV": 8,
    "DE": 9,
    "NO": 10,
    "FI": 11,
    "PT": 12,
    "IT": 13,
}


# ------------------------
# Console helpers
# ------------------------

def ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def log(msg: str) -> None:
    print(f"{ts()} – {msg}", flush=True)


# ------------------------
# Bot loading
# ------------------------

def load_bots(path: Path = BOTS_JSON) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"bots.json not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    bots = data.get("bots", data) if isinstance(data, dict) else data

    if not isinstance(bots, list):
        raise RuntimeError(f"Invalid bots.json shape: {path}")

    out: List[Dict[str, Any]] = []
    for b in bots:
        if isinstance(b, dict):
            out.append(b)
    return out


def bot_maps(path: Path = BOTS_JSON) -> Tuple[Dict[str, str], Dict[str, str]]:
    label_to_bot_id: Dict[str, str] = {}
    label_to_wf: Dict[str, str] = {}

    for b in load_bots(path):
        label = str(b.get("label") or "").strip()
        if not label:
            continue

        bot_id = str(b.get("id") or "").strip()
        wf = str(b.get("wf_username") or b.get("username") or label).strip()

        if bot_id:
            label_to_bot_id[label] = bot_id
        label_to_wf[label] = wf or label

    return label_to_bot_id, label_to_wf


# ------------------------
# Direct Wordfeud read helpers
# ------------------------

def bot_record_by_label(label: str, path: Path = BOTS_JSON) -> Dict[str, Any]:
    label_s = (label or "").strip()
    for b in load_bots(path):
        if str(b.get("label") or "").strip() == label_s:
            return b
    raise RuntimeError(f"No bot record found for label={label_s!r} in {path}")


def make_direct_wf_client(label: str, path: Path = BOTS_JSON) -> WordfeudClientLite:
    """
    Match DictManager's working discovery path:
    login directly with WordfeudClientLite and use client.games_raw().
    """
    b = bot_record_by_label(label, path)

    email = str(b.get("email") or "").strip()
    password = str(b.get("password") or b.get("pw") or "").strip()
    wf_username = str(b.get("wf_username") or b.get("username") or label).strip()

    if not email or not password:
        raise RuntimeError(f"Bot {label!r} is missing email/password in {path}")

    base_url = (os.getenv("WF_BASE_URL") or "https://game.wordfeud.com/wf").strip()
    timeout_s = float((os.getenv("WF_HTTP_TIMEOUT_S") or "45").strip())

    cli = WordfeudClientLite(base_url=base_url, timeout_s=timeout_s, debug=False)
    try:
        cli.username = wf_username
    except Exception:
        pass

    log(f"Direct WF login: label={label!r} wf_username={wf_username!r}")
    cli.login_with_email(email=email, password=password)
    return cli


def direct_games(client: WordfeudClientLite) -> List[object]:
    raw = client.games_raw() or {}

    if isinstance(raw, dict):
        games = raw.get("games") or raw.get("content") or []
        return list(games) if isinstance(games, list) else []

    if isinstance(raw, list):
        return list(raw)

    return []


def direct_game_details(client: WordfeudClientLite, game_id: int) -> Dict[str, Any]:
    for meth in ("game_details", "get_game", "game"):
        fn = getattr(client, meth, None)
        if not callable(fn):
            continue

        try:
            raw = fn(int(game_id))
        except TypeError:
            raw = fn(game_id=int(game_id))

        if isinstance(raw, dict):
            return raw

    return {}


def is_game_still_active_direct(
    client: WordfeudClientLite,
    *,
    game_id: int,
    p1_wf: str,
    p2_wf: str,
) -> bool:
    raw = {"games": direct_games(client)}
    ids = find_active_game_ids_between(
        raw,
        p1_wf=p1_wf,
        p2_wf=p2_wf,
        rulesets=sorted(set(LANG_TO_RULESET.values())),
    )
    return int(game_id) in set(ids)


def current_turn_slot_direct(
    client: WordfeudClientLite,
    *,
    game_id: int,
    p1_wf: str,
    p2_wf: str,
) -> Optional[str]:
    """
    Determine turn using ONLY the WillOrange/P1 direct WF view.
    """
    gd = direct_game_details(client, int(game_id))
    if not isinstance(gd, dict) or not gd:
        return None

    if _boolish_ended(gd):
        return None

    slot = _extract_current_turn_slot(gd, p1_wf=p1_wf, p2_wf=p2_wf)
    if slot in ("player1", "player2"):
        return slot

    inner = gd.get("game") if isinstance(gd.get("game"), dict) else gd

    for k in (
        "current_player_username",
        "current_player_name",
        "turn_username",
        "player_to_move_username",
        "next_player_username",
    ):
        v = inner.get(k) if isinstance(inner, dict) else None
        if isinstance(v, str) and v.strip():
            u = v.strip().lower()
            if u == p1_wf.strip().lower():
                return "player1"
            if u == p2_wf.strip().lower():
                return "player2"

    return None


def _summary_opponent_is(g: object, p2_wf: str) -> bool:
    if not isinstance(g, dict):
        return False

    target = (p2_wf or "").strip().lower()

    for k in ("opponent_username", "opponent", "opponent_name"):
        v = g.get(k)
        if isinstance(v, str) and v.strip().lower() == target:
            return True

    names = _players_from_game_summary(g)
    return target in names


def _summary_says_p1_turn(g: object) -> Optional[bool]:
    """
    Interpret common Wordfeud summary flags from the local user's POV.

    Return:
        True  => local/P1 user is in turn
        False => local/P1 user is not in turn
        None  => summary does not expose a clear turn flag
    """
    if not isinstance(g, dict):
        return None

    for k in (
        "is_your_turn",
        "your_turn",
        "is_my_turn",
        "my_turn",
        "user_turn",
        "is_current_user_turn",
        "current_user_turn",
    ):
        if k in g:
            return bool(g.get(k))

    for k in ("move", "should_move", "can_move"):
        if k in g and isinstance(g.get(k), bool):
            return bool(g.get(k))

    return None


def discover_p1_turn_games_from_p1_pov(
    client: WordfeudClientLite,
    *,
    p1_wf: str,
    p2_wf: str,
) -> List[int]:
    """
    Discover only WillOrange/P1 games from WillOrange's direct games_raw POV,
    and only include games where WillOrange/P1 is currently in turn.

    This intentionally does NOT inspect Munin's game list.
    """
    out: List[int] = []
    seen: Set[int] = set()

    games = direct_games(client)

    for g in games:
        if not isinstance(g, dict):
            continue
        if _boolish_ended(g):
            gid_dbg = _game_id_from_obj(g) if isinstance(g, dict) else None
            log(f"SKIP ended/inactive gid={gid_dbg!r} "
                f"is_running={g.get('is_running')!r} status={g.get('status')!r} "
                f"state={g.get('state')!r} opponent={g.get('opponent_username')!r}")
            continue

        if not _summary_opponent_is(g, p2_wf):
            continue

        gid = _game_id_from_obj(g)
        if not gid or int(gid) in seen:
            continue

        summary_turn = _summary_says_p1_turn(g)

        if summary_turn is True:
            log(f"KEEP gid={gid} source=summary_turn keys={sorted(g.keys())} "
                f"is_running={g.get('is_running')!r} status={g.get('status')!r} "
                f"state={g.get('state')!r} opponent={g.get('opponent_username')!r} "
                f"summary_turn={summary_turn!r}")
            out.append(int(gid))
            seen.add(int(gid))
            continue

        if summary_turn is False:
            continue

        # Fallback: details from WillOrange/P1 POV only.
        slot = current_turn_slot_direct(
            client,
            game_id=int(gid),
            p1_wf=p1_wf,
            p2_wf=p2_wf,
        )
        if slot == "player1":
            log(f"KEEP gid={gid} keys={sorted(g.keys())} "
                f"is_running={g.get('is_running')!r} status={g.get('status')!r} "
                f"state={g.get('state')!r} opponent={g.get('opponent_username')!r} "
                f"summary_turn={summary_turn!r}")
            out.append(int(gid))
            seen.add(int(gid))

    return sorted(out)


# ------------------------
# DictAPI helpers
# ------------------------

def make_api() -> DictAPIClient:
    action_url = get_dictapi_url()
    action_api_key = (os.getenv("DICTAPI_KEY") or "").strip() or None

    return DictAPIClient(
        DictAPIConfig(
            base_url=action_url,
            api_key=action_api_key,
            timeout_s=float((os.getenv("PASSOUT_API_TIMEOUT_S") or "30").strip()),
            wait_poll_s=float((os.getenv("PASSOUT_WAIT_POLL_S") or "0.25").strip()),
            login_timeout_s=float((os.getenv("PASSOUT_LOGIN_TIMEOUT_S") or "12").strip()),
        )
    )


def wait_done(api: DictAPIClient, request_id: str, timeout_s: float) -> Dict[str, Any]:
    rec = api.wait_done(request_id, timeout_s=timeout_s)
    return rec if isinstance(rec, dict) else {"status": "bad_response", "raw": rec}


def games_raw(api: DictAPIClient, observer_bot_id: str, timeout_s: float = 30.0) -> Dict[str, Any]:
    rid = api.enqueue_games_raw(observer_bot_id)
    rec = wait_done(api, rid, timeout_s=timeout_s)
    if rec.get("status") != "done":
        raise RuntimeError(f"games_raw failed: rid={rid} rec={rec!r}")

    result = rec.get("result") or {}
    if not isinstance(result, dict):
        raise RuntimeError(f"games_raw returned non-dict result: {result!r}")
    return result


def game_details(api: DictAPIClient, bot_id: str, game_id: int, timeout_s: float = 30.0) -> Dict[str, Any]:
    rid = api.enqueue_game_details(bot_id, int(game_id))
    rec = wait_done(api, rid, timeout_s=timeout_s)
    if rec.get("status") != "done":
        raise RuntimeError(f"game_details failed: game_id={game_id} rid={rid} rec={rec!r}")

    gd = _unwrap_game_details_from_rec(rec)
    if not isinstance(gd, dict):
        return {}
    return gd


def enqueue_and_wait_pass(api: DictAPIClient, bot_id: str, game_id: int, timeout_s: float) -> Dict[str, Any]:
    rid = api.enqueue_pass(bot_id, int(game_id))
    rec = wait_done(api, rid, timeout_s=timeout_s)
    if isinstance(rec, dict):
        rec["_request_id"] = rid
    return rec

def pass_rec_says_game_over(rec: Dict[str, Any]) -> bool:
    err = rec.get("error")
    if not isinstance(err, dict):
        return False

    if str(err.get("code") or "").strip().lower() == "game_over":
        return True

    msg = str(err.get("message") or "").strip().lower()
    if "game_over" in msg or "game over" in msg:
        return True

    details = err.get("details")
    if isinstance(details, dict):
        if str(details.get("error_type") or "").strip().lower() == "game_over":
            return True
        raw = str(details.get("raw") or "").strip().lower()
        if "game_over" in raw or "game over" in raw:
            return True

    return False

# ------------------------
# Game parsing
# ------------------------

def _boolish_ended(game: Dict[str, Any]) -> bool:
    inner = game.get("game") if isinstance(game.get("game"), dict) else game
    if not isinstance(inner, dict):
        return True

    # Strong active/running flags. If present and false, exclude.
    for k in ("is_running", "running", "active", "is_active"):
        if k in inner and inner.get(k) is False:
            return True

    # Strong ended flags.
    for k in (
        "ended", "is_ended", "finished", "is_finished",
        "game_over", "is_game_over", "completed", "is_completed",
        "resigned", "is_resigned",
    ):
        if inner.get(k) is True:
            return True

    status = str(inner.get("status") or inner.get("state") or "").strip().lower()
    if status in (
        "ended", "finished", "complete", "completed",
        "game_over", "closed", "archived",
    ):
        return True

    return False


def _game_id_from_obj(g: Dict[str, Any]) -> Optional[int]:
    """
    Extract game id from common Wordfeud summary/detail payload shapes.
    """
    if not isinstance(g, dict):
        return None

    for k in ("id", "game_id", "gameId"):
        try:
            v = g.get(k)
            if v is not None and int(v) > 0:
                return int(v)
        except Exception:
            pass

    inner = g.get("game")
    if isinstance(inner, dict):
        for k in ("id", "game_id", "gameId"):
            try:
                v = inner.get(k)
                if v is not None and int(v) > 0:
                    return int(v)
            except Exception:
                pass

    return None

def _players_from_game_summary(g: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()

    players = g.get("players")
    if isinstance(players, list):
        for p in players:
            if isinstance(p, dict):
                u = p.get("username") or p.get("name") or p.get("nick")
                if isinstance(u, str) and u.strip():
                    names.add(u.strip().lower())
            elif isinstance(p, str) and p.strip():
                names.add(p.strip().lower())

    for k in ("player1", "player2", "inviter", "invitee", "opponent_username", "opponent"):
        u = g.get(k)
        if isinstance(u, str) and u.strip():
            names.add(u.strip().lower())

    return names


def find_active_game_ids_between(
    raw: Dict[str, Any],
    *,
    p1_wf: str,
    p2_wf: str,
    rulesets: Optional[Iterable[int]] = None,
) -> List[int]:
    """
    First uses DictGame._extract_matching_active_game_ids per ruleset.
    Then falls back to a direct summary parser so games with odd/missing ruleset
    shapes are not missed.
    """
    found: Set[int] = set()

    ruleset_list = list(rulesets or sorted(set(LANG_TO_RULESET.values())))

    for rs in ruleset_list:
        try:
            ids = _extract_matching_active_game_ids(
                raw,
                p1_wf=p1_wf,
                p2_wf=p2_wf,
                ruleset=int(rs),
            )
            for gid in ids:
                found.add(int(gid))
        except Exception:
            pass

    games = raw.get("games")
    if isinstance(games, list):
        wanted = {p1_wf.strip().lower(), p2_wf.strip().lower()}

        for g in games:
            if not isinstance(g, dict):
                continue
            if _boolish_ended(g):
                continue

            names = _players_from_game_summary(g)
            if not wanted.issubset(names):
                continue

            # If ruleset is parseable, enforce the allowed ruleset set.
            # If not parseable, keep it because this script is specifically
            # targeted by both players.
            rs_raw = g.get("ruleset") if g.get("ruleset") is not None else g.get("ruleset_id") or g.get("rulesetId")
            if rs_raw is not None:
                try:
                    if int(rs_raw) not in set(ruleset_list):
                        continue
                except Exception:
                    pass

            gid = _game_id_from_obj(g)
            if gid:
                found.add(int(gid))

    return sorted(found)


def is_game_still_active(
    api: DictAPIClient,
    *,
    observer_bot_id: str,
    game_id: int,
    p1_wf: str,
    p2_wf: str,
) -> bool:
    try:
        raw = games_raw(api, observer_bot_id, timeout_s=20.0)
    except Exception:
        # If games_raw has a transient problem, fall back to game_details.
        try:
            gd = game_details(api, observer_bot_id, game_id, timeout_s=20.0)
            return not _boolish_ended(gd)
        except Exception:
            return True

    ids = find_active_game_ids_between(
        raw,
        p1_wf=p1_wf,
        p2_wf=p2_wf,
        rulesets=sorted(set(LANG_TO_RULESET.values())),
    )
    return int(game_id) in set(ids)


def current_turn_slot(
    api: DictAPIClient,
    *,
    game_id: int,
    p1_bot_id: str,
    p2_bot_id: str,
    p1_wf: str,
    p2_wf: str,
) -> Optional[str]:
    """
    Poll as P1 first, then P2. Return 'player1', 'player2', or None.
    """
    last_gd: Dict[str, Any] = {}

    for bot_id in (p1_bot_id, p2_bot_id):
        try:
            gd = game_details(api, bot_id, game_id, timeout_s=30.0)
            last_gd = gd
        except Exception as e:
            log(f"WARN: game_details failed game_id={game_id} bot_id={bot_id!r}: {e!r}")
            continue

        if _boolish_ended(gd):
            return None

        slot = _extract_current_turn_slot(gd, p1_wf=p1_wf, p2_wf=p2_wf)
        if slot in ("player1", "player2"):
            return slot

        # Conservative fallback for string username fields.
        inner = gd.get("game") if isinstance(gd.get("game"), dict) else gd
        for k in (
            "current_player_username",
            "current_player_name",
            "turn_username",
            "player_to_move_username",
            "next_player_username",
        ):
            v = inner.get(k)
            if isinstance(v, str) and v.strip():
                u = v.strip().lower()
                if u == p1_wf.strip().lower():
                    return "player1"
                if u == p2_wf.strip().lower():
                    return "player2"

    log(f"WARN: could not determine current turn for game_id={game_id}; last_details_keys={sorted(last_gd.keys()) if last_gd else []}")
    return None


# ------------------------
# Pass-out loop
# ------------------------

def pass_out_one_game(
    api: DictAPIClient,
    *,
    p1_client: WordfeudClientLite,
    game_id: int,
    p1_bot_id: str,
    p2_bot_id: str,
    p1_wf: str,
    p2_wf: str,
    max_passes: int,
    pass_timeout_s: float,
    between_pass_s: float,
) -> Tuple[bool, int]:
    passes = 0
    expected_slot: Optional[str] = None
    log(f"Game {game_id}: pass-out starting")

    while passes < max_passes:
        if not is_game_still_active_direct(
            p1_client,
            game_id=game_id,
            p1_wf=p1_wf,
            p2_wf=p2_wf,
        ):
            log(f"Game {game_id}: ended after {passes} pass(es)")
            return True, passes

        slot = expected_slot or current_turn_slot_direct(
            p1_client,
            game_id=game_id,
            p1_wf=p1_wf,
            p2_wf=p2_wf,
        )
        if slot == "player1":
            bot_id = p1_bot_id
            wf = p1_wf
        elif slot == "player2":
            bot_id = p2_bot_id
            wf = p2_wf
        else:
            if not is_game_still_active_direct(
                p1_client,
                game_id=game_id,
                p1_wf=p1_wf,
                p2_wf=p2_wf,
            ):
                log(f"Game {game_id}: ended after {passes} pass(es)")
                return True, passes

            raise RuntimeError(f"Game {game_id}: cannot determine player in turn; refusing blind pass")

        log(f"Game {game_id}: pass #{passes + 1} by {wf} ({slot}, bot_id={bot_id})")
        rec = enqueue_and_wait_pass(api, bot_id, game_id, timeout_s=pass_timeout_s)

        if rec.get("status") != "done":
            if pass_rec_says_game_over(rec):
                log(f"Game {game_id}: game_over reported after {passes} completed pass(es)")
                return True, passes
            raise RuntimeError(f"Game {game_id}: pass failed/pending: rec={rec!r}")

        passes += 1
        expected_slot = "player2" if slot == "player1" else "player1"
        time.sleep(max(0.0, float(between_pass_s)))

    still_active = is_game_still_active_direct(
        p1_client,
        game_id=game_id,
        p1_wf=p1_wf,
        p2_wf=p2_wf,
    )

    if still_active:
        log(f"Game {game_id}: still active after max_passes={max_passes}")
        return False, passes

    log(f"Game {game_id}: ended after {passes} pass(es)")
    return True, passes

def main() -> int:
    ap = argparse.ArgumentParser(description="Pass out all active games between two bots.")
    ap.add_argument("--p1", default="WillOrange", help="Player 1 bot label in bots.json")
    ap.add_argument("--p2", default="Munin", help="Player 2 bot label in bots.json")
    ap.add_argument("--bots-json", default=str(BOTS_JSON), help="Path to bots.json")
    ap.add_argument("--max-passes-per-game", type=int, default=3)
    ap.add_argument("--pass-timeout-s", type=float, default=90.0)
    ap.add_argument("--between-pass-s", type=float, default=0.75)
    ap.add_argument("--dry-run", action="store_true", help="List matching games but do not pass.")
    args = ap.parse_args()

    bots_path = Path(args.bots_json)
    label_to_bot_id, label_to_wf = bot_maps(bots_path)

    p1_label = str(args.p1).strip()
    p2_label = str(args.p2).strip()

    p1_bot_id = label_to_bot_id.get(p1_label, "").strip()
    p2_bot_id = label_to_bot_id.get(p2_label, "").strip()
    p1_wf = label_to_wf.get(p1_label, p1_label).strip()
    p2_wf = label_to_wf.get(p2_label, p2_label).strip()

    if not p1_bot_id or not p2_bot_id:
        raise RuntimeError(
            f"Missing bot id(s) in {bots_path}: "
            f"{p1_label}->{p1_bot_id!r}, {p2_label}->{p2_bot_id!r}"
        )

    print_startup_config("PassOutGames", include_local_services=True)
    log(f"WF root: {WF_ROOT}")
    log(f"Script dir: {SCRIPT_DIR}")
    log(f"Dbuild dir: {DBUILD_DIR}")
    log(f"API dir: {API_DIR}")
    log(f"bots.json: {bots_path}")
    log(f"DictAPI URL: {get_dictapi_url()}")
    log(f"Players: {p1_label}={p1_wf} ({p1_bot_id}) | {p2_label}={p2_wf} ({p2_bot_id})")

    api = make_api()

    # Login both bots up front so later action/poll requests do not start from
    # stale session state.
    for bot_id in (p1_bot_id, p2_bot_id):
        log(f"Login/check session: bot_id={bot_id}")
        api.login_bot(bot_id)

    p1_client = make_direct_wf_client(p1_label, bots_path)

    game_ids = discover_p1_turn_games_from_p1_pov(
        p1_client,
        p1_wf=p1_wf,
        p2_wf=p2_wf,
    )

    log(f"Found {len(game_ids)} active {p1_wf}-turn game(s) vs {p2_wf}: {game_ids}")

    if args.dry_run:
        log("DRY-RUN: no passes sent.")
        return 0

    ok_count = 0
    fail_count = 0
    total_passes = 0

    for gid in game_ids:
        try:
            ok, n = pass_out_one_game(
                api,
                p1_client=p1_client,
                game_id=int(gid),
                p1_bot_id=p1_bot_id,
                p2_bot_id=p2_bot_id,
                p1_wf=p1_wf,
                p2_wf=p2_wf,
                max_passes=max(1, int(args.max_passes_per_game)),
                pass_timeout_s=float(args.pass_timeout_s),
                between_pass_s=float(args.between_pass_s),
            )
            total_passes += int(n)
            if ok:
                ok_count += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            log(f"ERROR: game {gid}: {e!r}")

    log(
        f"Done. games_ended={ok_count} games_failed_or_still_active={fail_count} "
        f"passes_sent={total_passes}"
    )

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
