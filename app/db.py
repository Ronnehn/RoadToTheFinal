"""Database layer: a local SQLite file in development, Turso (libSQL) on Render.

All tables start with `rtf_` so nothing collides if the database is ever shared.
"""
import os
import sqlite3
import threading

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
LOCAL_PATH = os.getenv("LOCAL_DB_PATH", "roadtothefinal.db")

_lock = threading.Lock()


def _connect():
    if TURSO_URL:
        import libsql

        return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    return sqlite3.connect(LOCAL_PATH)


def _rows(cur):
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def query(sql, params=()):
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            return _rows(cur)
        finally:
            conn.close()


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def execute_many(statements):
    """Run several (sql, params) statements in one transaction."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            for sql, params in statements:
                cur.execute(sql, tuple(params))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()


GROUPS = {
    "1": ["The Fighting Hyenas", "ER Soccerteam", "FC Xelcius", "De Bankhangers"],
    "2": ["A.I.", "e-hollando", "De Snelle Jelles", "Warriors of Hearts United"],
    "3": ["Dé Biewies", "Paniek Zaaiende Zigeuners", "Revival", "Strike Back"],
    "4": ["De Ministers", "Limburgia FC", "Karbo Kings", "Sunbaked Surfers"],
    "5": ["Streetz Statue CF", "De Remmies", "FCMF", "miracle"],
    "6": ["BPP United", "FC Naosemi", "Lazio Roma", "LipNeusVleesVet 3.0"],
}

DEFAULT_SETTINGS = {
    "deadline": "2026-11-19T16:55",   # Europe/Amsterdam
    "match_mode": "1",
    "et_goals": "1",
    "pen_goals": "0",
}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS rtf_settings (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE IF NOT EXISTS rtf_teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, grp TEXT NOT NULL, position INTEGER DEFAULT 0,
        logo_id INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS rtf_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        name_key TEXT NOT NULL UNIQUE,
        combo_key TEXT NOT NULL UNIQUE,
        g1 INTEGER, g2 INTEGER, g3 INTEGER, g4 INTEGER, g5 INTEGER, g6 INTEGER,
        pin_hash TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS rtf_rounds (
        round INTEGER NOT NULL, team_id INTEGER NOT NULL,
        avg REAL NOT NULL, played INTEGER NOT NULL,
        PRIMARY KEY (round, team_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rtf_outs (
        team_id INTEGER PRIMARY KEY, round INTEGER NOT NULL,
        opp TEXT, how TEXT, score TEXT, pens TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS rtf_matches (
        round INTEGER NOT NULL, team_id INTEGER NOT NULL,
        opp TEXT, gf INTEGER, ga INTEGER, dec TEXT,
        etf INTEGER, eta INTEGER, pf INTEGER, pa INTEGER, points REAL,
        PRIMARY KEY (round, team_id)
    )""",
    """CREATE TABLE IF NOT EXISTS rtf_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT, content_type TEXT, data BLOB
    )""",
]


MIGRATIONS = ["ALTER TABLE rtf_entries ADD COLUMN pin_hash TEXT"]


def init_db():
    for stmt in SCHEMA:
        execute(stmt)
    for stmt in MIGRATIONS:
        try:
            execute(stmt)
        except Exception:
            pass  # column already there
    for k, v in DEFAULT_SETTINGS.items():
        if not one("SELECT key FROM rtf_settings WHERE key = ?", (k,)):
            execute("INSERT INTO rtf_settings (key, value) VALUES (?, ?)", (k, v))
    if not one("SELECT id FROM rtf_teams LIMIT 1"):
        stmts = []
        for g, names in GROUPS.items():
            for i, name in enumerate(names):
                stmts.append(("INSERT INTO rtf_teams (name, grp, position) VALUES (?, ?, ?)", (name, g, i)))
        execute_many(stmts)
