#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ImpCan.py

Usage:
    python ImpCan.py <Lang> <.txt file with candidate words>
    python ImpCan.py <Lang> <.txt file with candidate words> -dryrun

Example:
    python ImpCan.py ES candidates.txt -dryrun

Behavior:
- Reads candidate words from a text file.
- De-dupes and uppercases input while preserving first occurrence order.
- Applies Spanish digraph handling for ES: CH -> 1, LL -> 2, RR -> 3
  in IndexKey and C01-C15 storage columns.
- Inserts only words not already present for the language in PostgreSQL words table.
- New rows are inserted with verified=false and removed=false.
- Reports added and skipped counts.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from collections import Counter
from typing import Any

import psycopg2
from psycopg2.extras import execute_batch


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PG_HOST = "localhost"
PG_PORT = 5432
PG_DBNAME = "wfdata"
PG_USER = "postgres"
PG_PASSWORD = "PGBananaSpl1t"

WORDS_BASE_DIR = r"E:\OneDrive\WF\Words"

ES_DIGRAPHS = ("CH", "LL", "RR")
ES_DIGRAPH_TO_CODE = {"CH": "1", "LL": "2", "RR": "3"}

C_FIELDS = [f"C{i:02d}" for i in range(1, 16)]


# ---------------------------------------------------------------------------
# PostgreSQL connection / schema
# ---------------------------------------------------------------------------

