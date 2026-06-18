from __future__ import annotations

"""
EndGamesBetween.py

Usage:
    python EndGamesBetween.py BotLabel1 BotLabel2

Standalone utility:
- Takes exactly two positional bot labels.
- Looks both bots up in config/bots.json, or WF_BOTS_JSON if set.
- Logs in directly as both Wordfeud users using credentials from bots.json.
- Finds all active games exactly between those two players.
- Passes with whichever player is currently in turn until each game ends.
- Uses the existing consecutive-pass count from game details when it can be
  parsed, so games with 0/1/2 preceding passes need 3/2/1 further passes.
- Best-effort clears finished games if the direct Wordfeud endpoint is available.

Expected project layout:
    E:\OneDrive\WF\apps\Simulator\EndGamesBetween.py      (or Scripts)
    E:\OneDrive\WF\apps\API\api\wf_client.py
    E:\OneDrive\WF\config\bots.json
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


def _find_wf_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "config" / "bots.json").exists() and (p / "apps" / "API").exists():
            return p
    # Works for E:\OneDrive\WF\apps\Simulator\script.py and legacy E:\OneDrive\WF\Scripts\script.py
    if start.name.lower() in {"simulator", "builder", "api", "db"} and start.parent.name.lower() == "apps":
        return start.parent.parent
    if start.name.lower() == "scripts":
        return start.parent
    return start.parent


WF_ROOT = _find_wf_root(SCRIPT_DIR)
API_DIR = WF_ROOT / "apps" / "API"
BOTS_JSON = Path(os.getenv("WF_BOTS_JSON") or (WF_ROOT / "config" / "bots.json"))

for p in (str(SCRIPT_DIR), str(API_DIR), str(WF_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.wf_client import WordfeudClientLite, WFHttpError, WFApiError  # type: ignore


# ------------------------
# Console helpers
# ------------------------

def ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def log(msg: str) -> None:
    print(f"{ts()} – {msg}", flush=True)


# ------------------------
# Generic parsing helpers
# ------------------------

def _as_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _lower(s: Any) -> str:
    return str(s or "").strip().lower()


def _unwrap_content(obj: Any) -> Any:
    if isinstance(obj, dict) and "content" in obj:
        return obj.get("content")
    return obj


def _game_id_from_obj(g: Any) -> Optional[int]:
    if not isinstance(g, dict):
        return None
    for obj in (g, g.get("game") if isinstance(g.get("game"), dict) else None):
        if not isinstance(obj, dict):
            continue
        for k in ("id", "game_id", "gameId"):
            try:
                v = obj.get(k)
                if v is not None and int(v) > 0:
                    return int(v)
            except Exception:
                pass
    return None


def _boolish_ended(game: Any) -> bool:
    inner = game.get("game") if isinstance(game, dict) and isinstance(game.get("game"), dict) else game
    if not isinstance(inner, dict):
        return True

    for k in ("is_running", "running", "active", "is_active"):
        if k in inner and inner.get(k) is False:
            return True

    for k in (
        "ended", "is_ended", "finished", "is_finished",
        "game_over", "is_game_over", "completed", "is_completed",
        "resigned", "is_resigned",
    ):
        if inner.get(k) is True:
            return True

    status = _lower(inner.get("status") or inner.get("state") or inner.get("game_status"))
    return status in {"ended", "finished", "complete", "completed", "game_over", "closed", "archived"}


def _players_from_obj(obj: Any) -> Set[str]:
    names: Set[str] = set()
    if not isinstance(obj, dict):
        return names

    players = obj.get("players")
    if isinstance(players, list):
        for p in players:
            if isinstance(p, dict):
                for k in ("username", "name", "nick", "display_name"):
                    v = p.get(k)
                    if isinstance(v, str) and v.strip():
                        names.add(v.strip().lower())
            elif isinstance(p, str) and p.strip():
                names.add(p.strip().lower())

    for k in (
        "player1", "player2", "player_1", "player_2", "inviter", "invitee",
        "opponent_username", "opponent", "opponent_name", "username", "user_name",
    ):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            names.add(v.strip().lower())
        elif isinstance(v, dict):
            names |= _players_from_obj(v)

    inner = obj.get("game")
    if isinstance(inner, dict):
        names |= _players_from_obj(inner)

    return names


def _summary_is_my_turn(g: Any) -> Optional[bool]:
    if not isinstance(g, dict):
        return None
    for k in (
        "is_your_turn", "your_turn", "is_my_turn", "my_turn", "user_turn",
        "is_current_user_turn", "current_user_turn", "move", "should_move", "can_move",
    ):
        if k in g and isinstance(g.get(k), bool):
            return bool(g.get(k))
    return None


def _walk_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_dicts(v)


def _extract_username_from_player_obj(obj: Any) -> str:
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        for k in ("username", "name", "nick", "display_name"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _current_turn_username(details: Dict[str, Any]) -> str:
    # Direct username fields seen in similar WF payloads.
    for d in _walk_dicts(details):
        for k in (
            "current_player_username", "current_player_name", "turn_username",
            "player_to_move_username", "next_player_username", "current_user_name",
        ):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    # Current player as object.
    for d in _walk_dicts(details):
        for k in ("current_player", "player_to_move", "next_player", "turn_player"):
            u = _extract_username_from_player_obj(d.get(k))
            if u:
                return u

    return ""


def _side_from_details(details: Dict[str, Any], bot1_wf: str, bot2_wf: str) -> Optional[str]:
    # WF game_details current_player is from the requesting account's POV:
    #   0 = requester is in turn
    #   1 = opponent is in turn
    # This function is only username-based, so keep old logic here.
    u = _lower(_current_turn_username(details))
    if u == _lower(bot1_wf):
        return "bot1"
    if u == _lower(bot2_wf):
        return "bot2"
    return None


def _extract_consecutive_passes(details: Dict[str, Any]) -> Optional[int]:
    """
    Return current trailing/consecutive pass count when visible.
    Expected useful values are 0, 1, or 2. If the payload shape is unknown,
    this function returns None and the caller safely falls back to 3 passes.
    """
    preferred_keys = (
        "consecutive_passes", "consecutive_pass_count", "pass_count", "passes_in_a_row",
        "num_passes", "number_of_passes", "pass_streak", "passes",
    )
    for d in _walk_dicts(details):
        for k in preferred_keys:
            if k in d:
                try:
                    n = int(d.get(k))
                    if 0 <= n <= 2:
                        return n
                except Exception:
                    pass

    # Fallback: infer from trailing move/action list if available.
    for d in _walk_dicts(details):
        moves = d.get("moves") or d.get("move_history") or d.get("history") or d.get("turns")
        if not isinstance(moves, list) or not moves:
            continue
        trailing = 0
        for mv in reversed(moves):
            if isinstance(mv, dict):
                kind = _lower(mv.get("type") or mv.get("kind") or mv.get("move_type") or mv.get("action"))
                word = _lower(mv.get("word"))
                tiles = mv.get("tiles") or mv.get("placements") or mv.get("move")
                is_pass = kind == "pass" or word == "pass" or tiles == []
            else:
                is_pass = _lower(mv) == "pass"
            if is_pass:
                trailing += 1
                if trailing >= 2:
                    break
            else:
                break
        if 0 <= trailing <= 2:
            return trailing

    return None


# ------------------------
# Bot loading / direct login
# ------------------------

def load_bots(path: Path = BOTS_JSON) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"bots.json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    bots = data.get("bots", data) if isinstance(data, dict) else data
    if not isinstance(bots, list):
        raise RuntimeError(f"Invalid bots.json shape: {path}")
    return [b for b in bots if isinstance(b, dict)]


def bot_record_by_label(label: str, path: Path = BOTS_JSON) -> Dict[str, Any]:
    wanted = _lower(label)
    bots = load_bots(path)

    for b in bots:
        candidates = (
            b.get("label"),
            b.get("id"),
            b.get("wf_username"),
            b.get("username"),
            b.get("name"),
        )
        if any(_lower(v) == wanted for v in candidates):
            return b

    available = []
    for b in bots:
        shown = str(b.get("label") or b.get("wf_username") or b.get("username") or b.get("id") or "").strip()
        if shown:
            available.append(shown)

    available_s = ", ".join(sorted(available, key=str.lower)) or "(none)"
    raise RuntimeError(
        f"Bot {label!r} was not found in {path}.\n"
        f"Available bot labels/usernames: {available_s}"
    )


def bot_fields(label: str, path: Path = BOTS_JSON) -> Tuple[str, str, str, str]:
    b = bot_record_by_label(label, path)
    bot_id = str(b.get("id") or "").strip()
    wf_username = str(b.get("wf_username") or b.get("username") or label).strip()
    email = str(b.get("email") or "").strip()
    password = str(b.get("password") or b.get("pw") or "").strip()
    if not bot_id:
        raise RuntimeError(f"Bot {label!r} is missing id in {path}")
    if not email or not password:
        raise RuntimeError(f"Bot {label!r} is missing email/password in {path}")
    return bot_id, wf_username, email, password


def make_direct_wf_client(label: str, path: Path = BOTS_JSON) -> Tuple[WordfeudClientLite, str, str]:
    bot_id, wf_username, email, password = bot_fields(label, path)
    base_url = (os.getenv("WF_BASE_URL") or "https://api.wordfeud.com/wf").strip()
    timeout_s = float((os.getenv("WF_HTTP_TIMEOUT_S") or "45").strip())
    cli = WordfeudClientLite(base_url=base_url, timeout_s=timeout_s, debug=False)
    try:
        cli.username = wf_username
    except Exception:
        pass
    log(f"Direct WF login: label={label!r} wf_username={wf_username!r}")
    cli.login_with_email(email=email, password=password)
    return cli, bot_id, wf_username


# ------------------------
# Wordfeud direct helpers
# ------------------------

def direct_games(client: WordfeudClientLite) -> List[Dict[str, Any]]:
    raw = client.games_raw() or []
    if isinstance(raw, dict):
        raw = _unwrap_content(raw)
        games = raw.get("games") if isinstance(raw, dict) else raw
    else:
        games = raw
    return [g for g in games] if isinstance(games, list) else []


def direct_game_details(client: WordfeudClientLite, game_id: int) -> Dict[str, Any]:
    raw = client.game_details(int(game_id)) or {}
    raw = _unwrap_content(raw)
    return raw if isinstance(raw, dict) else {"raw": raw}


def _summary_matches_exactly_between(g: Dict[str, Any], self_wf: str, other_wf: str) -> bool:
    if _boolish_ended(g):
        return False

    wanted_self = _lower(self_wf)
    wanted_other = _lower(other_wf)

    # In /user/games/ the opponent field is the most reliable exact filter.
    for k in ("opponent_username", "opponent", "opponent_name"):
        v = g.get(k)
        if isinstance(v, str) and _lower(v) == wanted_other:
            return True
        if isinstance(v, dict) and _lower(_extract_username_from_player_obj(v)) == wanted_other:
            return True

    names = _players_from_obj(g)
    return wanted_self in names and wanted_other in names


def discover_games_from_pov(client: WordfeudClientLite, *, self_wf: str, other_wf: str) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for g in direct_games(client):
        if not isinstance(g, dict):
            continue
        if not _summary_matches_exactly_between(g, self_wf, other_wf):
            continue
        gid = _game_id_from_obj(g)
        if gid:
            out[int(gid)] = g
    return out


def is_game_still_active_any(client1: WordfeudClientLite, client2: WordfeudClientLite, game_id: int) -> bool:
    for cli in (client1, client2):
        try:
            gd = direct_game_details(cli, int(game_id))
            if gd and not _boolish_ended(gd):
                return True
        except Exception:
            pass
    return False


def current_side(
    client1: WordfeudClientLite,
    client2: WordfeudClientLite,
    *,
    game_id: int,
    bot1_wf: str,
    bot2_wf: str,
    summary1: Optional[Dict[str, Any]] = None,
    summary2: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    # Summary flags are from that logged-in account's POV.
    s1_turn = _summary_is_my_turn(summary1 or {})
    s2_turn = _summary_is_my_turn(summary2 or {})
    if s1_turn is True:
        return "bot1"
    if s2_turn is True:
        return "bot2"

    for cli in (client1, client2):
        try:
            gd = direct_game_details(cli, game_id)

            cp = None
            players = None

            for d in _walk_dicts(gd):
                if cp is None and "current_player" in d:
                    cp = d.get("current_player")
                if players is None and isinstance(d.get("players"), list):
                    players = d.get("players")
                if cp is not None and players is not None:
                    break

            if cp is not None and isinstance(players, list):
                try:
                    cp_i = int(cp)
                except Exception:
                    cp_i = None

                if cp_i is not None:
                    for p in players:
                        if not isinstance(p, dict):
                            continue
                        try:
                            pos = int(p.get("position"))
                        except Exception:
                            continue

                        if pos != cp_i:
                            continue

                        u = _lower(p.get("username"))
                        if u == _lower(bot1_wf):
                            return "bot1"
                        if u == _lower(bot2_wf):
                            return "bot2"

            side = _side_from_details(gd, bot1_wf, bot2_wf)
            if side in {"bot1", "bot2"}:
                return side

        except Exception as e:
            log(f"WARN: game_details failed while determining turn game_id={game_id}: {e!r}")

    return None

def initial_passes_needed(client1: WordfeudClientLite, client2: WordfeudClientLite, game_id: int) -> Tuple[int, Optional[int]]:
    for cli in (client1, client2):
        try:
            gd = direct_game_details(cli, game_id)
            n = _extract_consecutive_passes(gd)
            if n is not None:
                return max(1, 3 - int(n)), int(n)
        except Exception:
            pass
    return 3, None


def pass_with_client(client: WordfeudClientLite, game_id: int) -> Any:
    return client.pass_turn(int(game_id))


def best_effort_clear_finished(client: WordfeudClientLite, game_id: int) -> bool:
    """
    Wordfeud's private API endpoint for the app's Clear action is not exposed by
    the current wf_client.py. Try likely direct endpoints without failing the run.
    """
    paths = [
        f"/game/{int(game_id)}/clear/",
        f"/game/{int(game_id)}/delete/",
        f"/game/{int(game_id)}/remove/",
        f"/user/games/{int(game_id)}/clear/",
    ]
    req = getattr(client, "_request", None)
    read = getattr(client, "_read_json_or_text", None)
    raise_api = getattr(client, "_raise_api_error_if_any", None)
    if not callable(req) or not callable(read):
        return False

    for method in ("POST", "DELETE"):
        for path in paths:
            try:
                res = req(method, path)
                json_obj, text = read(res)
                if not getattr(res, "ok", False):
                    continue
                if callable(raise_api):
                    raise_api(json_obj, text, op="Clear finished game")
                return True
            except Exception:
                continue
    return False


# ------------------------
# Pass-out loop
# ------------------------

def pass_out_one_game(
    *,
    game_id: int,
    client1: WordfeudClientLite,
    client2: WordfeudClientLite,
    bot1_wf: str,
    bot2_wf: str,
    summary1: Optional[Dict[str, Any]],
    summary2: Optional[Dict[str, Any]],
) -> Tuple[bool, int]:
    passes_sent = 0
    needed, preceding = initial_passes_needed(client1, client2, game_id)
    if preceding is None:
        log(f"Game {game_id}: pass-out starting; preceding_passes=unknown, using max 3")
    else:
        log(f"Game {game_id}: pass-out starting; preceding_passes={preceding}, passes_needed={needed}")

    expected: Optional[str] = None
    max_attempts = max(1, min(3, int(needed)))

    while passes_sent < max_attempts:
        if not is_game_still_active_any(client1, client2, game_id):
            log(f"Game {game_id}: ended after {passes_sent} pass(es)")
            return True, passes_sent

        side = expected or current_side(
            client1,
            client2,
            game_id=game_id,
            bot1_wf=bot1_wf,
            bot2_wf=bot2_wf,
            summary1=summary1 if passes_sent == 0 else None,
            summary2=summary2 if passes_sent == 0 else None,
        )
        if side == "bot1":
            cli = client1
            wf = bot1_wf
        elif side == "bot2":
            cli = client2
            wf = bot2_wf
        else:
            raise RuntimeError(f"Game {game_id}: cannot determine player in turn; refusing blind pass")

        log(f"Game {game_id}: pass #{passes_sent + 1} by {wf}")
        try:
            pass_with_client(cli, game_id)
        except WFApiError as e:
            if _lower(getattr(e, "error_type", "")) == "game_over" or "game over" in _lower(e):
                log(f"Game {game_id}: game_over reported after {passes_sent} completed pass(es)")
                return True, passes_sent
            raise
        except WFHttpError as e:
            msg = _lower(str(e)) + " " + _lower(getattr(e, "body", ""))
            if "game_over" in msg or "game over" in msg:
                log(f"Game {game_id}: game_over reported after {passes_sent} completed pass(es)")
                return True, passes_sent
            raise

        passes_sent += 1
        expected = "bot2" if side == "bot1" else "bot1"
        time.sleep(float(os.getenv("ENDGAMES_BETWEEN_PASS_S") or "4.75"))

    if not is_game_still_active_any(client1, client2, game_id):
        log(f"Game {game_id}: ended after {passes_sent} pass(es)")
        return True, passes_sent

    # Defensive final check: if pass count was underreported, try up to 3 total.
    while passes_sent < 3 and is_game_still_active_any(client1, client2, game_id):
        side = expected or current_side(client1, client2, game_id=game_id, bot1_wf=bot1_wf, bot2_wf=bot2_wf)
        if side == "bot1":
            cli = client1
            wf = bot1_wf
        elif side == "bot2":
            cli = client2
            wf = bot2_wf
        else:
            break
        log(f"Game {game_id}: extra defensive pass #{passes_sent + 1} by {wf}")
        pass_with_client(cli, game_id)
        passes_sent += 1
        expected = "bot2" if side == "bot1" else "bot1"
        time.sleep(float(os.getenv("ENDGAMES_BETWEEN_PASS_S") or "4.75"))

    ended = not is_game_still_active_any(client1, client2, game_id)
    if ended:
        log(f"Game {game_id}: ended after {passes_sent} pass(es)")
    else:
        log(f"Game {game_id}: still active after {passes_sent} pass(es)")
    return ended, passes_sent


# ------------------------
# Main
# ------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="End all active games exactly between two bots by passing them out.")
    ap.add_argument("bot1", help="First bot label in bots.json")
    ap.add_argument("bot2", help="Second bot label in bots.json")
    args = ap.parse_args(argv)

    bot1_label = str(args.bot1).strip()
    bot2_label = str(args.bot2).strip()
    if not bot1_label or not bot2_label or bot1_label == bot2_label:
        raise RuntimeError("Provide exactly two different bot labels.")

    log(f"WF root: {WF_ROOT}")
    log(f"API dir: {API_DIR}")
    log(f"bots.json: {BOTS_JSON}")

    client1, bot1_id, bot1_wf = make_direct_wf_client(bot1_label, BOTS_JSON)
    client2, bot2_id, bot2_wf = make_direct_wf_client(bot2_label, BOTS_JSON)

    log(f"Players: {bot1_label}={bot1_wf} ({bot1_id}) | {bot2_label}={bot2_wf} ({bot2_id})")

    games1 = discover_games_from_pov(client1, self_wf=bot1_wf, other_wf=bot2_wf)
    games2 = discover_games_from_pov(client2, self_wf=bot2_wf, other_wf=bot1_wf)
    game_ids = sorted(set(games1) | set(games2))

    log(f"Found {len(game_ids)} active game(s) exactly between {bot1_wf} and {bot2_wf}: {game_ids}")

    ok_count = 0
    fail_count = 0
    total_passes = 0
    cleared_count = 0

    for gid in game_ids:
        try:
            ok, n = pass_out_one_game(
                game_id=int(gid),
                client1=client1,
                client2=client2,
                bot1_wf=bot1_wf,
                bot2_wf=bot2_wf,
                summary1=games1.get(int(gid)),
                summary2=games2.get(int(gid)),
            )
            total_passes += int(n)
            if ok:
                ok_count += 1
                cleared1 = best_effort_clear_finished(client1, int(gid))
                cleared2 = best_effort_clear_finished(client2, int(gid))
                if cleared1 or cleared2:
                    cleared_count += 1
                    log(f"Game {gid}: clear finished game succeeded for at least one account")
                else:
                    log(f"Game {gid}: clear finished game not available/supported by current API")
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            log(f"ERROR: game {gid}: {e!r}")

    log(
        f"Done. games_ended={ok_count} games_failed_or_still_active={fail_count} "
        f"passes_sent={total_passes} cleared={cleared_count}"
    )
    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(f"{ts()} – Cancelled.", flush=True)
        raise SystemExit(130)
    except RuntimeError as e:
        print(f"{ts()} – ERROR: {e}", flush=True)
        raise SystemExit(2)
