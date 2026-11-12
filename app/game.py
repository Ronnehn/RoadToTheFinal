"""Game rules for Road to the Final: points table, averages, knock-outs, standings, unique picks."""
import hashlib
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

from . import db

TZ = ZoneInfo("Europe/Amsterdam")
GROUPS = ["1", "2", "3", "4", "5", "6"]

# ---- points table (win per round; other results are fixed steps below it) ----
BASE = {1: 40000, 2: 40000, 3: 40000, 4: 40000, 5: 40000, 6: 44000, 7: 44000, 8: 44000, 9: 44000,
        10: 48000, 11: 52000, 12: 58000, 13: 68000, 14: 78000}
STEP = {"win": 0, "winET": -5000, "winPen": -10000, "lossPen": -20000, "lossET": -25000, "loss": -30000}
GOAL_FOR, GOAL_AGAINST = 501, 500
RESULT_LABEL = {"win": "Win", "winET": "Win after extra time", "winPen": "Win on penalties",
                "lossPen": "Loss on penalties", "lossET": "Loss after extra time", "loss": "Loss"}
HOW = {"regular": "in regular time", "extra": "after extra time", "penalties": "on penalties"}


def norm(s):
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def nl(x, decimals=2):
    """Dutch number format: 48.080,77"""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# ---- settings & time ----
def settings():
    return {r["key"]: r["value"] for r in db.query("SELECT key, value FROM rtf_settings")}


