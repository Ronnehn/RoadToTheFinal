# Road to the Final

A cup prediction game. Players pick six teams (one from each group), every player's six is unique,
and weekly cup results build the leaderboard.

- Public: `/` leaderboard, `/teams`, `/pick`
- Organiser: `/admin` (login with `ADMIN_USERNAME` / `ADMIN_PASSWORD`)

Stack: FastAPI + Jinja templates and its own free Turso (libSQL) database, running as a separate Render web
service, next to the portfolio. All tables start with `rtf_`. Logos are stored in the database too, because
Render's disk is wiped on every deploy.

## Rules built in

- **Picks**: one team per group, final once submitted. If a six is already taken, the page refuses it and shows,
  per group, which teams the player can swap in to make it unique. Names must be unique too.
- **Deadline**: Thursday 19 November 2026, 16:55 (Dutch time) by default; change it under Organiser → Settings.
  Until the deadline, players only see who has joined; everyone's teams and the leaderboard appear after it.
- **Points per match** (row = cup round): win / win after extra time / win on penalties / loss on penalties /
  loss after extra time / loss, plus 501 per goal scored and −500 per goal conceded. Round 1–5 start at 40.000,
  round 6–9 at 44.000, then 48.000, 52.000, 58.000, 68.000 and 78.000 for round 14.
- **Team score** = total points ÷ matches played. **Player score** = sum of the six team averages ÷ 6.
- **Knocked out**: a team that loses (regular time, extra time or penalties) is out. It keeps its average but
  earns nothing more. The leaderboard says who went out, how, and how many players lost a team.

## Weekly results

Organiser → **Enter results**:

1. **Paste standings** (always on): paste the list as you get it (`positie / team / punten`), click Read,
   tick the teams that got knocked out, Publish. Your first paste can be the current standings
   (e.g. "Teams na ronde 13"); later rounds build on it.
2. **Enter matches** (off until you turn it on in Settings): one line per team still in the cup. Enter the
   score, opponent and how it was decided; the site calculates points with the table and knocks out losers.
   Two switches decide whether extra-time goals and shootout goals count.

Made a mistake? Organiser → Rounds → **Remove round N** removes the latest round and its knock-outs.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ADMIN_PASSWORD=test123 uvicorn app.main:app --reload --port 8091
```

Without Turso variables it uses a local `roadtothefinal.db` file.

## Deploy on Render

1. Turso: create a database called `roadtothefinal`, copy its URL and create a token (Read & Write, never expires).
2. Render: **+ New → Blueprint**, pick this repo. Fill in `ADMIN_PASSWORD`, `TURSO_DATABASE_URL`,
   `TURSO_AUTH_TOKEN` (`SECRET_KEY` is generated). Deploy.
3. Log in at `/admin`, check the deadline, upload logos, share the `/pick` link.

The free plan sleeps after 15 minutes without visitors; the first visit then takes up to a minute.

## Files

```
app/main.py        routes: pages, pick submit + availability check, organiser
app/game.py        rules: points table, averages, knock-outs, standings, unique picks, crests
app/db.py          SQLite locally / Turso in production, tables, the 24 teams in 6 groups
templates/         pages (admin/ = organiser)
static/style.css   look (colour tokens at the top)
static/matches.js  live points preview when entering matches
render.yaml        Render deploy config
```
