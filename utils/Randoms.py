from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import requests


PROBE_BOT = "WillOrange"
RANDOM_DIR = Path(os.getenv("WF_RANDOM_BOARD_DIR", r"E:\OneDrive\WF\Boards\Random"))
PROCESSED_DIR = Path(os.getenv("WF_RANDOM_BOARD_PROCESSED_DIR", r"E:\OneDrive\WF\Boards\Random\Processed"))
BOTS_JSON_DEFAULT = r"E:\OneDrive\WF\Dbuild\Settings\bots.json"
WF_BASE_URL = (os.getenv("WF_BASE_URL") or "https://game.wordfeud.com/wf").strip()
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

def _add_wf_paths() -> Path:
    here = Path(__file__).resolve()
    candidates = [here.parent, *here.parents, Path.cwd(), *Path.cwd().parents]

    for parent in candidates:
        dbuild = parent / "Dbuild"
        scripts = parent / "Scripts"
        api_client = dbuild / "API" / "api" / "wf_client.py"
        reim = scripts / "ReIm.py"

        if api_client.exists():
            for p in (parent, dbuild, scripts):
                if p.exists() and str(p) not in sys.path:
                    sys.path.insert(0, str(p))
            return parent

    # Fallback for normal E:\OneDrive\WF layout when launched elsewhere.
    fallback = Path(r"E:\OneDrive\WF")
    dbuild = fallback / "Dbuild"
    scripts = fallback / "Scripts"
    if (dbuild / "API" / "api" / "wf_client.py").exists():
        for p in (fallback, dbuild, scripts):
            if p.exists() and str(p) not in sys.path:
                sys.path.insert(0, str(p))
        return fallback

    raise RuntimeError(f"Could not locate WF root/Dbuild/API/api/wf_client.py from {here}")


WF_ROOT = _add_wf_paths()

from API.api.wf_client import WordfeudClientLite  # noqa: E402
import ReIm  # noqa: E402

try:
    from common.wf_config import get_db_manager_url  # noqa: E402
except Exception:  # pragma: no cover - runtime fallback
    get_db_manager_url = None


BOTS_JSON = Path(os.getenv("WF_BOTS_JSON", str(WF_ROOT / "Dbuild" / "Settings" / "bots.json" if WF_ROOT else BOTS_JSON_DEFAULT)))


def db_server_url() -> str:
    if get_db_manager_url is not None:
        try:
            return str(get_db_manager_url()).rstrip("/")
        except Exception:
            pass
    return (os.getenv("WF_DB_SERVER_URL") or os.getenv("DB_SERVER_URL") or "http://localhost:8787").rstrip("/")