def set_setting(key, value):
    db.execute("INSERT INTO rtf_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))


def deadline():
    s = settings().get("deadline") or db.DEFAULT_SETTINGS["deadline"]
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def now():
    return datetime.now(TZ)


def picks_open():
    return now() < deadline()


DAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"]


def deadline_label():
    d = deadline()
    return f"{DAYS[d.weekday()].capitalize()} {d.day} {MONTHS[d.month - 1]} {d:%H.%M} uur"


# ---- state ----
class State:
    """Everything the pages need, loaded in a few queries."""

    def __init__(self):
        self.teams = db.query("SELECT * FROM rtf_teams ORDER BY grp, position, id")
        self.by_id = {t["id"]: t for t in self.teams}
        self.entries = db.query("SELECT * FROM rtf_entries ORDER BY created_at, id")
        self.outs = {o["team_id"]: o for o in db.query("SELECT * FROM rtf_outs")}
        self.rounds = {}
        self.played = {}
        for r in db.query("SELECT round, team_id, avg, played FROM rtf_rounds"):
            self.rounds.setdefault(r["round"], {})[r["team_id"]] = r["avg"]
            self.played.setdefault(r["round"], {})[r["team_id"]] = r["played"]
        self.round_list = sorted(self.rounds)

    @property
    def last_round(self):
        return self.round_list[-1] if self.round_list else 0

    def team_by_name(self, name):
        n = norm(name)
        return next((t for t in self.teams if norm(t["name"]) == n), None)

    def is_out(self, team_id, rnd):
        o = self.outs.get(team_id)
        return bool(o) and o["round"] <= rnd

    def active_after(self, rnd):
        return [t for t in self.teams if not self.is_out(t["id"], rnd)]

    def picks(self, e):
        return [e[f"g{i}"] for i in range(1, 7)]

    def picked_by(self, team_id):
        return [e for e in self.entries if team_id in self.picks(e)]

    def standings(self, rnd):
        pts = self.rounds.get(rnd)
        if not pts:
            return []
        rows = []
        for e in self.entries:
            score = sum(pts.get(t, 0) for t in self.picks(e)) / 6
            rows.append({**e, "score": score})
        rows.sort(key=lambda r: -r["score"])
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows

    def team_table(self, rnd):
        pts = self.rounds.get(rnd, {})
        rows = [{**t, "pts": pts.get(t["id"])} for t in self.teams]
        rows.sort(key=lambda t: -(t["pts"] or 0))
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows


def combo_key(team_ids):
    return "-".join(str(int(t)) for t in team_ids)


def taken_keys():
    return {r["combo_key"] for r in db.query("SELECT combo_key FROM rtf_entries")}


def availability(state, picks):
    """picks: dict group -> team_id (some may be missing).
    Returns which teams would complete an already-taken six, whether the full six is free,
    and for a taken six the single swaps that make it free."""
    taken = taken_keys()
    groups = {g: [t for t in state.teams if t["grp"] == g] for g in GROUPS}
    missing = [g for g in GROUPS if not picks.get(g)]
    blocked = []
    if len(missing) <= 1:
        check_groups = missing if missing else GROUPS
        for g in check_groups:
            for t in groups[g]:
                if picks.get(g) == t["id"]:
                    continue
                trial = {**picks, g: t["id"]}
                if combo_key([trial[x] for x in GROUPS]) in taken:
                    blocked.append(t["id"])
    result = {"blocked": blocked, "complete": not missing, "free": None, "swaps": {}}
    if not missing:
        key = combo_key([picks[g] for g in GROUPS])
        result["free"] = key not in taken
        if not result["free"]:
            for g in GROUPS:
                opts = []
                for t in groups[g]:
                    if t["id"] == picks[g]:
                        continue
                    trial = {**picks, g: t["id"]}
                    if combo_key([trial[x] for x in GROUPS]) not in taken:
                        opts.append({"id": t["id"], "name": t["name"]})
                if opts:
                    result["swaps"][g] = opts
    return result


# ---- results: standings paste ----
def parse_standings(text, state):
    m = re.search(r"ronde\s+(\d+)", text, re.I) or re.search(r"round\s+(\d+)", text, re.I)
    rnd = int(m.group(1)) if m else state.last_round + 1
    found, unknown = {}, []
    for line in text.splitlines():
        mm = re.match(r"^\s*\d+\s*[\t ]+(.+?)[\t ]+(-?[\d.]*\d(?:,\d+)?)\s*$", line)
        if not mm:
            continue
        name = re.sub(r"^#\s*", "", mm.group(1)).strip()
        raw = re.sub(r"\.(?=\d{3}(\D|$))", "", mm.group(2)).replace(",", ".")
        try:
            pts = float(raw)
        except ValueError:
            continue
        t = state.team_by_name(name)
        if t:
            found[t["id"]] = pts
        else:
            unknown.append(name)
    prev = rnd - 1 if (rnd - 1) in state.rounds else None
    active = state.active_after(prev) if prev else state.teams
    missing = [t for t in active if t["id"] not in found]
    return {"round": rnd, "found": found, "unknown": unknown, "active": active, "missing": missing, "prev": prev}


# ---- results: matches ----
def compute_match(row, rnd, prev_avg, prev_played, s):
    """row: dict with gf, ga, dec, etf, eta, pf, pa (ints or None). Returns dict or raises ValueError."""
    gf, ga, dec = row.get("gf"), row.get("ga"), row.get("dec") or "regular"
    if gf is None or ga is None:
        raise ValueError("score missing")
    if rnd not in BASE:
        raise ValueError("the points table only goes up to round 14")
    if dec == "regular":
        if gf == ga:
            raise ValueError("a match decided in regular time can't end level")
        key = "win" if gf > ga else "loss"
    elif dec == "extra":
        if gf == ga:
            raise ValueError("level after extra time means penalties")
        key = "winET" if gf > ga else "lossET"
    elif dec == "penalties":
        if gf != ga:
            raise ValueError("a shootout only happens after a level score")
        pf, pa = row.get("pf"), row.get("pa")
        if pf is None or pa is None or pf == pa:
            raise ValueError("enter a shootout score with a winner")
        key = "winPen" if pf > pa else "lossPen"
    else:
        raise ValueError("unknown result type")
    etf = eta = 0
    if dec != "regular" and s.get("et_goals") != "1":
        etf, eta = row.get("etf") or 0, row.get("eta") or 0
        if etf > gf or eta > ga:
            raise ValueError("more extra-time goals than goals in total")
    pen_count = dec == "penalties" and s.get("pen_goals") == "1"
    goals_for = gf - etf + ((row.get("pf") or 0) if pen_count else 0)
    goals_ag = ga - eta + ((row.get("pa") or 0) if pen_count else 0)
    points = BASE[rnd] + STEP[key] + GOAL_FOR * goals_for - GOAL_AGAINST * goals_ag
    played = (prev_played or 0) + 1
    avg = ((prev_avg or 0) * (prev_played or 0) + points) / played
    return {"key": key, "points": points, "avg": avg, "played": played, "lost": key.startswith("loss")}


def save_round(state, rnd, avgs, outs, matches=None):
    """Write one round: averages for active teams, frozen values for teams already out, new knock-outs."""
    prev = rnd - 1 if (rnd - 1) in state.rounds else None
    stmts = [("DELETE FROM rtf_rounds WHERE round = ?", (rnd,)),
             ("DELETE FROM rtf_outs WHERE round >= ?", (rnd,)),
             ("DELETE FROM rtf_matches WHERE round = ?", (rnd,))]
    for t in state.teams:
        tid = t["id"]
        already_out = prev is not None and state.is_out(tid, prev)
        if already_out:
            avg, played = state.rounds[prev].get(tid, 0), state.played[prev].get(tid, 0)
        elif tid in avgs:
            avg = avgs[tid]
            played = (state.played.get(prev, {}).get(tid, rnd - 1) + 1) if prev else rnd
        else:
            continue
        stmts.append(("INSERT INTO rtf_rounds (round, team_id, avg, played) VALUES (?, ?, ?, ?)",
                      (rnd, tid, round(avg, 2), played)))
    for tid, o in outs.items():
        stmts.append(("INSERT INTO rtf_outs (team_id, round, opp, how, score, pens) VALUES (?, ?, ?, ?, ?, ?)",
                      (tid, rnd, o.get("opp") or "", o.get("how") or "regular", o.get("score") or "", o.get("pens") or "")))
    for tid, m in (matches or {}).items():
        stmts.append(("INSERT INTO rtf_matches (round, team_id, opp, gf, ga, dec, etf, eta, pf, pa, points) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (rnd, tid, m.get("opp"), m.get("gf"), m.get("ga"), m.get("dec"), m.get("etf"), m.get("eta"),
                       m.get("pf"), m.get("pa"), m.get("points"))))
    db.execute_many(stmts)


