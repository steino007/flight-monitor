# Flight Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A Flask webapp that checks AirLabs API 2x daily for 7 flight routes and shows a flat list of individual flights with their status (Arrived/Departed/Cancelled) at https://flights.stijndriessen.cloud

**Architecture:** Python Flask app with SQLite, APScheduler for 2x daily API checks (using /schedules endpoint for live flight status), Jinja2 templates. Single-password auth via session cookie. Deployed as Docker container on Coolify.

**Tech Stack:** Python 3.12, Flask, APScheduler, requests, SQLite3, Jinja2, gunicorn

---

## AirLabs API Reference

- **Endpoint:** `https://airlabs.co/api/v9/schedules`
- **Params:** `api_key`, `dep_iata`, `arr_iata`, `airline_iata` (optional)
- **Response:** `{"request": {..., "has_more": bool}, "response": [{flight_iata, flight_number, dep_iata, dep_time, arr_iata, arr_time, airline_iata, status, ...}]}`
- **Status values:** `scheduled`, `cancelled`, `active`, `landed`
- **Free tier fields:** `airline_iata`, `flight_iata`, `flight_number`, `dep_iata`, `dep_time`, `arr_iata`, `arr_time`
- **Free tier limit:** 50 results per request, 1000 requests/month
- **Note:** Shows flights up to ~10 hours ahead. Check 2x/day (06:00 + 14:00 UTC) to catch most flights.

## Dashboard Output Format

```
12 apr  |  SGN → PQC  |  VJ123  |  Dep 08:30  |  Arr 09:25  |  ✅ Arrived
12 apr  |  SGN → PQC  |  VJ127  |  Dep 14:15  |  Arr 15:10  |  ❌ Cancelled
12 apr  |  AMS → IST  |  TK1952 |  Dep 10:40  |  Arr 15:20  |  🛫 Departed
```

---

### Task 1: Project Setup

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/db.py`

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
from app.db import init_db


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    init_db(app)

    from app.views import bp
    app.register_blueprint(bp)

    from app.scheduler import start_scheduler
    start_scheduler(app)

    return app
```

**Step 5: Create app/db.py**

Database with two tables:
- `routes`: monitored routes (origin, destination, airline)
- `flights`: individual flights seen per check (date, flight_iata, dep_time, arr_time, status, route_id)

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
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            flight_iata TEXT,
            dep_time TEXT,
            arr_time TEXT,
            status TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(route_id, date, flight_iata)
        );
        CREATE INDEX IF NOT EXISTS idx_flights_route_date ON flights(route_id, date);
        CREATE INDEX IF NOT EXISTS idx_flights_date ON flights(date DESC);
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
from flask import session, redirect, url_for

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

**Step 2: Create app/templates/login.html — minimal login form**

**Step 3: Commit**

```bash
git add -A
git commit -m "feat: add password authentication"
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


def fetch_schedules(origin, destination, airline=None):
    """Fetch live flight schedules from AirLabs /schedules endpoint.
    Returns list of flight dicts with status, or empty list on error.
    """
    params = {
        "api_key": API_KEY,
        "dep_iata": origin,
        "arr_iata": destination,
    }
    if airline:
        params["airline_iata"] = airline

    try:
        resp = requests.get(f"{BASE_URL}/schedules", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", []) or []
    except Exception:
        return []
```

**Step 2: Commit**

```bash
git add app/airlabs.py
git commit -m "feat: add AirLabs API client using /schedules endpoint"
```

---

### Task 4: Scheduler

**Files:**
- Create: `app/scheduler.py`

**Step 1: Create app/scheduler.py**

Checks all routes 2x daily (06:00 and 14:00 UTC). Per route, fetches /schedules and upserts individual flights into the flights table. Uses UNIQUE constraint to update status if flight already seen today.

```python
import sqlite3
import os
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from app.airlabs import fetch_schedules

DB_PATH = os.environ.get("DB_PATH", "data/flight_monitor.db")


def check_all_routes():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    routes = conn.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()

    for route in routes:
        flights = fetch_schedules(route["origin"], route["destination"], route["airline"])
        for f in flights:
            conn.execute("""
                INSERT INTO flights (route_id, date, flight_iata, dep_time, arr_time, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(route_id, date, flight_iata)
                DO UPDATE SET status=excluded.status, dep_time=excluded.dep_time,
                             arr_time=excluded.arr_time, checked_at=CURRENT_TIMESTAMP
            """, (
                route["id"], today,
                f.get("flight_iata", ""),
                f.get("dep_time", ""),
                f.get("arr_time", ""),
                f.get("status", "unknown"),
            ))
    conn.commit()
    conn.close()


def start_scheduler(app):
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_routes, "cron", hour=6, minute=0, id="check_morning")
    scheduler.add_job(check_all_routes, "cron", hour=14, minute=0, id="check_afternoon")
    scheduler.start()
```

**Step 2: Commit**

```bash
git add app/scheduler.py
git commit -m "feat: add scheduler for 2x daily flight checks"
```

---

### Task 5: Views & Templates

**Files:**
- Create: `app/views.py`
- Create: `app/templates/base.html`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/add_route.html`

**Step 1: Create app/views.py**

Routes:
- `GET/POST /login` — auth
- `GET /logout`
- `GET /` — dashboard: flat list of all flights, newest first, filterable by date
- `GET/POST /routes/add` — add a route
- `POST /route/<id>/delete` — delete a route
- `POST /route/<id>/check` — manual check trigger
- `POST /check-all` — manual check all routes

Dashboard query: JOIN flights with routes, ORDER BY date DESC, flight_iata. Show date, origin → destination, flight_iata, dep_time, arr_time, status with emoji.

**Step 2: Create templates**

- `base.html` — minimal layout, nav (Vluchten, Routes beheren, Uitloggen)
- `dashboard.html` — flat flight list as described, date filter, manual "Nu checken" button
- `add_route.html` — form: origin, destination, airline (optional), plus list of current routes with delete button

**Step 3: Commit**

```bash
git add -A
git commit -m "feat: add views and templates for flight dashboard"
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

**Step 3: Commit**

```bash
git add wsgi.py Dockerfile
git commit -m "feat: add wsgi entrypoint and Dockerfile"
```

---

### Task 7: Deploy to Coolify

**Step 1:** Create GitHub repo: `gh repo create flight-monitor --public --source=. --push`

**Step 2:** Coolify setup:
- Add resource → GitHub → flight-monitor
- Domain: `flights.stijndriessen.cloud`
- Port: 5000
- Env vars: `AIRLABS_API_KEY`, `FLIGHT_MONITOR_PASSWORD`, `SECRET_KEY`
- Volume: `/app/data`

**Step 3:** DNS: A record `flights.stijndriessen.cloud` → `72.61.179.120`

**Step 4:** Deploy, login, add 7 routes, trigger manual check, verify output.
