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


def availability(state, picks, own_key=None):
    """picks: dict group -> team_id (some may be missing).
    Returns which teams would complete an already-taken six, whether the full six is free,
    and for a taken six the single swaps that make it free. own_key: the player's current six (when editing)."""
    taken = taken_keys() - ({own_key} if own_key else set())
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


# logos shipped with the site (static/logos); an uploaded logo in the organiser area takes priority
STATIC_LOGOS = {
    "A.I.": "a-i.png",
    "De Bankhangers": "de-bankhangers.png",
    "Dé Biewies": "de-biewies.png",
    "e-hollando": "e-hollando.png",
    "ER Soccerteam": "er-soccerteam.png",
    "FCMF": "fcmf.png",
    "FC Xelcius": "fc-xelcius.png",
    "The Fighting Hyenas": "the-fighting-hyenas.png",
    "Limburgia FC": "limburgia-fc.png",
    "miracle": "miracle.png",
    "Sunbaked Surfers": "sunbaked-surfers.png",
    "Warriors of Hearts United": "warriors-of-hearts-united.png",
    "Paniek Zaaiende Zigeuners": "paniek-zaaiende-zigeuners.png",
    "De Snelle Jelles": "de-snelle-jelles.png",
    "Lazio Roma": "lazio-roma.png",
    "Revival": "revival.png",
    "De Remmies": "de-remmies.png",
    "BPP United": "bpp-united.png",
    "Strike Back": "strike-back.png",
    "LipNeusVleesVet 3.0": "lipneusvleesvet-3-0.png",
    "De Ministers": "de-ministers.png",
    # no own logo: plain shield with a green H
    "Karbo Kings": "default-h.svg",
    "Streetz Statue CF": "default-h.svg",
    "FC Naosemi": "default-h.svg",
    "FC Kick and Rush": "fc-kick-and-rush.png",   # not in the groups yet; shows if a team is renamed to this
}


def crest(team, out=False, size=""):
    """Real logo if uploaded, otherwise a generated shield."""
    cls = f"crest {size} {'crest-out' if out else ''}".strip()
    label = escape(team["name"]) + (" (out of the cup)" if out else "")
    src = f"/files/{team['logo_id']}" if team.get("logo_id") else (
        f"/static/logos/{STATIC_LOGOS[team['name']]}" if team["name"] in STATIC_LOGOS else None)
    if src:
        x = '<span class="x">✕</span>' if out else ""
        return Markup(f'<span class="{cls} logo"><img src="{src}" alt="{label}" loading="lazy">{x}</span>')
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


# ---- PIN codes (players change their six until the deadline) ----
import hashlib as _hl
import hmac as _hmac
import os as _os


def hash_pin(pin):
    salt = _os.urandom(16).hex()
    h = _hl.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()
    return f"{salt}${h}"


def check_pin(pin, stored):
    if not stored or "$" not in stored:
        return False
    salt, h = stored.split("$", 1)
    test = _hl.pbkdf2_hmac("sha256", (pin or "").encode(), bytes.fromhex(salt), 100_000).hex()
    return _hmac.compare_digest(test, h)


# ---- paste match results: "Team A 2-1 Team B", "Team A - Team B 1-1 (ns 4-3)", "A 3-2 B nv" ----
_PEN = [
    re.compile(r"\(?\s*\b(?:n\.?\s*s\.?|ns|pen(?:alties|alty's|s)?|strafschoppen|shootout)(?![a-z])\s*:?\s*(\d+)\s*[-–:]\s*(\d+)\s*\)?", re.I),
    re.compile(r"\(\s*(\d+)\s*[-–:]\s*(\d+)\s*\b(?:n\.?\s*s\.?|ns|pen\w*|strafschoppen)\s*\)", re.I),
]
_ET = re.compile(r"\(?\s*(?:\bn\.?\s*v\.?(?=\s|\)|$)|\bnv\b|\ba\.?e\.?t\.?(?=\s|\)|$)|na verlenging|verlenging|extra time)\s*\)?", re.I)
_SCORE = re.compile(r"(\d+)\s*[-–:]\s*(\d+)")


def _find_team(state, text):
    text = text.strip(" -–:\t|#*")
    if not text:
        return None, ""
    exact = state.team_by_name(text)
    if exact:
        return exact, text
    n = norm(text)
    # longest team name contained in the text (handles extra words around the name)
    hits = [t for t in state.teams if norm(t["name"]) and (norm(t["name"]) in n or (len(n) >= 4 and n in norm(t["name"])))]
    hits.sort(key=lambda t: -len(norm(t["name"])))
    return (hits[0] if hits else None), text


