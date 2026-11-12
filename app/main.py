import hmac
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, game
from .game import GROUPS, State

BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def admin_accounts():
    """Organiser accounts from the environment (never stored in the code):
    ADMIN_USERS="name1:password1;name2:password2" and/or ADMIN_USERNAME + ADMIN_PASSWORD."""
    accounts = {}
    for part in os.getenv("ADMIN_USERS", "").split(";"):
        if ":" in part:
            u, p = part.split(":", 1)
            if u.strip() and p:
                accounts[u.strip().lower()] = p
    if ADMIN_PASSWORD:
        accounts.setdefault(ADMIN_USERNAME.strip().lower(), ADMIN_PASSWORD)
    return accounts
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
ON_RENDER = bool(os.getenv("RENDER"))
MAX_LOGO = 2 * 1024 * 1024

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=ON_RENDER, max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals.update(crest=game.crest, nl=game.nl, out_text=game.out_text, GROUPS=GROUPS,
                             RESULT_LABEL=game.RESULT_LABEL, BASE=game.BASE)


@app.on_event("startup")
def startup():
    db.init_db()
    if not admin_accounts():
        print("WARNING: no organiser accounts (set ADMIN_USERS or ADMIN_PASSWORD); the organiser login is disabled.")


def is_admin(request):
    return bool(request.session.get("admin"))


def page(request, template, status_code=200, **ctx):
    ctx.setdefault("state", None)
    ctx.update(is_admin=is_admin(request), admin_user=request.session.get("admin_user", ""), picks_open=game.picks_open(), deadline_label=game.deadline_label(),
               deadline_iso=game.deadline().isoformat(), active_tab=ctx.get("active_tab", ""))
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def pick_round(state, request):
    try:
        r = int(request.query_params.get("round", ""))
    except ValueError:
        r = None
    return r if r in state.rounds else state.last_round


# ---------------- public ----------------
def board_context(s, rnd, hide):
    rows = s.standings(rnd) if rnd else []
    prev = s.standings(rnd - 1) if rnd and (rnd - 1) in s.rounds else []
    return dict(state=s, rnd=rnd, rows=rows, prev_rank={e["id"]: e["rank"] for e in prev},
                prev_score={e["id"]: e["score"] for e in prev},
                ko_now=[t for t in s.teams if s.outs.get(t["id"]) and s.outs[t["id"]]["round"] == rnd],
                ko_before=[t for t in s.teams if s.outs.get(t["id"]) and s.outs[t["id"]]["round"] < rnd],
                hide=hide, still_in=len(s.active_after(rnd)) if rnd else len(s.teams))


@app.get("/admin/simulate", response_class=HTMLResponse)
def simulate(request: Request):
    require_admin(request)
    s = State()
    q = request.query_params
    real_last = s.last_round

    def num(k, default, lo, hi):
        try:
            return max(lo, min(hi, int(q.get(k, default))))
        except ValueError:
            return default
    start = num("start", real_last + 1 if real_last else 1, 1, 14)
    n = num("rounds", 5, 1, 14)
    seed = num("seed", 2026, 0, 10**9)
    sim = None
    if q.get("go"):
        sim = game.simulate(s, start, n, seed, game.settings())
    rnd = pick_round(s, request) if sim else None
    ctx = board_context(s, rnd, False) if sim else dict(state=s, rnd=None, rows=[], hide=False)
    return page(request, "admin/simulate.html", active_tab="simulate", start=start, n=n, seed=seed, sim=sim,
                real_last=real_last, **ctx)


@app.get("/", response_class=HTMLResponse)
def leaderboard(request: Request):
    s = State()
    rnd = pick_round(s, request)
    hide = game.picks_open() and not is_admin(request)
    rows = s.standings(rnd) if rnd else []
    prev = s.standings(rnd - 1) if rnd and (rnd - 1) in s.rounds else []
    prev_rank = {e["id"]: e["rank"] for e in prev}
    prev_score = {e["id"]: e["score"] for e in prev}
    ko_now = [t for t in s.teams if s.outs.get(t["id"]) and s.outs[t["id"]]["round"] == rnd]
    ko_before = [t for t in s.teams if s.outs.get(t["id"]) and s.outs[t["id"]]["round"] < rnd]
    return page(request, "leaderboard.html", state=s, rnd=rnd, rows=rows, prev_rank=prev_rank, prev_score=prev_score,
                ko_now=ko_now, ko_before=ko_before, hide=hide, active_tab="leaderboard",
                still_in=len(s.active_after(rnd)) if rnd else len(s.teams))