def get_pg_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DBNAME,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def ensure_pg_schema(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS words (
                language   VARCHAR(2) NOT NULL,
                word       TEXT NOT NULL,
                indexkey   TEXT,
                length     INTEGER,
                source     TEXT,
                last       TIMESTAMP NULL,
                c01        TEXT,
                c02        TEXT,
                c03        TEXT,
                c04        TEXT,
                c05        TEXT,
                c06        TEXT,
                c07        TEXT,
                c08        TEXT,
                c09        TEXT,
                c10        TEXT,
                c11        TEXT,
                c12        TEXT,
                c13        TEXT,
                c14        TEXT,
                c15        TEXT,
                candidate  BOOLEAN NOT NULL DEFAULT FALSE,
                verified   BOOLEAN NOT NULL,
                removed    BOOLEAN NOT NULL,
                removedate TIMESTAMP NULL
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_words_language_word
            ON words(language, word)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_words_language_indexkey
            ON words(language, indexkey)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_words_language_verified_removed
            ON words(language, verified, removed)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_words_language_length
            ON words(language, length)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_words_language_c_tiles
            ON words(
                language, length,
                c01, c02, c03, c04, c05,
                c06, c07, c08, c09, c10,
                c11, c12, c13, c14, c15
            )
            """
        )


# ---------------------------------------------------------------------------
# Normalization / tokenization
# ---------------------------------------------------------------------------

def norm_lang(value: str) -> str:
    lang = (value or "").strip().upper()
    if len(lang) != 2 or not lang.isalpha():
        raise SystemExit("Language code must be exactly 2 letters, e.g. ES / DA / NB / FR.")
    return lang


def strip_diacritic_for_lang(ch: str, legal_tiles: set[str]) -> str:
    """
    Normalize one input character.

    Rule:
    - If the accented/distinct character itself exists in the language tile-set,
      keep it. Example: ES Ñ.
    - Otherwise strip accents/diacritics to the base character where possible.
      Example: É/Ê/Ë -> E.
    """
    if not ch:
        return ch

    ch = ch.upper()

    if ch in legal_tiles:
        return ch

    if "A" <= ch <= "Z":
        return ch

    decomp = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in decomp if unicodedata.category(c) != "Mn").upper()

    if len(base) == 1:
        return base

    return ch


def normalize_input_word(value: str, legal_tiles: set[str]) -> str:
    raw = value.strip().upper()

    # Remove hyphen variants before normalization/tokenization
    raw = (
        raw
        .replace("-", "")
        .replace("‐", "")
        .replace("-", "")
        .replace("‒", "")
        .replace("–", "")
        .replace("—", "")
    )

    return "".join(strip_diacritic_for_lang(ch, legal_tiles) for ch in raw)

def tokenize_word(
    lang: str,
    word: str,
    legal_tiles: set[str],
) -> tuple[list[str], str] | tuple[None, None]:
    w = normalize_input_word(word, legal_tiles)
    if not w:
        return None, None

    storage_tiles: list[str] = []

    if lang == "ES":
        i = 0
        while i < len(w):
            if i + 1 < len(w):
                two = w[i:i + 2]
                if two in ES_DIGRAPHS:
                    storage_tiles.append(ES_DIGRAPH_TO_CODE[two])
                    i += 2
                    continue

            ch = w[i]
            if not ch.isalpha():
                return None, None
            storage_tiles.append(ch)
            i += 1
    else:
        for ch in w:
            if not ch.isalpha():
                return None, None
            storage_tiles.append(ch)

    for tile in storage_tiles:
        if tile not in legal_tiles:
            return None, None

    return storage_tiles, "".join(storage_tiles)

def tile_storage_value(lang: str, tile: str) -> str:
    return tile


def indexkey_for_tiles(lang: str, tiles: list[str]) -> str:
    return "".join(sorted(tiles))

def ccols_for_tiles(lang: str, tiles: list[str], max_len: int = 15) -> list[str | None]:
    values: list[str | None] = []
    for i in range(max_len):
        if i < len(tiles):
            values.append(tile_storage_value(lang, tiles[i]))
        else:
            values.append(None)
    return values


def dedupe_preserve_order(items: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    out: list[str] = []

    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)

    return out, len(items) - len(out)


# ---------------------------------------------------------------------------
# PostgreSQL lookups / inserts
# ---------------------------------------------------------------------------

INSERT_SQL = """
INSERT INTO words (
    language, word, indexkey, length, source, last,
    c01, c02, c03, c04, c05, c06, c07, c08, c09, c10,
    c11, c12, c13, c14, c15,
    candidate, verified, removed, removedate
)
VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s
)
"""


def fetch_existing_words(conn: psycopg2.extensions.connection, lang: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT word FROM words WHERE language = %s", (lang,))
        return {str(row[0]).strip().upper() for row in cur.fetchall() if row[0] is not None}

def fetch_language_tiles(conn: psycopg2.extensions.connection, lang: str) -> set[str]:
    """
    Returns legal storage tiles for a language.

    For ES, tiles CH/LL/RR are converted to storage values 1/2/3.
    Other tiles are uppercased as stored.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT tile FROM tiles WHERE language = %s", (lang,))
        raw_tiles = {str(row[0]).strip().upper() for row in cur.fetchall() if row[0] is not None}

    if not raw_tiles:
        raise RuntimeError(f"No tiles found in tiles table for language {lang}")

    legal: set[str] = set()
    for tile in raw_tiles:
        if lang == "ES":
            legal.add(ES_DIGRAPH_TO_CODE.get(tile, tile))
        else:
            legal.add(tile)

    return legal

def insert_rows(conn: psycopg2.extensions.connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_batch(cur, INSERT_SQL, rows, page_size=1000)


def build_insert_row(lang: str, word: str, tiles: list[str], source: str | None = None) -> tuple[Any, ...]:
    indexkey = indexkey_for_tiles(lang, tiles)
    cvals = ccols_for_tiles(lang, tiles)

    return (
        lang,              # language
        word,              # word
        indexkey,          # indexkey
        len(tiles),        # length
        source,            # source
        None,              # last
        *cvals,            # c01-c15
        True,              # candidate
        False,             # verified
        False,             # removed
        None,              # removedate
    )


# ---------------------------------------------------------------------------
# File handling / reporting
# ---------------------------------------------------------------------------

def resolve_candidates_path(lang: str, arg: str) -> str:
    raw = arg.strip().strip('"').strip("'")
    if os.path.isabs(raw) or os.path.exists(raw):
        return raw
    return os.path.join(WORDS_BASE_DIR, lang, raw)

def read_candidate_file(path: str) -> list[str]:
    with open(path, "rt", encoding="utf-8", errors="ignore") as f:
        return [line.strip() for line in f if line.strip()]

def print_report(lang: str, path: str, dryrun: bool, total_lines: int, dup_removed: int,
                 added: int, skipped: Counter[str]) -> None:
    print("[SUMMARY]")
    print(f"  Language                         : {lang}")
    print(f"  Candidate file                   : {path}")
    print(f"  Mode                             : {'DRY RUN - no PostgreSQL changes made' if dryrun else 'LIVE - PostgreSQL changed'}")
    print(f"  Total non-empty lines read        : {total_lines}")
    print(f"  Removed input duplicates          : {dup_removed}")
    print(f"  Added to PostgreSQL words table   : {added}")
    print(f"  Skipped total                     : {sum(skipped.values())}")

    if skipped:
        print("  Skipped reasons:")
        for reason, count in sorted(skipped.items()):
            print(f"    {reason}: {count}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = [a.strip() for a in sys.argv[1:]]

    dryrun = False
    filtered_args: list[str] = []
    for arg in args:
        if arg.lower() in {"-dryrun", "--dryrun", "-dry-run", "--dry-run"}:
            dryrun = True
        else:
            filtered_args.append(arg)

    if len(filtered_args) != 2:
        print("Usage: python ImpCan.py <Lang> <.txt file with candidate words> [-dryrun]")
        return 2

    lang = norm_lang(filtered_args[0])
    candidates_path = resolve_candidates_path(lang, filtered_args[1])

    if not os.path.exists(candidates_path):
        print(f"ERROR: candidates file not found: {candidates_path}")
        return 1

    raw_words = read_candidate_file(candidates_path)
    total_lines = len(raw_words)
    words, dup_removed = dedupe_preserve_order(raw_words)

    pg_conn = None
    skipped: Counter[str] = Counter()
    insert_batch: list[tuple[Any, ...]] = []

    try:
        pg_conn = get_pg_connection()
        pg_conn.autocommit = False
        ensure_pg_schema(pg_conn)

        legal_tiles = fetch_language_tiles(pg_conn, lang)
        existing_words = fetch_existing_words(pg_conn, lang)
        planned_words: set[str] = set()

        for raw_word in words:
            tiles, word = tokenize_word(lang, raw_word, legal_tiles)

            if tiles is None or word is None:
                skipped["invalid_tiles_or_characters"] += 1
                continue

            if len(tiles) < 2 or len(tiles) > 15:
                skipped["invalid_length_not_2_to_15_tiles"] += 1
                continue

            if word in existing_words:
                skipped["already_exists_for_language"] += 1
                continue

            if word in planned_words:
                skipped["duplicate_after_tokenization"] += 1
                continue

            insert_batch.append(build_insert_row(lang, word, tiles, source=os.path.basename(candidates_path)))
            planned_words.add(word)

        if dryrun:
            pg_conn.rollback()
        else:
            insert_rows(pg_conn, insert_batch)

            # Add net new candidates to status table
            if insert_batch:
                with pg_conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE status
                           SET candidates = COALESCE(candidates, 0) + %s
                         WHERE language = %s
                        """,
                        (len(insert_batch), lang),
                    )

            pg_conn.commit()

        print_report(
            lang=lang,
            path=candidates_path,
            dryrun=dryrun,
            total_lines=total_lines,
            dup_removed=dup_removed,
            added=len(insert_batch),
            skipped=skipped,
        )
        return 0

    except Exception as exc:
        if pg_conn is not None:
            pg_conn.rollback()
        print(f"ERROR: {exc}")
        return 2

    finally:
        if pg_conn is not None:
            pg_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
