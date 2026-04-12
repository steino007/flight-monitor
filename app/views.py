import json
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from app.db import get_db
from app.auth import login_required, check_password
from app.airlabs import fetch_schedules

bp = Blueprint("main", __name__)

STATUS_DISPLAY = {
    "scheduled": "Scheduled",
    "active": "Departed",
    "landed": "Arrived",
    "cancelled": "Cancelled",
    "unknown": "Unknown",
    "probably_landed": "Wsrl. geland",
}

STATUS_ICON = {
    "scheduled": "🕐",
    "active": "🛫",
    "landed": "✅",
    "cancelled": "❌",
    "unknown": "❓",
    "probably_landed": "🟡",
}

NL_TZ = ZoneInfo("Europe/Amsterdam")


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
    today_str = date.today().isoformat()
    date_filter = request.args.get("date", today_str)

    routes = db.execute("SELECT * FROM routes ORDER BY origin, destination").fetchall()

    last_check = db.execute(
        "SELECT checked_at, routes_checked, flights_found, source FROM check_log ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Route summary cards
    route_cards = []
    for r in routes:
        route_name = f"{r['origin']} → {r['destination']}"
        today_count = db.execute(
            "SELECT COUNT(*) as cnt FROM flights WHERE route_id = ? AND date = ?",
            (r["id"], date_filter),
        ).fetchone()["cnt"]

        # 7-day sparkline
        spark_rows = db.execute("""
            SELECT date, COUNT(*) as cnt FROM flights
            WHERE route_id = ? AND date >= date(?, '-6 days') AND date <= ?
            GROUP BY date ORDER BY date
        """, (r["id"], today_str, today_str)).fetchall()
        sparkline = [row["cnt"] for row in spark_rows]

        trend = _calc_trend(db, r["id"], today_str)

        route_cards.append({
            "id": r["id"],
            "name": route_name,
            "origin": r["origin"],
            "destination": r["destination"],
            "airline": r["airline"] or "",
            "today_count": today_count,
            "sparkline": sparkline,
            "trend": trend,
        })

    # Flights grouped by route
    flights = db.execute("""
        SELECT f.*, r.origin, r.destination, r.airline
        FROM flights f
        JOIN routes r ON f.route_id = r.id
        WHERE f.date = ?
        ORDER BY r.origin ASC, r.destination ASC, f.dep_time ASC
    """, (date_filter,)).fetchall()

    flight_groups = {}
    for f in flights:
        route_name = f"{f['origin']} → {f['destination']}"
        if route_name not in flight_groups:
            flight_groups[route_name] = []

        status_raw = f["status"]
        flight_groups[route_name].append({
            "flight_iata": f["flight_iata"],
            "dep_time": _format_time(f["dep_time"]),
            "arr_time": _format_time(f["arr_time"]),
            "status": STATUS_DISPLAY.get(status_raw, status_raw),
            "status_icon": STATUS_ICON.get(status_raw, ""),
            "status_raw": status_raw,
        })

    return render_template(
        "dashboard.html",
        route_cards=route_cards,
        flight_groups=flight_groups,
        date_filter=date_filter,
        today=today_str,
        last_check={
            "checked_at": _utc_to_nl(last_check["checked_at"]),
            "routes_checked": last_check["routes_checked"],
            "flights_found": last_check["flights_found"],
            "source": last_check["source"],
        } if last_check else None,
    )


@bp.route("/api/trend")
@login_required
def trend_api():
    db = get_db()
    days = min(int(request.args.get("days", 7)), 90)
    today_str = date.today().isoformat()

    routes = db.execute("SELECT * FROM routes ORDER BY origin, destination").fetchall()
    result = {}

    for r in routes:
        route_name = f"{r['origin']} → {r['destination']}"
        rows = db.execute("""
            SELECT date, COUNT(*) as cnt FROM flights
            WHERE route_id = ? AND date >= date(?, ?) AND date <= ?
            GROUP BY date ORDER BY date
        """, (r["id"], today_str, f"-{days-1} days", today_str)).fetchall()
        result[route_name] = {row["date"]: row["cnt"] for row in rows}

    all_dates = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        all_dates.append(d)

    for route_name in result:
        for d in all_dates:
            if d not in result[route_name]:
                result[route_name][d] = 0

    return jsonify({"dates": all_dates, "routes": result})


@bp.route("/routes", methods=["GET", "POST"])
@login_required
def manage_routes():
    db = get_db()
    if request.method == "POST":
        origin = request.form.get("origin", "").strip().upper()
        destination = request.form.get("destination", "").strip().upper()
        airline = request.form.get("airline", "").strip().upper() or None

        if origin and destination:
            db.execute(
                "INSERT INTO routes (origin, destination, airline) VALUES (?, ?, ?)",
                (origin, destination, airline),
            )
            db.commit()
            return redirect(url_for("main.manage_routes"))

    routes = db.execute("SELECT * FROM routes ORDER BY origin, destination").fetchall()
    return render_template("routes.html", routes=routes)


@bp.route("/route/<int:route_id>/delete", methods=["POST"])
@login_required
def delete_route(route_id):
    db = get_db()
    db.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    db.commit()
    return redirect(url_for("main.manage_routes"))


@bp.route("/check-all", methods=["POST"])
@login_required
def manual_check():
    db = get_db()
    routes = db.execute("SELECT * FROM routes").fetchall()
    today = date.today().isoformat()

    total_flights = 0
    seen_flight_ids = set()

    for route in routes:
        flights = fetch_schedules(route["origin"], route["destination"], route["airline"])
        total_flights += len(flights)
        for f in flights:
            flight_date = today
            dep_time = f.get("dep_time", "")
            if dep_time and " " in dep_time:
                flight_date = dep_time.split(" ")[0]

            flight_iata = f.get("flight_iata", "")
            seen_flight_ids.add((route["id"], flight_date, flight_iata))

            db.execute("""
                INSERT INTO flights (route_id, date, flight_iata, dep_time, arr_time, arr_time_utc, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(route_id, date, flight_iata)
                DO UPDATE SET status=excluded.status, dep_time=excluded.dep_time,
                             arr_time=excluded.arr_time, arr_time_utc=excluded.arr_time_utc,
                             checked_at=CURRENT_TIMESTAMP
            """, (
                route["id"], flight_date,
                flight_iata,
                dep_time,
                f.get("arr_time", ""),
                f.get("arr_time_utc", ""),
                f.get("status", "unknown"),
            ))

    # Mark flights no longer in feed as probably landed
    stale = db.execute("""
        SELECT id, route_id, date, flight_iata FROM flights
        WHERE date = ? AND status IN ('scheduled', 'active')
    """, (today,)).fetchall()

    for row in stale:
        key = (row["route_id"], row["date"], row["flight_iata"])
        if key not in seen_flight_ids:
            db.execute(
                "UPDATE flights SET status = 'probably_landed' WHERE id = ?",
                (row["id"],),
            )

    db.execute(
        "INSERT INTO check_log (routes_checked, flights_found, source) VALUES (?, ?, ?)",
        (len(routes), total_flights, "manual"),
    )
    db.commit()
    return redirect(url_for("main.dashboard"))


# --- Helpers ---

def _calc_trend(db, route_id, today_str):
    """Compare recent 3 days avg vs previous 3 days avg. Returns 'up', 'down', 'stable', or 'new'."""
    rows = db.execute("""
        SELECT date, COUNT(*) as cnt FROM flights
        WHERE route_id = ? AND date >= date(?, '-6 days') AND date <= ?
        GROUP BY date ORDER BY date
    """, (route_id, today_str, today_str)).fetchall()

    if len(rows) < 2:
        return "new"

    counts = [r["cnt"] for r in rows]
    mid = len(counts) // 2
    recent = sum(counts[mid:]) / max(len(counts[mid:]), 1)
    earlier = sum(counts[:mid]) / max(len(counts[:mid]), 1)

    if earlier == 0:
        return "new"
    change = (recent - earlier) / earlier
    if change < -0.2:
        return "down"
    elif change > 0.2:
        return "up"
    return "stable"


def _format_time(time_str):
    if not time_str:
        return "—"
    if " " in time_str:
        return time_str.split(" ")[1][:5]
    return time_str[:5]


def _utc_to_nl(timestamp_str):
    if not timestamp_str:
        return "—"
    try:
        dt = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
        return dt.astimezone(NL_TZ).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        return timestamp_str