@app.get("/teams", response_class=HTMLResponse)
def teams_page(request: Request):
    s = State()
    rnd = pick_round(s, request)
    rows = s.team_table(rnd) if rnd else [{**t, "pts": None, "rank": i + 1} for i, t in enumerate(s.teams)]
    prev = {t["id"]: t for t in s.team_table(rnd - 1)} if rnd and (rnd - 1) in s.rounds else {}
    hide = game.picks_open() and not is_admin(request)
    return page(request, "teams.html", state=s, rnd=rnd, rows=rows, prev=prev, hide=hide, active_tab="teams")


def editing_entry(request):
    eid = request.session.get("entry")
    return db.one("SELECT * FROM rtf_entries WHERE id = ?", (eid,)) if eid else None


@app.get("/pick", response_class=HTMLResponse)
def pick_form(request: Request):
    s = State()
    e = editing_entry(request)
    form = {"name": e["name"], **{f"g{g}": e[f"g{g}"] for g in GROUPS}} if e else {}
    return page(request, "pick.html", state=s, active_tab="pick", error=None, form=form, avail=None, editing=e)


@app.get("/pick/new")
def pick_new(request: Request):
    request.session.pop("entry", None)
    return RedirectResponse("/pick", 303)


@app.get("/pick/change", response_class=HTMLResponse)
def change_form(request: Request):
    return page(request, "change.html", active_tab="pick", error=None, name="")


_pin_fails: dict[str, list[float]] = {}


@app.post("/pick/change", response_class=HTMLResponse)
async def change_login(request: Request):
    form = await request.form()
    name = " ".join((form.get("name") or "").split())
    pin = (form.get("pin") or "").strip()
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _pin_fails.get(ip, []) if now - t < 600]
    if not game.picks_open():
        return page(request, "change.html", active_tab="pick", error=f"Picks closed on {game.deadline_label()}.", name=name)
    if len(recent) >= 8:
        return page(request, "change.html", active_tab="pick", error="Too many wrong tries. Wait 10 minutes.", name=name)
    e = db.one("SELECT * FROM rtf_entries WHERE name_key = ?", (game.norm(name),))
    if not e or not game.check_pin(pin, e.get("pin_hash")):
        _pin_fails[ip] = recent + [now]
        time.sleep(1)
        msg = "Name or PIN is wrong." if not e or e.get("pin_hash") else "This entry has no PIN. Ask the organiser to change it for you."
        return page(request, "change.html", active_tab="pick", error=msg, name=name)
    request.session["entry"] = e["id"]
    return RedirectResponse("/pick", 303)


@app.post("/api/check")
async def api_check(request: Request):
    data = await request.json()
    s = State()
    picks = {}
    for g in GROUPS:
        try:
            tid = int((data.get("picks") or {}).get(g) or 0)
        except (TypeError, ValueError):
            tid = 0
        if tid and s.by_id.get(tid, {}).get("grp") == g:
            picks[g] = tid
    e = editing_entry(request)
    return JSONResponse(game.availability(s, picks, e["combo_key"] if e else None))


_recent_posts: dict[str, list[float]] = {}


