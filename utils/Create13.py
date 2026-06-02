"""
Create13RuleSetGames.py

Creates one Wordfeud game for each of the 13 rule sets 0..12.

Inviter: WillOrange
Invitee: Munin
Board:   0  (Wordfeud random board)

The script creates/accepts one invite at a time. It does not start DictGame
workers and exits after all 13 games have been created.

Run from the same folder structure as DictManager.py, for example:
    python Create13RuleSetGames.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import inspect


def add_wf_paths() -> Path:
    """
    Add import roots for the current WF layout.

      E:\OneDrive\WF              -> common\...
      E:\OneDrive\WF\apps\API     -> api\...
    """
    here = Path(__file__).resolve()

    for parent in [here.parent, *here.parents]:
        common_dir = parent / "common"
        apps_api_dir = parent / "apps" / "API"

        if (common_dir / "wf_config.py").exists() and (apps_api_dir / "api" / "wf_client.py").exists():
            for path in (parent, apps_api_dir):
                s = str(path)
                if s not in sys.path:
                    sys.path.insert(0, s)
            return parent

    raise RuntimeError(
        f"Could not locate WF root from {here}. Expected common\\wf_config.py "
        f"and apps\\API\\api\\wf_client.py under the same WF root."
    )


WF_ROOT = add_wf_paths()

from api.wf_client import WordfeudClientLite

log_import_source = getattr(sys.modules.get(WordfeudClientLite.__module__), "__file__", None)
print(f"Loaded WordfeudClientLite from: {log_import_source}", flush=True)

INVITER_LABEL = "WillOrange"
INVITEE_LABEL = "Munin"
RANDOM_BOARD = True
BOARD = None

RULESETS: List[int] = list(range(13))  # 0..12

# Board numbers wanted from Wordfeud random boards.
# Edit this list before running.
TARGET_GAMES = {1089}

MAX_KEPT_GAMES = 13

POLL_S = 1.0
INVITE_WAIT_S = 60.0
GAME_DISCOVERY_S = 90.0
PASS_OUT_MAX_PASSES = 80
PASS_OUT_SLEEP_MIN_S = 2.0
PASS_OUT_SLEEP_MAX_S = 3.0


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"{ts()} – {msg}", flush=True)


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = WF_ROOT / "Dbuild" / "Settings"


def load_bots(settings_dir: Path) -> List[Dict[str, Any]]:
    bots_path = settings_dir / "bots.json"
    if not bots_path.exists():
        raise FileNotFoundError(f"Missing bot settings: {bots_path}")

    data = json.loads(bots_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("bots"), list):
        bots = data["bots"]
    elif isinstance(data, list):
        bots = data
    else:
        raise RuntimeError(f"Unsupported bots.json format: {bots_path}")

    return [b for b in bots if isinstance(b, dict) and str(b.get("label", "")).strip()]


def bot_record_by_label(label: str) -> Dict[str, Any]:
    label_s = label.strip()
    for bot in load_bots(SETTINGS_DIR):
        if str(bot.get("label") or "").strip() == label_s:
            return bot
    raise RuntimeError(f"No bot record found for label={label_s!r} in {SETTINGS_DIR / 'bots.json'}")


def wf_username(bot: Dict[str, Any], fallback_label: str) -> str:
    return str(bot.get("wf_username") or bot.get("username") or fallback_label).strip()


def make_wf_client(label: str) -> WordfeudClientLite:
    bot = bot_record_by_label(label)
    email = str(bot.get("email") or "").strip()
    password = str(bot.get("password") or bot.get("pw") or "").strip()
    username = wf_username(bot, label)

    if not email or not password:
        raise RuntimeError(f"Bot {label!r} is missing email/password in Settings/bots.json")

    base_url = (os.getenv("WF_BASE_URL") or "https://game.wordfeud.com/wf").strip()
    timeout_s = float((os.getenv("WF_HTTP_TIMEOUT_S") or "20").strip())

    client = WordfeudClientLite(base_url=base_url, timeout_s=timeout_s, debug=False)
    try:
        client.username = username
    except Exception:
        pass

    log(f"Logging in {label} as {username!r}")
    client.login_with_email(email=email, password=password)
    return client


def to_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except Exception:
        return default


def get_obj(obj: object, name: str, default: object = None) -> object:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def iter_invites(status: Dict[str, object], kind: str) -> List[Dict[str, object]]:
    k1 = "invites_received" if kind == "received" else "invites_sent"
    v = status.get(k1)
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]

    inv = status.get("invites")
    if isinstance(inv, dict):
        k2 = "received" if kind == "received" else "sent"
        v2 = inv.get(k2)
        if isinstance(v2, list):
            return [x for x in v2 if isinstance(x, dict)]

    k3 = "invitations_received" if kind == "received" else "invitations_sent"
    v3 = status.get(k3)
    if isinstance(v3, list):
        return [x for x in v3 if isinstance(x, dict)]

    return []


def invite_user(inv: Dict[str, object], key1: str, key2: str, key3: str) -> str:
    v = inv.get(key1) or inv.get(key2) or inv.get(key3)
    if isinstance(v, dict):
        return str(v.get("username") or v.get("name") or "")
    return str(v or "")


def inviter_username(inv: Dict[str, object]) -> str:
    return invite_user(inv, "inviter", "from", "inviter_username")


def invitee_username(inv: Dict[str, object]) -> str:
    return invite_user(inv, "invitee", "to", "invitee_username")


def invite_ruleset(inv: Dict[str, object]) -> int:
    rs = inv.get("ruleset")
    if rs is not None:
        return to_int(rs, -1)
    rulesets = inv.get("rulesets")
    if isinstance(rulesets, list) and rulesets:
        r0 = rulesets[0]
        if isinstance(r0, dict):
            return to_int(r0.get("id"), -1)
    return -1


def extract_id(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        n = to_int(value, 0)
        return n if n > 0 else None
    if isinstance(value, dict):
        for key in ("id", "invitation_id", "game_id"):
            n = to_int(value.get(key), 0)
            if n > 0:
                return n
        for key in ("invitation", "result", "data", "raw", "game"):
            got = extract_id(value.get(key))
            if got:
                return got
    return None

# Location: after extract_id(...)
# Add this helper

def invite_random_board(
    client: WordfeudClientLite,
    *,
    username: str,
    ruleset: int,
) -> object:
    """
    Invite using an explicit random-board argument if wf_client supports one.

    Fatal rather than silently falling back to board=0, because board=0 is not
    explicit enough and may mean standard board depending on client/API mapping.
    """
    sig = inspect.signature(client.invite_by_username)
    params = sig.parameters

    if "random_board" in params:
        return client.invite_by_username(
            username=username,
            ruleset=int(ruleset),
            random_board=True,
        )

    if "board_type" in params:
        return client.invite_by_username(
            username=username,
            ruleset=int(ruleset),
            board_type="random",
        )

    if "board" in params and "random" in params:
        return client.invite_by_username(
            username=username,
            ruleset=int(ruleset),
            board=None,
            random=True,
        )

    raise RuntimeError(
        "WordfeudClientLite.invite_by_username() does not expose an explicit "
        "random-board argument. Add support in wf_client.py first; do not rely "
        "on board=0."
    )

def status(client: WordfeudClientLite) -> Dict[str, object]:
    raw = client.status() or {}
    return raw if isinstance(raw, dict) else {}


def games_raw(client: WordfeudClientLite) -> List[object]:
    raw = client.games_raw() or {}
    if isinstance(raw, dict):
        games = raw.get("games") or raw.get("content") or []
        return list(games) if isinstance(games, list) else []
    if isinstance(raw, list):
        return list(raw)
    return []


def matching_active_game_ids(
    client: WordfeudClientLite,
    *,
    p1_wf: str,
    p2_wf: str,
    ruleset: int,
) -> List[int]:
    wanted = {p1_wf.strip().lower(), p2_wf.strip().lower()}
    out: List[int] = []

    for g in games_raw(client):
        gid = to_int(get_obj(g, "id"), 0)
        if gid <= 0:
            continue
        if not bool(get_obj(g, "is_running", False)):
            continue
        if to_int(get_obj(g, "ruleset"), -999) != int(ruleset):
            continue

        opp = str(get_obj(g, "opponent_username", "") or "").strip().lower()
        if opp and opp not in wanted:
            continue

        out.append(gid)

    return out


def find_sent_invite(
    client: WordfeudClientLite,
    *,
    invitee_wf: str,
    ruleset: int,
) -> Optional[int]:
    invitee_l = invitee_wf.strip().lower()
    for inv in iter_invites(status(client), "sent"):
        if invitee_username(inv).strip().lower() != invitee_l:
            continue
        if invite_ruleset(inv) != int(ruleset):
            continue
        inv_id = extract_id(inv)
        if inv_id:
            return int(inv_id)
    return None


def find_received_invite(
    client: WordfeudClientLite,
    *,
    inviter_wf: str,
    ruleset: int,
) -> Optional[int]:
    inviter_l = inviter_wf.strip().lower()
    for inv in iter_invites(status(client), "received"):
        if inviter_username(inv).strip().lower() != inviter_l:
            continue
        if invite_ruleset(inv) != int(ruleset):
            continue
        inv_id = extract_id(inv)
        if inv_id:
            return int(inv_id)
    return None


def wait_for_received_invite(
    client: WordfeudClientLite,
    *,
    inviter_wf: str,
    ruleset: int,
    timeout_s: float,
) -> int:
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        inv_id = find_received_invite(client, inviter_wf=inviter_wf, ruleset=ruleset)
        if inv_id:
            return int(inv_id)
        time.sleep(POLL_S)
    raise RuntimeError(f"Timed out waiting for invite from {inviter_wf!r}, ruleset={ruleset}")


def wait_for_new_game(
    client: WordfeudClientLite,
    *,
    existing_ids: Iterable[int],
    p1_wf: str,
    p2_wf: str,
    ruleset: int,
    timeout_s: float,
) -> int:
    existing = {int(x) for x in existing_ids or []}
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        ids = matching_active_game_ids(client, p1_wf=p1_wf, p2_wf=p2_wf, ruleset=ruleset)
        for gid in ids:
            if int(gid) not in existing:
                return int(gid)
        time.sleep(POLL_S)
    raise RuntimeError(f"Timed out waiting for new game, ruleset={ruleset}")


def create_one_game(
    *,
    p1_client: WordfeudClientLite,
    p2_client: WordfeudClientLite,
    p1_wf: str,
    p2_wf: str,
    ruleset: int,
) -> Dict[str, int]:
    log(f"Ruleset {ruleset}: checking existing active games")
    existing_ids = matching_active_game_ids(
        p1_client,
        p1_wf=p1_wf,
        p2_wf=p2_wf,
        ruleset=ruleset,
    )

    invitation_id = find_sent_invite(p1_client, invitee_wf=p2_wf, ruleset=ruleset)
    if invitation_id:
        log(f"Ruleset {ruleset}: using existing sent invite id={invitation_id}")
    else:
        log(
            f"Ruleset {ruleset}: waiting 5s before invite "
            f"to {p2_wf!r}"
        )
        time.sleep(5.0)

        log(f"Ruleset {ruleset}: inviting {p2_wf!r} with explicit random board")
        invite_ret = invite_random_board(
            p1_client,
            username=p2_wf,
            ruleset=int(ruleset),
        )
        invitation_id = extract_id(invite_ret)
        log(f"Ruleset {ruleset}: invite returned {invite_ret!r}, parsed_id={invitation_id!r}")

        if invitation_id is None:
            invitation_id = wait_for_received_invite(
                p2_client,
                inviter_wf=p1_wf,
                ruleset=ruleset,
                timeout_s=INVITE_WAIT_S,
            )

    invitation_id = int(invitation_id)

    log(f"Ruleset {ruleset}: accepting invitation_id={invitation_id}")
    accept_ret = p2_client.accept_invitation(invitation_id=invitation_id)
    game_id = extract_id({"id": accept_ret}) or extract_id(accept_ret)
    log(f"Ruleset {ruleset}: accept returned {accept_ret!r}, parsed_game_id={game_id!r}")

    if game_id is None:
        game_id = wait_for_new_game(
            p1_client,
            existing_ids=existing_ids,
            p1_wf=p1_wf,
            p2_wf=p2_wf,
            ruleset=ruleset,
            timeout_s=GAME_DISCOVERY_S,
        )

    log(f"Ruleset {ruleset}: created game_id={int(game_id)}")
    return {"ruleset": int(ruleset), "invitation_id": invitation_id, "game_id": int(game_id)}

def pass_once(
    client: WordfeudClientLite,
    *,
    game_id: int,
) -> object:
    for meth in ("pass_game", "pass_turn", "pass_move", "pass_"):
        fn = getattr(client, meth, None)
        if not callable(fn):
            continue
        try:
            return fn(int(game_id))
        except TypeError:
            return fn(game_id=int(game_id))

    raise RuntimeError(
        "WordfeudClientLite has no recognised pass method "
        "(tried pass_game, pass_turn, pass_move, pass_)"
    )

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

def game_inner_payload(client: WordfeudClientLite, game_id: int) -> Dict[str, Any]:
    gd = direct_game_details(client, int(game_id))
    inner = gd.get("game") if isinstance(gd.get("game"), dict) else gd
    return inner if isinstance(inner, dict) else {}


def extract_board_number_from_game(inner: Dict[str, Any]) -> Optional[int]:
    """
    Extract the random board/catalog number from the Wordfeud game payload.

    This intentionally checks several likely field names because Wordfeud payloads
    have varied across endpoints/client wrappers.
    """
    for key in (
        "board",
        "board_id",
        "board_number",
        "board_type",
        "random_board",
        "random_board_id",
        "random_board_number",
        "board_layout_id",
    ):
        if key not in inner:
            continue

        value = inner.get(key)

        if isinstance(value, dict):
            for subkey in ("id", "number", "board", "board_id"):
                n = to_int(value.get(subkey), 0)
                if n > 0:
                    return n

        n = to_int(value, 0)
        if n > 0:
            return n

    settings = inner.get("settings")
    if isinstance(settings, dict):
        got = extract_board_number_from_game(settings)
        if got:
            return got

    raise RuntimeError(
        f"Could not extract board number from game payload. "
        f"Available keys={sorted(inner.keys())}; payload={inner!r}"
    )


def game_is_running_from_inner(inner: Dict[str, Any]) -> bool:
    for key in ("is_running", "running", "active"):
        if key in inner:
            return bool(inner.get(key))

    for key in ("game_over", "is_finished", "finished", "ended"):
        if key in inner:
            return not bool(inner.get(key))

    # If no explicit finished/running field exists, assume it may still be active.
    return True


def pass_out_game(
    *,
    p1_client: WordfeudClientLite,
    p2_client: WordfeudClientLite,
    p1_wf: str,
    p2_wf: str,
    game_id: int,
) -> None:
    """
    Pass both sides until the game is no longer running.

    Fatal on ambiguous turn detection rather than risking passing the wrong side.
    """
    log(f"Game {game_id}: passing out unwanted board")

    for pass_no in range(1, PASS_OUT_MAX_PASSES + 1):
        inner = game_inner_payload(p1_client, int(game_id))
        if not game_is_running_from_inner(inner):
            log(f"Game {game_id}: pass-out complete after {pass_no - 1} passes")
            return

        if game_is_munin_turn(p2_client, game_id=int(game_id), munin_wf=p2_wf):
            log(f"Game {game_id}: pass-out #{pass_no}: {p2_wf} passing")
            ret = pass_once(p2_client, game_id=int(game_id))
        else:
            log(f"Game {game_id}: pass-out #{pass_no}: {p1_wf} passing")
            ret = pass_once(p1_client, game_id=int(game_id))

        log(f"Game {game_id}: pass-out #{pass_no} returned {ret!r}")

        delay_s = random.uniform(
            PASS_OUT_SLEEP_MIN_S,
            PASS_OUT_SLEEP_MAX_S,
        )
        log(
            f"Game {game_id}: waiting "
            f"{delay_s:.2f}s before next pass"
        )
        time.sleep(delay_s)

    raise RuntimeError(
        f"Game {game_id}: pass-out did not finish after "
        f"{PASS_OUT_MAX_PASSES} passes"
    )

def game_is_munin_turn(
    client: WordfeudClientLite,
    *,
    game_id: int,
    munin_wf: str,
) -> bool:
    gd = direct_game_details(client, int(game_id))
    inner = gd.get("game") if isinstance(gd.get("game"), dict) else gd

    if not isinstance(inner, dict):
        log(f"Game {game_id}: could not read game details; not passing blindly")
        return False

    current_player = inner.get("current_player")
    players = inner.get("players")

    if current_player is not None and isinstance(players, list):
        current_player_i = to_int(current_player, -1)

        for p in players:
            if not isinstance(p, dict):
                continue

            # Wordfeud payloads vary:
            #   player id may be at p["id"], p["user_id"], or nested in p["user"]
            #   username may be at p["username"], p["name"], or nested in p["user"]
            user = p.get("user") if isinstance(p.get("user"), dict) else {}

            ids = [
                p.get("position"),
                p.get("id"),
                p.get("user_id"),
                p.get("player_id"),
                user.get("id"),
                user.get("user_id"),
            ]

            if current_player_i not in {to_int(x, -999999) for x in ids}:
                continue

            username = str(
                p.get("username")
                or p.get("name")
                or p.get("nickname")
                or user.get("username")
                or user.get("name")
                or user.get("nickname")
                or ""
            ).strip()

            log(
                f"Game {game_id}: current_player={current_player!r}, "
                f"matched_player={p!r}, username={username!r}"
            )

            if not username:
                raise RuntimeError(
                    f"Game {game_id}: matched current_player={current_player!r} "
                    f"but could not extract username from player payload: {p!r}"
                )

            return username.lower() == munin_wf.strip().lower()

        raise RuntimeError(
            f"Game {game_id}: current_player={current_player!r} was not found "
            f"in players payload: {players!r}"
        )

    for k in (
        "current_player_username",
        "current_player_name",
        "turn_username",
        "player_to_move_username",
        "next_player_username",
    ):
        v = inner.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().lower() == munin_wf.strip().lower()

    for k in (
        "is_your_turn",
        "your_turn",
        "is_my_turn",
        "my_turn",
        "user_turn",
        "is_current_user_turn",
        "current_user_turn",
        "move",
        "should_move",
        "can_move",
    ):
        if k in inner and isinstance(inner.get(k), bool):
            return bool(inner.get(k))

    raise RuntimeError(
        f"Game {game_id}: no recognised turn field; keys={sorted(inner.keys())}; "
        f"payload={inner!r}"
    )

def main() -> int:
    log(f"WF root: {WF_ROOT}")
    log(f"Settings dir: {SETTINGS_DIR}")
    log(
        f"Creating {len(RULESETS)} games: {INVITER_LABEL} -> {INVITEE_LABEL}, "
        f"random_board={RANDOM_BOARD}"
    )

    p1_bot = bot_record_by_label(INVITER_LABEL)
    p2_bot = bot_record_by_label(INVITEE_LABEL)
    p1_wf = wf_username(p1_bot, INVITER_LABEL)
    p2_wf = wf_username(p2_bot, INVITEE_LABEL)

    p1_client = make_wf_client(INVITER_LABEL)
    p2_client = make_wf_client(INVITEE_LABEL)

    made: List[Dict[str, int]] = []
    made_boards: set[int] = set()
    target_boards = {int(x) for x in TARGET_GAMES}

    if not target_boards:
        raise RuntimeError("TARGET_GAMES is empty; add at least one board number.")

    log(f"Target board numbers: {sorted(target_boards)}")

    for ruleset in RULESETS:
        if len(made) >= MAX_KEPT_GAMES:
            log(f"Stopping: kept {len(made)} games")
            break

        if made_boards >= target_boards:
            log("Stopping: all TARGET_GAMES have been created")
            break

        while len(made) < MAX_KEPT_GAMES and not (made_boards >= target_boards):
            rec = create_one_game(
                p1_client=p1_client,
                p2_client=p2_client,
                p1_wf=p1_wf,
                p2_wf=p2_wf,
                ruleset=int(ruleset),
            )

            game_id = int(rec["game_id"])
            inner = game_inner_payload(p1_client, game_id)
            board_no = extract_board_number_from_game(inner)

            log(
                f"Ruleset {ruleset}: game_id={game_id} has board_no={board_no}"
            )

            if board_no not in target_boards:
                log(
                    f"Ruleset {ruleset}: board_no={board_no} is not in TARGET_GAMES; "
                    f"passing out and retrying same tile-set"
                )
                pass_out_game(
                    p1_client=p1_client,
                    p2_client=p2_client,
                    p1_wf=p1_wf,
                    p2_wf=p2_wf,
                    game_id=game_id,
                )
                time.sleep(1.0)
                continue

            if board_no in made_boards:
                log(
                    f"Ruleset {ruleset}: board_no={board_no} already created; "
                    f"passing out duplicate and retrying same tile-set"
                )
                pass_out_game(
                    p1_client=p1_client,
                    p2_client=p2_client,
                    p1_wf=p1_wf,
                    p2_wf=p2_wf,
                    game_id=game_id,
                )
                time.sleep(1.0)
                continue

            rec["board_no"] = int(board_no)
            made.append(rec)
            made_boards.add(int(board_no))

            log(
                f"Ruleset {ruleset}: keeping game_id={game_id}, "
                f"board_no={board_no}"
            )

            if game_is_munin_turn(
                p2_client,
                game_id=game_id,
                munin_wf=p2_wf,
            ):
                log(f"Ruleset {ruleset}: Munin is first mover; passing once so WillOrange becomes first mover")
                pass_ret = pass_once(p2_client, game_id=game_id)
                log(f"Ruleset {ruleset}: Munin pass returned {pass_ret!r}")
            else:
                log(f"Ruleset {ruleset}: WillOrange already appears to be first mover; no Munin pass needed")

            # Move to next tile-set after keeping one wanted board.
            time.sleep(1.0)
            break

    log("Completed kept games:")
    for rec in made:
        log(
            f"  ruleset={rec['ruleset']} board_no={rec['board_no']} "
            f"invitation_id={rec['invitation_id']} game_id={rec['game_id']}"
        )

    missing = sorted(target_boards - made_boards)
    if missing:
        log(f"Target boards not created in this run: {missing}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