def db_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = (os.getenv("WF_DB_API_KEY") or os.getenv("DB_API_KEY") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def db_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{db_server_url()}{path}"
    resp = requests.post(url, json=payload, headers=db_headers(), timeout=30)
    if resp.status_code == 404:
        raise RuntimeError(f"DB endpoint not found: {url}")
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return {}


def load_bots() -> list[dict[str, str]]:
    obj = json.loads(BOTS_JSON.read_text(encoding="utf-8"))

    if isinstance(obj, dict) and isinstance(obj.get("bots"), list):
        rows = obj["bots"]
    elif isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        rows = []
        for key, val in obj.items():
            if isinstance(val, dict):
                row = dict(val)
                row.setdefault("label", key)
                rows.append(row)
    else:
        rows = []

    out: list[dict[str, str]] = []
    for b in rows:
        if not isinstance(b, dict):
            continue
        if b.get("active") is False or b.get("enabled") is False or b.get("disabled") is True:
            continue
        label = str(b.get("label") or b.get("name") or "").strip()
        wf_username = str(b.get("wf_username") or b.get("username") or label).strip()
        email = str(b.get("email") or "").strip()
        password = str(b.get("password") or b.get("pw") or "").strip()
        if label and wf_username and email and password:
            out.append({
                "label": label,
                "wf_username": wf_username,
                "email": email,
                "password": password,
            })
    return out


def get_probe_bot() -> dict[str, str]:
    want = PROBE_BOT.lower()
    for bot in load_bots():
        if bot["label"].lower() == want or bot["wf_username"].lower() == want:
            return bot
    raise RuntimeError(f"Could not find active bot {PROBE_BOT!r} with email/password in {BOTS_JSON}")


def make_wf_client(bot: dict[str, str]) -> WordfeudClientLite:
    timeout_s = float((os.getenv("WF_HTTP_TIMEOUT_S") or "20").strip())
    cli = WordfeudClientLite(base_url=WF_BASE_URL, timeout_s=timeout_s, debug=False)
    try:
        cli.username = bot["wf_username"]
    except Exception:
        pass
    cli.login_with_email(email=bot["email"], password=bot["password"])
    return cli


def first_value(d: dict, keys: tuple[str, ...], default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def nested_game(g: dict) -> dict:
    inner = g.get("game") if isinstance(g, dict) else None
    return inner if isinstance(inner, dict) else g


def unwrap_state(value: dict[str, Any]) -> dict[str, Any]:
    state = value
    for key in ("game", "content", "result", "data"):
        wrapped = state.get(key) if isinstance(state, dict) else None
        if isinstance(wrapped, dict):
            state = wrapped
    return state if isinstance(state, dict) else value


def game_id(g: dict) -> Any:
    ng = nested_game(g)
    return first_value(ng, ("id", "game_id", "gid", "gameId"), first_value(g, ("id", "game_id", "gid", "gameId"), None))


def game_status(g: dict) -> str:
    ng = nested_game(g)
    raw = str(first_value(ng, ("status", "state", "game_status", "phase"), first_value(g, ("status", "state", "game_status", "phase"), ""))).lower()
    if any(x in raw for x in ("finish", "ended", "complete", "resigned", "timed_out", "timeout")):
        return "FINISHED"
    for obj in (ng, g):
        if not isinstance(obj, dict):
            continue
        if obj.get("finished") is True or obj.get("is_finished") is True or obj.get("ended") is True:
            return "FINISHED"
        if obj.get("is_running") is False:
            return "FINISHED"
    return "ACTIVE"


def game_ruleset(g: dict) -> Any:
    ng = nested_game(g)
    return first_value(ng, ("ruleset", "rule_set", "ruleset_id"), first_value(g, ("ruleset", "rule_set", "ruleset_id"), None))

def board_number(g: dict) -> Any:
    ng = nested_game(g)
    return first_value(ng, ("board", "board_id", "boardId"), first_value(g, ("board", "board_id", "boardId"), None))


def players(g: dict) -> list[dict[str, Any]]:
    ng = nested_game(g)
    for obj in (ng, g):
        ps = obj.get("players") if isinstance(obj, dict) else None
        if isinstance(ps, list):
            return [p for p in ps if isinstance(p, dict)]
    return []


def current_turn_value(g: dict) -> Any:
    for obj in (nested_game(g), g):
        if not isinstance(obj, dict):
            continue
        cur = first_value(obj, ("current_player", "current_turn", "turn", "turn_player", "player_turn", "player_to_move", "next_player"), None)
        if cur is not None:
            if isinstance(cur, dict):
                return first_value(cur, ("username", "name", "nickname", "display_name", "nick", "id"), None)
            return cur
    return None


def player_name(p: dict[str, Any]) -> str:
    return str(first_value(p, ("username", "name", "nickname", "display_name", "nick", "id"), "")).strip()


def player_from_position(g: dict, pos_value: Any) -> str | None:
    try:
        pos_i = int(pos_value)
    except Exception:
        return None

    ps = players(g)
    for p in ps:
        try:
            if int(p.get("position")) == pos_i:
                name = player_name(p)
                return name or None
        except Exception:
            pass

    if 0 <= pos_i < len(ps):
        return player_name(ps[pos_i]) or None
    if 1 <= pos_i <= len(ps):
        return player_name(ps[pos_i - 1]) or None
    return None


def turn_username(g: dict) -> str | None:
    cur = current_turn_value(g)
    if cur is None:
        return None
    mapped = player_from_position(g, cur)
    return mapped or str(cur)


def is_probe_bot_turn(g: dict, bot: dict[str, str]) -> bool:
    name = (turn_username(g) or "").strip().lower()
    return name in {bot["label"].lower(), bot["wf_username"].lower()}


def fetch_game_detail(client: WordfeudClientLite, gid: Any) -> dict[str, Any]:
    for meth in ("game_details", "get_game", "game"):
        fn = getattr(client, meth, None)
        if not callable(fn):
            continue
        try:
            raw = fn(int(gid))
        except TypeError:
            try:
                raw = fn(game_id=int(gid))
            except TypeError:
                continue
        except Exception:
            continue
        if isinstance(raw, dict):
            return raw
    return {}


def fetch_active_game_details(client: WordfeudClientLite, bot: dict[str, str]) -> list[dict[str, Any]]:
    raw = cli_games_raw(client)
    games = []
    if isinstance(raw, dict):
        games = raw.get("games") or raw.get("content") or []
    elif isinstance(raw, list):
        games = raw

    active = [g for g in games if isinstance(g, dict) and game_status(g) == "ACTIVE"]
    details: list[dict[str, Any]] = []

    for g in active:
        gid = game_id(g)
        detail = fetch_game_detail(client, gid)
        state = unwrap_state(detail) if detail else g
        # Keep games where WillOrange participates. Prefer active games only; do not require bot turn.
        names = {player_name(p).lower() for p in players(state)}
        if bot["wf_username"].lower() in names or bot["label"].lower() in names:
            if detail:
                details.append(detail)
            else:
                details.append(g)
    return details


def cli_games_raw(client: WordfeudClientLite) -> Any:
    fn = getattr(client, "games_raw", None)
    if not callable(fn):
        raise RuntimeError("WordfeudClientLite has no games_raw() method")
    return fn() or {}


def normalize_rack_text(value: Any) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(first_value(item, ("tile", "letter", "char", "value"), "")))
            else:
                parts.append(str(item))
        value = "".join(parts)
    text = str(value or "").strip().upper()
    # Do not split Spanish 1/2/3 here; matching is by exact observed token characters.
    text = text.replace("_", "?").replace("-", "?")
    return "".join(ch for ch in text if not ch.isspace())


def rack_key(value: Any) -> tuple[tuple[str, int], ...]:
    text = normalize_rack_text(value)
    return tuple(sorted(Counter(text).items()))


def rack_counter(value: Any) -> Counter[str]:
    return Counter(normalize_rack_text(value))

def rack_matches(a: Any, b: Any) -> bool:
    """
    One-off Random board matching:
    - Prefer exact match.
    - If ReIm sees ?, drop ? before comparing.
    """
    aa = normalize_rack_text(a)
    bb = normalize_rack_text(b)

    if rack_key(aa) == rack_key(bb):
        return True

    return rack_key(aa.replace("?", "")) == rack_key(bb.replace("?", ""))


def rack_key_no_blanks(value: Any) -> tuple[tuple[str, int], ...]:
    text = normalize_rack_text(value)
    return tuple(sorted(Counter(ch for ch in text if ch != "?").items()))


def rack_len(value: Any) -> int:
    return len(normalize_rack_text(value))

def player_rack_from_state(state: dict[str, Any], bot: dict[str, str]) -> str:
    aliases = {bot["label"].lower(), bot["wf_username"].lower()}

    for p in players(state):
        if player_name(p).lower() not in aliases:
            continue
        for k in ("rack", "tiles", "rack_tiles", "letters", "hand"):
            if k in p and p.get(k) not in (None, ""):
                return normalize_rack_text(p.get(k))

    # Some payloads expose the current player's rack at top level.
    for obj in (nested_game(state), state):
        if not isinstance(obj, dict):
            continue
        for k in ("rack", "tiles_left", "rack_tiles", "letters", "hand"):
            if k in obj and obj.get(k) not in (None, ""):
                return normalize_rack_text(obj.get(k))

    return ""


def detail_identity(detail: dict[str, Any]) -> dict[str, Any]:
    state = unwrap_state(detail)
    return {
        "game_id": game_id(state),
        "board": board_number(state),
        "ruleset": game_ruleset(state),
        "turn": turn_username(state),
        "players": [player_name(p) for p in players(state)],
    }

def match_detail_by_rack(details: list[dict[str, Any]], screenshot_rack: str, bot: dict[str, str]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []

    for detail in details:
        state = unwrap_state(detail)
        game_rack = player_rack_from_state(state, bot)
        if rack_matches(screenshot_rack, game_rack):
            candidates.append(detail)

    if not candidates:
        print(f"  No active {PROBE_BOT} game has rack matching {screenshot_rack!r}.")
        print("  Active game racks:")
        for detail in details:
            state = unwrap_state(detail)
            ident = detail_identity(state)
            game_rack = player_rack_from_state(state, bot)
            print(
                f"    game={ident['game_id']} "
                f"board={ident['board']} "
                f"rack={game_rack!r} "
                f"rack_key={rack_key(game_rack)} "
                f"turn={ident['turn']}"
            )
        raise RuntimeError("Could not match screenshot to active game by rack")

    if len(candidates) > 1:
        print(f"  Multiple active games matched rack {screenshot_rack!r}; using first:")
        for detail in candidates:
            state = unwrap_state(detail)
            ident = detail_identity(state)
            game_rack = player_rack_from_state(state, bot)
            print(
                f"    game={ident['game_id']} "
                f"board={ident['board']} "
                f"rack={game_rack!r} "
                f"turn={ident['turn']}"
            )

    return candidates[0]

def read_image_bonus_and_rack(image_path: Path) -> tuple[str, set[tuple[str, int]]]:
    board = ReIm.load_board(str(image_path), require_tile_conf=False)
    ReIm.ensure_rack(board)
    ReIm.ensure_bonus(board)
    sets_map = ReIm.board_to_bonus_sets(board)

    items: set[tuple[str, int]] = set()
    for key, bonus in (("DL_SET", "DL"), ("TL_SET", "TL"), ("DW_SET", "DW"), ("TW_SET", "TW")):
        for loc in sets_map.get(key, set()):
            items.add((bonus, int(loc)))

    return normalize_rack_text(board.rack_string), items


def db_items_from_bonus(bonus_items: set[tuple[str, int]]) -> list[dict[str, Any]]:
    return [{"bonus": bonus, "location": loc} for bonus, loc in sorted(bonus_items, key=lambda x: (x[0], x[1]))]


def row_set(items: list[dict[str, Any]]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for item in items or []:
        try:
            out.add((str(item.get("bonus")).upper(), int(item.get("location"))))
        except Exception:
            pass
    return out

def ruleset_from_items(items: list[dict[str, Any]], fallback: Any = None) -> Any:
    for item in items or []:
        if item.get("ruleset") not in (None, ""):
            return item.get("ruleset")
    return fallback


def ensure_boards_table() -> None:
    db_post("/v1/boards/ensure_table", {})


def get_saved_board(board_no: int) -> list[dict[str, Any]]:
    data = db_post("/v1/boards/get", {"board": int(board_no)})
    items = data.get("items", []) if isinstance(data, dict) else []
    return [x for x in items if isinstance(x, dict)]

def list_saved_boards() -> list[int]:
    data = db_post("/v1/boards/list", {})
    boards = data.get("boards", []) if isinstance(data, dict) else []
    return sorted({int(x) for x in boards})

def replace_saved_board(board_no: int, ruleset: Any, bonus_items: set[tuple[str, int]]) -> None:
    payload = {
        "board": int(board_no),
        "ruleset": int(ruleset) if ruleset not in (None, "") else None,
        "items": [
            {
                "bonus": bonus,
                "location": int(loc),
                "ruleset": int(ruleset) if ruleset not in (None, "") else None,
            }
            for bonus, loc in sorted(bonus_items, key=lambda x: (x[0], x[1]))
        ],
    }
    db_post("/v1/boards/replace", payload)


def format_bonus_set(items: set[tuple[str, int]]) -> str:
    by_bonus: dict[str, list[int]] = {"DL": [], "TL": [], "DW": [], "TW": []}
    for bonus, loc in sorted(items, key=lambda x: (x[0], x[1])):
        by_bonus.setdefault(bonus, []).append(loc)
    return "; ".join(f"{b}={vals}" for b, vals in by_bonus.items() if vals)

def print_board_inventory_summary(seen_boards: set[int] | None = None) -> None:
    try:
        stored_boards = set(list_saved_boards())
    except Exception as e:
        print("\nBoard inventory report:")
        print(f"  ERROR: Could not fetch board list: {type(e).__name__}: {e}")
        stored_boards = set()

    stored_boards.update(seen_boards or set())

    print("\nBoard inventory report:")
    print(f"  Unique boards on table, including board 0: {len(stored_boards)}")

    non_zero_boards = sorted(n for n in stored_boards if n != 0)
    if not non_zero_boards:
        print("  Missing boards between lowest non-zero and highest: 0")
        return

    low = non_zero_boards[0]
    high = non_zero_boards[-1]
    missing = [n for n in range(low, high + 1) if n not in stored_boards]

    print(f"  Non-zero board span: {low}-{high}")
    print(f"  Missing boards between lowest non-zero and highest: {len(missing)}")

    if missing:
        print("  Missing board list:")
        print("  " + ", ".join(str(x) for x in missing))

def compare_and_save(board_no: int, ruleset: Any, bonus_items: set[tuple[str, int]]) -> None:
    saved_items = get_saved_board(board_no)
    saved_set = row_set(saved_items)
    old_ruleset = ruleset_from_items(saved_items, fallback="?")

    if not saved_set:
        replace_saved_board(board_no, ruleset, bonus_items)
        print(f"*** Board {board_no}: NEW board saved ruleset={ruleset} squares={len(bonus_items)}")
        return

    if saved_set == bonus_items:
        print(f"*** Board {board_no}: Matches previous seen old_ruleset={old_ruleset} new_ruleset={ruleset}")
    else:
        print(f"*** Board {board_no}: Does NOT match previously seen old_ruleset={old_ruleset} new_ruleset={ruleset}")
        print(f"    saved: {format_bonus_set(saved_set)}")
        print(f"      new: {format_bonus_set(bonus_items)}")
        # Keep the latest observation in the table so repeated reads can show whether the mismatch persists.
        replace_saved_board(board_no, ruleset, bonus_items)


def unique_processed_path(src: Path) -> Path:
    dst = PROCESSED_DIR / src.name
    if not dst.exists():
        return dst
    stem = src.stem
    suffix = src.suffix
    i = 1
    while True:
        candidate = PROCESSED_DIR / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def list_random_images() -> list[Path]:
    if not RANDOM_DIR.exists():
        raise RuntimeError(f"Random board folder does not exist: {RANDOM_DIR}")
    return sorted(
        p for p in RANDOM_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def process_image(image_path: Path, details: list[dict[str, Any]], bot: dict[str, str]) -> bool:
    print(f"\n=== {image_path.name} ===")
    rack, bonus_items = read_image_bonus_and_rack(image_path)
    print(f"  ReIm rack={rack!r} bonus_squares={len(bonus_items)}")

    if not rack:
        raise RuntimeError("ReIm returned empty rack; cannot match screenshot to active game")

    matched = match_detail_by_rack(details, rack, bot)
    state = unwrap_state(matched)
    board_no = board_number(state)
    ruleset = game_ruleset(state)
    gid = game_id(state)

    if board_no in (None, ""):
        raise RuntimeError(f"Matched game {gid} but detail payload has no board number")

    print(f"  Matched game={gid} board={board_no} ruleset={ruleset} rack={rack!r}")
    board_no_i = int(board_no)
    compare_and_save(board_no_i, ruleset, bonus_items)
    return board_no_i


def main() -> int:
    bot = get_probe_bot()
    print(f"Using probe bot: {bot['label']} ({bot['wf_username']})")
    print(f"Random folder: {RANDOM_DIR}")
    print(f"Processed folder: {PROCESSED_DIR}")
    print(f"DB server: {db_server_url()}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    ensure_boards_table()

    images = list_random_images()
    if not images:
        print("No images to process.")
        print_board_inventory_summary(set())
        return 0

    client = make_wf_client(bot)
    details = fetch_active_game_details(client, bot)
    print(f"Active games for {bot['wf_username']}: {len(details)}")

    ok = 0
    failed = 0
    seen_boards: set[int] = set()

    for image_path in images:
        try:
            board_no = process_image(image_path, details, bot)
            seen_boards.add(board_no)
            dst = unique_processed_path(image_path)
            shutil.move(str(image_path), str(dst))
            print(f"  Moved to {dst}")
            ok += 1
        except Exception as e:
            failed += 1
            print(f"  ERROR: {type(e).__name__}: {e}")
            # Keep failed images in Random so they can be retried after fixing the cause.

    print(f"\nComplete. Processed={ok} Failed={failed}")
    print_board_inventory_summary(seen_boards)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