@app.post("/pick", response_class=HTMLResponse)
async def pick_submit(request: Request):
    form = await request.form()
    s = State()
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _recent_posts.get(ip, []) if now - t < 60]
    name = " ".join((form.get("name") or "").split())[:40]
    picks = {}
    for g in GROUPS:
        try:
            tid = int(form.get(f"g{g}") or 0)
        except ValueError:
            tid = 0
        if tid and s.by_id.get(tid, {}).get("grp") == g:
            picks[g] = tid
    editing = editing_entry(request)
    if editing and not is_admin(request):
        name = editing["name"]
    pin = (form.get("pin") or "").strip()
    ctx = dict(state=s, active_tab="pick", form={"name": name, **{f"g{g}": picks.get(g) for g in GROUPS}}, avail=None, editing=editing)

    def fail(msg, avail=None):
        return page(request, "pick.html", error=msg, status_code=400, **{**ctx, "avail": avail})

    if len(recent) >= 10:
        return fail("Too many attempts from this connection. Wait a minute and try again.")
    _recent_posts[ip] = recent + [now]
    if not game.picks_open() and not is_admin(request):
        return fail(f"Picks closed on {game.deadline_label()}.")
    if len(name) < 2:
        return fail("Enter your name (at least 2 characters).")
    if len(picks) < 6:
        return fail("Pick one team in every group.")
    if any(s.is_out(t, s.last_round) for t in picks.values()):
        return fail("One of your teams is already out of the cup. Pick another one.")
    clash = db.one("SELECT id FROM rtf_entries WHERE name_key = ?", (game.norm(name),))
    if clash and (not editing or clash["id"] != editing["id"]):
        return fail(f"The name “{name}” is already in the game. Use your full name or add your Hattrick team name.")
    if not editing and not is_admin(request) and not (pin.isdigit() and 4 <= len(pin) <= 8):
        return fail("Choose a PIN of 4 to 8 digits. You need it to change your teams before the deadline.")
    if pin and not (pin.isdigit() and 4 <= len(pin) <= 8):
        return fail("A PIN must be 4 to 8 digits.")
    key = game.combo_key([picks[g] for g in GROUPS])
    avail = game.availability(s, picks, editing["combo_key"] if editing else None)
    if not avail["free"]:
        return fail("Someone already has exactly these six teams. Swap one team for one of the options below.", avail)
    if editing:
        try:
            sets = ", ".join(f"g{g} = ?" for g in GROUPS)
            params = [name, game.norm(name), key, *[picks[g] for g in GROUPS]]
            extra = ""
            if pin:
                extra = ", pin_hash = ?"
                params.append(game.hash_pin(pin))
            db.execute(f"UPDATE rtf_entries SET name = ?, name_key = ?, combo_key = ?, {sets}{extra} WHERE id = ?", (*params, editing["id"]))
        except Exception:
            return fail("Someone submitted exactly these six teams a moment ago. Swap one team.",
                        game.availability(State(), picks, editing["combo_key"]))
        request.session.pop("entry", None)
        entry = db.one("SELECT * FROM rtf_entries WHERE id = ?", (editing["id"],))
        return page(request, "done.html", state=State(), entry=entry, active_tab="pick", changed=True, pin_set=bool(entry.get("pin_hash")))
    try:
        db.execute(f"INSERT INTO rtf_entries (name, name_key, combo_key, {', '.join('g' + g for g in GROUPS)}, pin_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (name, game.norm(name), key, *[picks[g] for g in GROUPS], game.hash_pin(pin) if pin else None))
    except Exception as e:  # UNIQUE constraint: someone submitted the same name or six a moment ago
        msg = str(e).lower()
        if "name_key" in msg:
            return fail(f"The name “{name}” was just taken. Use another name.")
        return fail("Someone submitted exactly these six teams a moment ago. Swap one team for one of the options below.",
                    game.availability(State(), picks))
    entry = db.one("SELECT * FROM rtf_entries WHERE combo_key = ?", (key,))
    return page(request, "done.html", state=State(), entry=entry, active_tab="pick", changed=False, pin_set=bool(pin))


@app.get("/files/{file_id}")
def get_file(file_id: int):
    f = db.one("SELECT filename, content_type, data FROM rtf_files WHERE id = ?", (file_id,))
    if not f:
        raise HTTPException(404)
    return Response(content=bytes(f["data"]), media_type=f["content_type"],
                    headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


@app.get("/healthz")
def health():
    return {"ok": True}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return page(request, "message.html", status_code=404, title="Not found", text="This page doesn't exist.")


# ---------------- organiser ----------------
_fails: dict[str, list[float]] = {}


def require_admin(request):
    if not is_admin(request):
        raise HTTPException(303, headers={"Location": "/admin/login"})


@app.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page(request, "admin/login.html", error=None)


@app.post("/admin/login", response_class=HTMLResponse)
async def login(request: Request):
    form = await request.form()
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]
    if len(recent) >= 5:
        return page(request, "admin/login.html", error="Too many attempts. Try again in 10 minutes.")
    accounts = admin_accounts()
    user = (form.get("username") or "").strip().lower()
    expected = accounts.get(user, "")
    ok = bool(expected) and hmac.compare_digest((form.get("password") or "").encode(), expected.encode())
    if not ok:
        _fails[ip] = recent + [now]
        time.sleep(1)
        return page(request, "admin/login.html", error="No organiser accounts are set on the server." if not accounts else "Wrong username or password.")
    request.session["admin"] = True
    request.session["admin_user"] = user
    return RedirectResponse("/admin", 303)


@app.get("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    require_admin(request)
    s = State()
    return page(request, "admin/home.html", state=s, settings=game.settings(), msg=request.query_params.get("msg"))


@app.post("/admin/settings")
async def admin_settings(request: Request):
    require_admin(request)
    form = await request.form()
    dl = (form.get("deadline") or "").strip()
    try:
        datetime.fromisoformat(dl)
        game.set_setting("deadline", dl[:16])
    except ValueError:
        return RedirectResponse("/admin?msg=That+deadline+is+not+a+valid+date.", 303)
    game.set_setting("match_mode", "1" if form.get("match_mode") else "0")
    game.set_setting("et_goals", "1" if form.get("et_goals") else "0")
    game.set_setting("pen_goals", "1" if form.get("pen_goals") else "0")
    return RedirectResponse("/admin?msg=Settings+saved.", 303)


@app.post("/admin/entries/{eid}/delete")
def delete_entry(request: Request, eid: int):
    require_admin(request)
    db.execute("DELETE FROM rtf_entries WHERE id = ?", (eid,))
    return RedirectResponse("/admin?msg=Player+removed.#players", 303)


@app.get("/admin/entries/{eid}/edit")
def edit_entry(request: Request, eid: int):
    require_admin(request)
    request.session["entry"] = eid
    return RedirectResponse("/pick", 303)


@app.get("/admin/forum", response_class=HTMLResponse)
def forum(request: Request):
    require_admin(request)
    s = State()
    rnd = pick_round(s, request)
    players, teams, kos = game.forum_tables(s, rnd) if rnd else ("", "", "")
    return page(request, "admin/forum.html", state=s, rnd=rnd, players=players, teams=teams, kos=kos, active_tab="forum")


@app.get("/admin/import", response_class=HTMLResponse)
def import_form(request: Request):
    require_admin(request)
    return page(request, "admin/import.html", state=State(), text="", results=None, done=None, active_tab="admin")


@app.post("/admin/import", response_class=HTMLResponse)
async def import_preview(request: Request):
    require_admin(request)
    form = await request.form()
    s = State()
    text = form.get("text") or ""
    results = game.parse_entries_text(text, s)
    if form.get("action") != "import":
        return page(request, "admin/import.html", state=s, text=text, results=results, done=None, active_tab="admin")
    added, skipped = [], []
    for i, r in enumerate(results):
        if not form.get(f"take_{i}"):
            continue
        if not r["ok"] or r.get("clash") or r["already_in"]:
            skipped.append(r["name"])
            continue
        try:
            db.execute(f"INSERT INTO rtf_entries (name, name_key, combo_key, {', '.join('g' + g for g in GROUPS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (r["name"][:40], game.norm(r["name"]), r["key"], *[r["picks"][g]["id"] for g in GROUPS]))
            added.append(r["name"])
        except Exception:
            skipped.append(r["name"])
    return page(request, "admin/import.html", state=State(), text="", results=None, done={"added": added, "skipped": skipped},
                active_tab="admin")


@app.post("/admin/teams/{tid}")
async def update_team(request: Request, tid: int):
    require_admin(request)
    form = await request.form()
    name = " ".join((form.get("name") or "").split())
    if name:
        db.execute("UPDATE rtf_teams SET name = ? WHERE id = ?", (name, tid))
    up = form.get("logo")
    if up is not None and getattr(up, "filename", ""):
        data = await up.read()
        ctype = up.content_type or ""
        if data and len(data) <= MAX_LOGO and ctype in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            fid = db.execute("INSERT INTO rtf_files (filename, content_type, data) VALUES (?, ?, ?)", (up.filename, ctype, data))
            db.execute("UPDATE rtf_teams SET logo_id = ? WHERE id = ?", (fid, tid))
        else:
            return RedirectResponse("/admin?msg=Logo+must+be+a+PNG,+JPG,+WebP+or+GIF+under+2+MB.#teams", 303)
    if form.get("remove_logo"):
        db.execute("UPDATE rtf_teams SET logo_id = NULL WHERE id = ?", (tid,))
    db.execute("DELETE FROM rtf_files WHERE id NOT IN (SELECT logo_id FROM rtf_teams WHERE logo_id IS NOT NULL)")
    return RedirectResponse("/admin?msg=Team+saved.#teams", 303)


@app.post("/admin/rounds/{rnd}/delete")
def delete_round(request: Request, rnd: int):
    require_admin(request)
    s = State()
    if rnd != s.last_round:
        return RedirectResponse("/admin?msg=Only+the+latest+round+can+be+removed.", 303)
    db.execute_many([("DELETE FROM rtf_rounds WHERE round = ?", (rnd,)), ("DELETE FROM rtf_outs WHERE round = ?", (rnd,)),
                     ("DELETE FROM rtf_matches WHERE round = ?", (rnd,))])
    return RedirectResponse(f"/admin?msg=Round+{rnd}+removed.", 303)


# ---- results ----
@app.get("/admin/results", response_class=HTMLResponse)
def results_form(request: Request):
    require_admin(request)
    s = State()
    st = game.settings()
    mode = request.query_params.get("mode", "paste")
    if mode == "matches" and st.get("match_mode") != "1":
        mode = "paste"
    return page(request, "admin/results.html", state=s, settings=st, mode=mode, parsed=None, text="",
                errors={}, rows=None, next_round=s.last_round + 1 if s.last_round else 1)


@app.post("/admin/results/read", response_class=HTMLResponse)
async def results_read(request: Request):
    require_admin(request)
    form = await request.form()
    s = State()
    text = form.get("text") or ""
    parsed = game.parse_standings(text, s)
    problems = []
    if not game.BASE.get(parsed["round"]):
        problems.append("The points table only goes up to round 14.")
    if s.round_list and parsed["prev"] is None and parsed["round"] != s.round_list[0]:
        problems.append(f"There is no round {parsed['round'] - 1} yet. Enter the rounds in order.")
    if parsed["missing"]:
        problems.append("Missing teams that are still in the cup: " + ", ".join(t["name"] for t in parsed["missing"]) + ".")
    parsed["problems"] = problems
    return page(request, "admin/results.html", state=s, settings=game.settings(), mode="paste", parsed=parsed, text=text,
                errors={}, rows=None, next_round=s.last_round + 1 if s.last_round else 1)


@app.post("/admin/results/publish")
async def results_publish(request: Request):
    require_admin(request)
    form = await request.form()
    s = State()
    parsed = game.parse_standings(form.get("text") or "", s)
    if parsed["missing"] or not game.BASE.get(parsed["round"]):
        return RedirectResponse("/admin/results?msg=Read+the+results+again.", 303)
    outs = {}
    for t in parsed["active"]:
        if form.get(f"ko_{t['id']}"):
            outs[t["id"]] = {"how": form.get(f"kohow_{t['id']}") or "regular", "opp": (form.get(f"koopp_{t['id']}") or "").strip()[:60]}
    game.save_round(s, parsed["round"], parsed["found"], outs)
    return RedirectResponse(f"/?round={parsed['round']}", 303)


@app.post("/admin/results/paste-matches", response_class=HTMLResponse)
async def results_paste_matches(request: Request):
    require_admin(request)
    form = await request.form()
    s = State()
    st = game.settings()
    rnd = s.last_round + 1 if s.last_round else 1
    prev = rnd - 1 if (rnd - 1) in s.rounds else None
    active = s.active_after(prev) if prev else s.teams
    text = form.get("text") or ""
    rows, unread, notes = game.parse_match_lines(text, s, {t["id"] for t in active})
    missing = [t["name"] for t in active if t["id"] not in rows]
    return page(request, "admin/results.html", state=s, settings=st, mode="matches", parsed=None, text="",
                errors={}, rows=rows, next_round=rnd, paste_text=text, paste_report={"read": len(rows), "unread": unread,
                "notes": notes, "missing": missing})


def _int(v):
    try:
        return max(0, int(v)) if v not in (None, "") else None
    except ValueError:
        return None


@app.post("/admin/results/matches", response_class=HTMLResponse)
async def results_matches(request: Request):
    require_admin(request)
    form = await request.form()
    s = State()
    st = game.settings()
    if st.get("match_mode") != "1":
        return RedirectResponse("/admin/results", 303)
    rnd = s.last_round + 1 if s.last_round else 1
    prev = rnd - 1 if (rnd - 1) in s.rounds else None
    active = s.active_after(prev) if prev else s.teams
    rows, errors, avgs, outs, matches = {}, {}, {}, {}, {}
    for t in active:
        tid = t["id"]
        r = {"opp": (form.get(f"opp_{tid}") or "").strip()[:60], "dec": form.get(f"dec_{tid}") or "regular",
             **{k: _int(form.get(f"{k}_{tid}")) for k in ("gf", "ga", "etf", "eta", "pf", "pa")}}
        rows[tid] = r
        try:
            res = game.compute_match(r, rnd, s.rounds.get(prev, {}).get(tid) if prev else 0,
                                     s.played.get(prev, {}).get(tid) if prev else 0, st)
        except ValueError as e:
            errors[tid] = str(e).capitalize() + "."
            continue
        avgs[tid] = res["avg"]
        matches[tid] = {**r, "points": res["points"]}
        if res["lost"]:
            outs[tid] = {"how": r["dec"], "opp": r["opp"], "score": f"{r['gf']}-{r['ga']}",
                         "pens": f"{r['pf']}-{r['pa']}" if r["dec"] == "penalties" else ""}
    if errors:
        return page(request, "admin/results.html", state=s, settings=st, mode="matches", parsed=None, text="",
                    errors=errors, rows=rows, next_round=rnd)
    game.save_round(s, rnd, avgs, outs, matches)
    return RedirectResponse(f"/?round={rnd}", 303)