def parse_match_lines(text, state, active_ids):
    """Returns rows per team id (gf, ga, opp, dec, pf, pa) for our teams, plus lines it couldn't read."""
    rows, unread, notes = {}, [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or not _SCORE.search(line):
            continue
        dec, pf, pa = "regular", None, None
        for rx in _PEN:
            m = rx.search(line)
            if m:
                pf, pa = int(m.group(1)), int(m.group(2))
                dec = "penalties"
                line = (line[:m.start()] + " " + line[m.end():]).strip()
                break
        m = _ET.search(line)
        if m:
            if dec == "regular":
                dec = "extra"
            line = (line[:m.start()] + " " + line[m.end():]).strip()
        m = _SCORE.search(line)
        if not m:
            unread.append(raw.strip())
            continue
        a_txt, b_txt = line[:m.start()], line[m.end():]
        ga_, gb_ = int(m.group(1)), int(m.group(2))
        if not b_txt.strip():  # "Team A - Team B 2-1"
            parts = re.split(r"\s+[-–]\s+|\s+vs\.?\s+|\s+tegen\s+", a_txt.strip())
            if len(parts) == 2:
                a_txt, b_txt = parts
        ta, a_name = _find_team(state, a_txt)
        tb, b_name = _find_team(state, b_txt)
        if not ta and not tb:
            unread.append(raw.strip())
            continue
        for me, my_g, their_g, opp_name, mpf, mpa in ((ta, ga_, gb_, tb["name"] if tb else b_name, pf, pa),
                                                     (tb, gb_, ga_, ta["name"] if ta else a_name, pa, pf)):
            if not me:
                continue
            if me["id"] not in active_ids:
                notes.append(f"{me['name']} is already out of the cup; line ignored for this team.")
                continue
            rows[me["id"]] = {"gf": my_g, "ga": their_g, "opp": opp_name.strip()[:60], "dec": dec,
                              "pf": mpf, "pa": mpa, "etf": None, "eta": None}
    return rows, unread, notes


# ---- Hattrick forum tables ----
def forum_tables(state, rnd):
    rows = state.standings(rnd)
    prev = {e["id"]: e["rank"] for e in state.standings(rnd - 1)} if (rnd - 1) in state.rounds else {}
    out = ["[b]Road to the Final: stand na ronde %d[/b]" % rnd, "[table]",
           "[tr][th]#[/th][th]Speler[/th][th]+/-[/th][th]Teams in de beker[/th][th]Punten[/th][/tr]"]
    for e in rows:
        left = sum(1 for t in state.picks(e) if not state.is_out(t, rnd))
        mv = ""
        if e["id"] in prev:
            d = prev[e["id"]] - e["rank"]
            mv = f"+{d}" if d > 0 else (str(d) if d < 0 else "=")
        out.append(f"[tr][td]{e['rank']}[/td][td]{e['name']}[/td][td]{mv}[/td][td]{left}/6[/td][td]{nl(e['score'])}[/td][/tr]")
    out.append("[/table]")
    players = "\n".join(out)

    tt = state.team_table(rnd)
    out = ["[b]Teams na ronde %d[/b]" % rnd, "[table]",
           "[tr][th]positie[/th][th]team[/th][th]groep[/th][th]beker[/th][th]gekozen[/th][th]punten[/th][/tr]"]
    for t in tt:
        o = state.outs.get(t["id"]) if state.is_out(t["id"], rnd) else None
        status = f"uit (ronde {o['round']})" if o else "in"
        out.append(f"[tr][td]{t['rank']}[/td][td]{t['name']}[/td][td]{t['grp']}[/td][td]{status}[/td]"
                   f"[td]{len(state.picked_by(t['id']))}[/td][td]{nl(t['pts'] or 0)}[/td][/tr]")
    out.append("[/table]")
    teams = "\n".join(out)

    HOW_NL = {"regular": "in de reguliere speeltijd", "extra": "na verlenging", "penalties": "na strafschoppen"}
    kos = [t for t in state.teams if state.outs.get(t["id"]) and state.outs[t["id"]]["round"] == rnd]
    ko_lines = []
    for t in kos:
        o = state.outs[t["id"]]
        txt = f"{t['name']} verloor"
        if o.get("score"):
            txt += f" met {o['score']}"
        if o.get("opp"):
            txt += f" van {o['opp']}"
        txt += f" {HOW_NL.get(o.get('how'), '')}"
        if o.get("how") == "penalties" and o.get("pens"):
            txt += f" ({o['pens']})"
        n = len(state.picked_by(t["id"]))
        txt += f" — {n} speler{'s' if n != 1 else ''} {'zijn' if n != 1 else 'is'} een team kwijt."
        ko_lines.append(txt)
    knockouts = ("[b]Uitgeschakeld in ronde %d[/b]\n" % rnd + "\n".join(ko_lines)) if ko_lines else ""
    return players, teams, knockouts


# ---- bulk import of entries from forum posts ----
import difflib as _difflib

_USER_LINE = re.compile(r"^\s*user\s*(?::\s*|\s+)(.+?)\s*$", re.I)
_GROUP_ONLY = re.compile(r"^\s*groep\s*(\d)\s*[:.\-]?\s*$", re.I)
_GROUP_PREFIX = re.compile(r"^\s*(?:groep\s*(\d)|(\d))\s*[:.\-)]?\s+(.+)$", re.I)


def _clean_name(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)   # [name](link) -> name
    return s.strip().strip("*").strip()


def _score(line_n, team):
    tn = norm(team["name"])
    if not line_n or not tn:
        return 0.0
    if line_n == tn:
        return 1.0
    if line_n in tn or tn in line_n:
        return 0.95 if len(line_n) >= 3 else 0.6
    words = [norm(w) for w in re.split(r"[\s\-]+", team["name"]) if len(norm(w)) >= 3 and norm(w) not in ("the", "fc", "de", "united")]
    if any(w.startswith(line_n) or line_n.startswith(w) for w in words) and len(line_n) >= 3:
        return 0.9
    return _difflib.SequenceMatcher(None, line_n, tn).ratio()


def match_team(state, text, group=None):
    """Best team for a written name; prefers the given group. Returns (team, score, best_overall_team)."""
    n = norm(text)
    pool = [t for t in state.teams if not group or t["grp"] == group]
    ranked = sorted(pool, key=lambda t: -_score(n, t))
    overall = max(state.teams, key=lambda t: _score(n, t))
    best = ranked[0] if ranked else None
    return best, (_score(n, best) if best else 0), overall


def parse_entries_text(text, state):
    """Parse forum posts like 'user: Name' followed by six team lines in any common format."""
    blocks, cur = [], None
    for raw in text.splitlines():
        m = _USER_LINE.match(raw)
        if m:
            cur = {"name": _clean_name(m.group(1)), "lines": []}
            blocks.append(cur)
        elif cur is not None and raw.strip():
            cur["lines"].append(raw.strip())
    results, seen = [], {}
    for b in blocks:
        cands, pending_group = [], None
        for line in b["lines"]:
            if re.fullmatch(r"[-=_ ]{3,}", line):
                continue
            line = re.sub(r"^[\-•*·]+\s+", "", line).strip()
            if not line:
                continue
            g = _GROUP_ONLY.match(line)
            if g:
                pending_group = g.group(1)
                continue
            gp = _GROUP_PREFIX.match(line)
            if gp and (gp.group(1) or gp.group(2)) in GROUPS:
                cands.append({"text": gp.group(3).strip(" -:"), "group": gp.group(1) or gp.group(2)})
            else:
                cands.append({"text": line, "group": pending_group})
            pending_group = None
        # lines without a group label: use their position when there are exactly six team-like lines
        unlabeled = [c for c in cands if not c["group"]]
        team_like = [c for c in cands if match_team(state, c["text"])[1] >= 0.55]
        if len(team_like) == 6 and all(not c["group"] for c in team_like):
            for i, c in enumerate(team_like):
                c["group"] = GROUPS[i]
        picks, problems, ignored = {}, [], []
        for c in cands:
            team, sc, overall = match_team(state, c["text"], c["group"])
            if sc < 0.55 or not team:
                gteam, gsc, _ = match_team(state, c["text"])
                if gsc >= 0.75 and gteam:
                    team, sc = gteam, gsc
                else:
                    ignored.append(c["text"])
                    continue
            o_sc = _score(norm(c["text"]), overall)
            if overall["id"] != team["id"] and o_sc > sc + 0.05:
                team, sc = overall, o_sc
            if team["grp"] in picks and picks[team["grp"]]["id"] != team["id"]:
                problems.append(f"two teams from group {team['grp']}: {picks[team['grp']]['name']} and {team['name']}")
                continue
            picks[team["grp"]] = team
            if sc < 0.9 and norm(c["text"]) != norm(team["name"]):
                c["guess"] = True
                problems.append(f"read “{c['text']}” as {team['name']}")
        missing = [g for g in GROUPS if g not in picks]
        if missing:
            problems.append("no team for group " + ", ".join(missing))
        key = combo_key([picks[g]["id"] for g in GROUPS]) if not missing else None
        name_key = norm(b["name"])
        if name_key in seen:
            prev = seen[name_key]
            if prev["key"] == key:
                prev["listed_twice"] = True
                continue
            problems.append("this name appears twice with different teams; the first one is used")
        r = {"name": b["name"], "picks": picks, "key": key, "problems": problems, "ignored": ignored,
             "ok": not missing, "listed_twice": False, "guessed": [p for p in problems if p.startswith("read ")]}
        if name_key not in seen:
            seen[name_key] = r
            results.append(r)
    # same six for several players: first in the list keeps it
    by_key = {}
    existing = {e["combo_key"]: e["name"] for e in state.entries}
    existing_names = {e["name_key"] for e in state.entries}
    for r in results:
        r["already_in"] = norm(r["name"]) in existing_names
        if not r["key"]:
            continue
        if r["key"] in existing and not r["already_in"]:
            r["clash"] = existing[r["key"]]
        elif r["key"] in by_key:
            r["clash"] = by_key[r["key"]]["name"]
        else:
            by_key[r["key"]] = r
    return results



# ---- test run: random rounds in memory, nothing is saved ----
def simulate(state, start, n, seed, s):
    """Play n random rounds from round `start` on a copy of the state (in place on `state`, which callers
    create fresh and never save). Returns {round: [match dicts]} and notes."""
    import random
    rng = random.Random(seed)
    EXT = ["Excelsior '20", "SV Oranjeboys", "Kampong Kickers", "FC Polderjongens", "VV De Dijk", "Rapid Rotterdam",
           "FC Grachtengordel", "SC Molenwiek", "Tukkers United", "VV Zandvoort"]
    # rounds after the start are replaced by the simulation
    for r in [r for r in state.rounds if r >= start]:
        state.rounds.pop(r); state.played.pop(r, None)
    state.outs = {k: v for k, v in state.outs.items() if v["round"] < start}
    log, notes = {}, []
    for rnd in range(start, start + n):
        if rnd not in BASE:
            notes.append("The points table stops at round 14, so the test run stops there.")
            break
        prev = rnd - 1 if (rnd - 1) in state.rounds else None
        active = [t for t in state.teams if not (prev and state.is_out(t["id"], prev))]
        if not active:
            notes.append(f"No teams left after round {rnd - 1}.")
            break
        rng.shuffle(active)
        pairs, i = [], 0
        while i < len(active):   # sometimes two of our teams meet each other
            if i + 1 < len(active) and rng.random() < 0.3:
                pairs.append((active[i], active[i + 1])); i += 2
            else:
                pairs.append((active[i], None)); i += 1
        state.rounds[rnd], state.played[rnd], matches = {}, {}, []

        def play(team, gf, ga, dec, pf, pa, opp):
            tid = team["id"]
            pav = state.rounds[prev][tid] if prev else 0
            ppl = state.played[prev][tid] if prev else 0
            res = compute_match({"gf": gf, "ga": ga, "dec": dec, "pf": pf, "pa": pa, "etf": 0, "eta": 0}, rnd, pav, ppl, s)
            state.rounds[rnd][tid] = round(res["avg"], 2)
            state.played[rnd][tid] = res["played"]
            if res["lost"]:
                state.outs[tid] = {"team_id": tid, "round": rnd, "opp": opp, "how": dec, "score": f"{gf}-{ga}",
                                   "pens": f"{pf}-{pa}" if dec == "penalties" else ""}
            matches.append({"team": team, "opp": opp, "gf": gf, "ga": ga, "dec": dec, "pf": pf, "pa": pa,
                            "points": res["points"], "lost": res["lost"]})

        for a, b in pairs:
            if b:   # two of our teams: even match
                ga_, gb_ = rng.choice([0, 1, 1, 2, 2, 3, 4]), rng.choice([0, 1, 1, 2, 2, 3, 4])
            else:   # against an outside team our teams usually win (about 3 out of 4)
                ga_, gb_ = rng.choice([1, 2, 2, 3, 3, 4, 5, 6]), rng.choice([0, 0, 0, 1, 1, 1, 2, 3])
            dec, pa_, pb_ = "regular", None, None
            if ga_ == gb_:
                if rng.random() < 0.5:   # someone scores in extra time
                    dec = "extra"
                    if rng.random() < 0.5: ga_ += 1
                    else: gb_ += 1
                else:
                    dec = "penalties"
                    pa_ = rng.randint(2, 5); pb_ = rng.choice([x for x in range(1, 6) if x != pa_])
            opp_name = b["name"] if b else rng.choice(EXT)
            play(a, ga_, gb_, dec, pa_, pb_, opp_name)
            if b:
                play(b, gb_, ga_, dec, pb_, pa_, a["name"])
        # teams already out keep their frozen average
        for t in state.teams:
            if prev and state.is_out(t["id"], prev):
                state.rounds[rnd][t["id"]] = state.rounds[prev][t["id"]]
                state.played[rnd][t["id"]] = state.played[prev][t["id"]]
        log[rnd] = sorted(matches, key=lambda m: (m["team"]["grp"], m["team"]["name"]))
    state.round_list = sorted(state.rounds)
    return {"log": log, "notes": notes, "start": start}