def out_text(o):
    parts = ["lost"]
    if o.get("score"):
        parts.append(o["score"])
    if o.get("opp"):
        parts.append(f"to {o['opp']}")
    parts.append(HOW.get(o.get("how"), ""))
    txt = " ".join(p for p in parts if p)
    if o.get("how") == "penalties" and o.get("pens"):
        txt += f" ({o['pens']})"
    return txt


# ---- temporary crests ----
PALETTE = [("#0f3d91", "#ffffff"), ("#c8102e", "#ffffff"), ("#1b7f3b", "#f5d000"), ("#111111", "#f26a1b"),
           ("#6b1d78", "#ffffff"), ("#f5d000", "#111111"), ("#0a8fa8", "#ffffff"), ("#7a1f1f", "#9fc2e7"),
           ("#ffffff", "#c8102e"), ("#14213d", "#e2b43a"), ("#e85d04", "#1d3557"), ("#2a9d8f", "#264653")]


def initials(name):
    words = [w for w in re.split(r"[\s\-]+", re.sub(r"[#*]", " ", name)) if w]
    words = [w for w in words if not re.fullmatch(r"(fc|afc|cf|f\.?c\.?|bvo|de|the)", w, re.I)]
    named = [w for w in words if re.search(r"[A-Za-zÀ-ÿ]", w)]
    if not named:
        return "?"
    if len(named) == 1:
        w = re.sub(r"[^A-Za-zÀ-ÿ0-9]", "", named[0])
        if len(w) <= 4 and w == w.upper():
            return w
        caps = re.findall(r"[A-Z]", w)
        if len(caps) >= 2:
            return "".join(caps[:3])
        return w[:2].upper()
    return (named[0][0] + named[1][0]).upper()


def crest(team, out=False, size=""):
    """Real logo if uploaded, otherwise a generated shield."""
    cls = f"crest {size} {'crest-out' if out else ''}".strip()
    label = escape(team["name"]) + (" (out of the cup)" if out else "")
    if team.get("logo_id"):
        x = '<span class="x">✕</span>' if out else ""
        return Markup(f'<span class="{cls} logo"><img src="/files/{team["logo_id"]}" alt="{label}" loading="lazy">{x}</span>')
    h = int(hashlib.md5(team["name"].encode()).hexdigest(), 16)
    c1, c2 = PALETTE[h % len(PALETTE)]
    pat = (h >> 4) % 4
    cid = f"cr{team['id']}{'o' if out else ''}{h % 997}"
    shield = "M20 2 37 7v15c0 12-8 20-17 24C11 42 3 34 3 22V7z"
    patterns = ["", f'<path d="M20 0h20v48H20z" fill="{c2}"/>', f'<path d="M10 0h6v48h-6zM24 0h6v48h-6z" fill="{c2}"/>',
                f'<path d="M-4 30 44 6v10L-4 40z" fill="{c2}"/>']
    dark = c2 if c1 == "#ffffff" else c1
    disc = c1 if c2 == "#ffffff" else "#ffffff"
    ink = c2 if pat == 0 else (dark if disc == "#ffffff" else "#ffffff")
    ini = escape(initials(team["name"]))
    big = len(ini) > 2
    circle = "" if pat == 0 else f'<circle cx="20" cy="23" r="{10.5 if big else 9}" fill="{disc}" stroke="rgba(0,0,0,.25)" stroke-width="1"/>'
    cross = '<path d="M8 10 32 40M32 10 8 40" stroke="#d64545" stroke-width="3.5" stroke-linecap="round"/>' if out else ""
    return Markup(
        f'<svg class="{cls}" viewBox="0 0 40 48" role="img" aria-label="{label} crest"><defs><clipPath id="{cid}"><path d="{shield}"/></clipPath></defs>'
        f'<g clip-path="url(#{cid})"><path d="M0 0h40v48H0z" fill="{c1}"/>{patterns[pat]}</g>'
        f'<path d="{shield}" fill="none" stroke="rgba(0,0,0,.35)" stroke-width="1.5"/>{circle}'
        f'<text x="20" y="27" text-anchor="middle" font-family="Barlow Condensed, Arial Narrow, sans-serif" font-weight="800" '
        f'font-size="{9.5 if big else 11.5}" fill="{ink}">{ini}</text>{cross}</svg>')
