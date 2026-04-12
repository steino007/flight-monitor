# Flight Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A Flask webapp that daily checks AirLabs API for 7 flight routes and displays status/trends on a password-protected dashboard at https://flights.stijndriessen.cloud

**Architecture:** Python Flask app with SQLite, APScheduler for daily API checks, Jinja2 templates. Single-password auth via session cookie. Deployed as Docker container on Coolify.

**Tech Stack:** Python 3.12, Flask, APScheduler, requests, SQLite3, Jinja2, gunicorn

---

## AirLabs API Reference

- **Endpoint:** `https://airlabs.co/api/v9/routes`
- **Params:** `api_key`, `dep_iata`, `arr_iata`, `airline_iata` (optional)
- **Response:** `{"request": {...}, "response": [{flight_iata, flight_number, dep_iata, dep_time, arr_iata, arr_time, airline_iata, days, duration}]}`
- **Free tier fields:** `airline_iata`, `flight_iata`, `flight_number`, `dep_iata`, `dep_time`, `arr_iata`, `arr_time`
- **Free tier limit:** 50 results per request, 1000 requests/month
- **`days` field:** array like `["mon", "wed", "sat"]` — which weekdays the flight operates

---

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/db.py`
- Create: `.env.example`
- Create: `.gitignore`

**Step 1: Create .gitignore**

```
__pycache__/
*.pyc
*.db
.env
venv/
instance/
```

**Step 2: Create requirements.txt**

```
flask==3.1.1
requests==2.32.3
apscheduler==3.11.0
gunicorn==23.0.0
python-dotenv==1.1.0
```

**Step 3: Create .env.example**

```
AIRLABS_API_KEY=your_key_here
FLIGHT_MONITOR_PASSWORD=your_password_here
SECRET_KEY=generate_a_random_string
```

**Step 4: Create app/__init__.py**

```python
import os
from flask import Flask
from app.db import init_db, get_db


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    init_db(app)

    from app.routes import bp
    app.register_blueprint(bp)

    from app.scheduler import start_scheduler
    start_scheduler(app)

    return app
```

**Step 5: Create app/db.py**

```python
import sqlite3
import os
from flask import g


DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    app.teardown_appcontext(close_db)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            airline TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            flights_found INTEGER NOT NULL DEFAULT 0,
            flight_data TEXT NOT NULL DEFAULT '[]',
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_route_date ON snapshots(route_id, date);
    """)
    conn.commit()
    conn.close()
```

**Step 6: Commit**

```bash
git add -A
git commit -m "feat: project setup with Flask app factory and database schema"
```

---

### Task 2: Authentication

**Files:**
- Create: `app/auth.py`
- Create: `app/templates/login.html`

**Step 1: Create app/auth.py**

```python
import os
from functools import wraps
from flask import session, redirect, url_for, request


PASSWORD = os.environ.get("FLIGHT_MONITOR_PASSWORD", "changeme")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("main.login"))
        return f(*args, **kwargs)
    return decorated


def check_password(password):
    return password == PASSWORD
```

**Step 2: Create app/templates/login.html**

Simple login form — single password field, POST to /login. Show error on wrong password. Minimal styling.

```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Flight Monitor — Login</title>
    <style>
        body { font-family: system-ui, sans-serif; max-width: 400px; margin: 100px auto; padding: 0 1rem; }
        form { display: flex; flex-direction: column; gap: 0.5rem; }
        input { padding: 0.5rem; font-size: 1rem; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 0.5rem; font-size: 1rem; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .error { color: #dc2626; }
    </style>
</head>
<body>
    <h1>Flight Monitor</h1>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
    <form method="POST">
        <input type="password" name="password" placeholder="Wachtwoord" autofocus>
        <button type="submit">Inloggen</button>
    </form>
</body>
</html>
```

**Step 3: Commit**

```bash
git add -A
git commit -m "feat: add password authentication with login_required decorator"
```

---

### Task 3: AirLabs API Client

**Files:**
- Create: `app/airlabs.py`

**Step 1: Create app/airlabs.py**

```python
import os
import requests

API_KEY = os.environ.get("AIRLABS_API_KEY", "")
BASE_URL = "https://airlabs.co/api/v9"


def fetch_route(origin, destination, airline=None):
    """Fetch scheduled flights for a route from AirLabs /routes endpoint.

    Returns list of flight dicts, or empty list on error.
    """
    params = {
        "api_key": API_KEY,
        "dep_iata": origin,
        "arr_iata": destination,
    }
    if airline:
        params["airline_iata"] = airline

    try:
        resp = requests.get(f"{BASE_URL}/routes", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", []) or []
    except Exception:
        return []
```

**Step 2: Commit**

```bash
git add app/airlabs.py
git commit -m "feat: add AirLabs API client for route fetching"
```

---

### Task 4: Scheduler (Daily Check)

**Files:**
- Create: `app/scheduler.py`

**Step 1: Create app/scheduler.py**

```python
import json
import sqlite3
import os
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from app.airlabs import fetch_route

DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def check_all_routes():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    routes = conn.execute("SELECT * FROM routes").fetchall()

    today = date.today().isoformat()

    for route in routes:
        flights = fetch_route(route["origin"], route["destination"], route["airline"])
        conn.execute(
            "INSERT INTO snapshots (route_id, date, flights_found, flight_data) VALUES (?, ?, ?, ?)",
            (route["id"], today, len(flights), json.dumps(flights)),
        )

    conn.commit()
    conn.close()


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_routes, "cron", hour=6, minute=0, id="daily_check")
    scheduler.start()
```

**Step 2: Commit**

```bash
git add app/scheduler.py
git commit -m "feat: add APScheduler for daily route checking at 06:00 UTC"
```

---

### Task 5: Flask Routes & Templates

**Files:**
- Create: `app/routes.py`
- Create: `app/templates/base.html`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/route_detail.html`
- Create: `app/templates/add_route.html`

**Step 1: Create app/routes.py**

```python
import json
from flask import Blueprint, render_template, request, redirect, url_for, session
from app.db import get_db
from app.auth import login_required, check_password
from app.airlabs import fetch_route

bp = Blueprint("main", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_password(request.form.get("password", "")):
            session["authenticated"] = True
            return redirect(url_for("main.dashboard"))
        return render_template("login.html", error="Verkeerd wachtwoord")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/")
@login_required
def dashboard():
    db = get_db()
    routes = db.execute("SELECT * FROM routes ORDER BY created_at").fetchall()

    route_data = []
    for route in routes:
        # Get last 7 snapshots for trend
        snapshots = db.execute(
            "SELECT * FROM snapshots WHERE route_id = ? ORDER BY date DESC LIMIT 7",
            (route["id"],),
        ).fetchall()

        latest = snapshots[0] if snapshots else None
        trend = _calc_trend(snapshots) if len(snapshots) >= 2 else "—"

        route_data.append({
            "route": route,
            "latest": latest,
            "trend": trend,
            "flights_today": latest["flights_found"] if latest else "—",
        })

    return render_template("dashboard.html", routes=route_data)


@bp.route("/route/<int:route_id>")
@login_required
def route_detail(route_id):
    db = get_db()
    route = db.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    if not route:
        return redirect(url_for("main.dashboard"))

    snapshots = db.execute(
        "SELECT * FROM snapshots WHERE route_id = ? ORDER BY date DESC LIMIT 30",
        (route_id,),
    ).fetchall()

    # Parse flight_data JSON for display
    parsed_snapshots = []
    for s in snapshots:
        flights = json.loads(s["flight_data"]) if s["flight_data"] else []
        parsed_snapshots.append({"snapshot": s, "flights": flights})

    return render_template("route_detail.html", route=route, snapshots=parsed_snapshots)


@bp.route("/routes/add", methods=["GET", "POST"])
@login_required
def add_route():
    if request.method == "POST":
        origin = request.form.get("origin", "").strip().upper()
        destination = request.form.get("destination", "").strip().upper()
        airline = request.form.get("airline", "").strip().upper() or None

        if origin and destination:
            db = get_db()
            db.execute(
                "INSERT INTO routes (origin, destination, airline) VALUES (?, ?, ?)",
                (origin, destination, airline),
            )
            db.commit()
            return redirect(url_for("main.dashboard"))

    return render_template("add_route.html")


@bp.route("/route/<int:route_id>/delete", methods=["POST"])
@login_required
def delete_route(route_id):
    db = get_db()
    db.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    db.commit()
    return redirect(url_for("main.dashboard"))


@bp.route("/route/<int:route_id>/check", methods=["POST"])
@login_required
def manual_check(route_id):
    """Trigger a manual check for one route."""
    from datetime import date as d
    db = get_db()
    route = db.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    if route:
        flights = fetch_route(route["origin"], route["destination"], route["airline"])
        db.execute(
            "INSERT INTO snapshots (route_id, date, flights_found, flight_data) VALUES (?, ?, ?, ?)",
            (route_id, d.today().isoformat(), len(flights), json.dumps(flights)),
        )
        db.commit()
    return redirect(url_for("main.route_detail", route_id=route_id))


def _calc_trend(snapshots):
    """Compare latest snapshot with average of previous ones."""
    if len(snapshots) < 2:
        return "—"
    current = snapshots[0]["flights_found"]
    previous_avg = sum(s["flights_found"] for s in snapshots[1:]) / len(snapshots[1:])
    if current > previous_avg:
        return "↑"
    elif current < previous_avg:
        return "↓"
    return "→"
```

**Step 2: Create app/templates/base.html**

Base template with nav (Dashboard, Toevoegen, Uitloggen). Minimal clean CSS. `{% block content %}`.

**Step 3: Create app/templates/dashboard.html**

Table with columns: Route, Maatschappij, Vluchten vandaag, Trend, Laatste check. Each row links to route detail. "Route toevoegen" button.

**Step 4: Create app/templates/route_detail.html**

Route header (SGN → PQC, airline). Table of snapshots: Datum, Vluchten gevonden, Vluchtnummers, Tijden. Manual "Nu checken" button. Delete route button.

**Step 5: Create app/templates/add_route.html**

Form with 3 fields: Vertrek (IATA), Aankomst (IATA), Maatschappij (IATA, optioneel). Submit button.

**Step 6: Commit**

```bash
git add -A
git commit -m "feat: add Flask routes, templates for dashboard, detail, and route management"
```

---

### Task 6: Entrypoint & Docker

**Files:**
- Create: `wsgi.py`
- Create: `Dockerfile`

**Step 1: Create wsgi.py**

```python
from dotenv import load_dotenv
load_dotenv()

from app import create_app

application = create_app()

if __name__ == "__main__":
    application.run(debug=True, port=5000)
```

**Step 2: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

VOLUME ["/app/data"]

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "wsgi:application"]
```

Note: `--workers 1` is important — APScheduler runs in-process, multiple workers would duplicate the cron job.

**Step 3: Commit**

```bash
git add wsgi.py Dockerfile
git commit -m "feat: add wsgi entrypoint and Dockerfile for Coolify deployment"
```

---

### Task 7: Deploy to Coolify

**Step 1: Create GitHub repo**

```bash
gh repo create flight-monitor --public --source=. --push
```

**Step 2: Set up in Coolify**

- Add new resource → GitHub repo → flight-monitor
- Set domain: `flights.stijndriessen.cloud`
- Set environment variables:
  - `AIRLABS_API_KEY` = your key
  - `FLIGHT_MONITOR_PASSWORD` = your chosen password
  - `SECRET_KEY` = random string (e.g. `python3 -c "import secrets; print(secrets.token_hex(32))"`)
- Set port: 5000
- Enable auto-deploy on push to main
- Add persistent volume: `/app/data` for SQLite

**Step 3: DNS**

Add CNAME or A record for `flights.stijndriessen.cloud` → `72.61.179.120`

**Step 4: Deploy and verify**

Visit https://flights.stijndriessen.cloud, log in, add the 7 routes, trigger a manual check.

---

### Task 8: Seed Initial Routes

**Step 1: Add routes via the UI after deploy**

| Origin | Destination | Airline |
|--------|-------------|---------|
| SGN | PQC | VJ (VietJet) or VN (Vietnam Airlines) — depends on booking |
| PQC | DAD | VJ or VN |
| DAD | HPH | VJ or VN |
| AMS | IST | TK (Turkish Airlines) |
| IST | SGN | TK |
| HAN | IST | TK |
| IST | AMS | TK |
