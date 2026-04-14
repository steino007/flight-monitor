# Flight Monitor — Architecture

## Doel

Monitoren of specifieke vliegroutes nog operationeel zijn, om het effect van het kerosinetekort op geboekte vluchten te volgen. Combineert dagelijks schema-informatie (welke vluchten staan gepland) met real-time vluchtstatus (wat vliegt er daadwerkelijk).

## Stack

| Component | Technologie |
|---|---|
| Backend | Python 3.12, Flask |
| Database | SQLite (WAL mode) |
| Scheduler | APScheduler (in-process) |
| Data bron | AirLabs API (free tier, 1000 calls/maand) |
| Charts | Chart.js 4.x via CDN |
| WSGI | Gunicorn (1 worker) |
| Hosting | Coolify op Hostinger VPS (72.61.179.120) |
| URL | https://flights.stijndriessen.cloud |

## Bestandsstructuur

```
flight-monitor/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── auth.py              # Wachtwoord-login (env var)
│   ├── db.py                # SQLite connectie + schema migraties
│   ├── airlabs.py           # AirLabs API client (/schedules + /routes)
│   ├── checks.py            # Gedeelde check-logica (vluchten + schema)
│   ├── scheduler.py         # APScheduler setup (4 jobs)
│   ├── views.py             # Routes, dashboard, API endpoints
│   └── templates/
│       ├── base.html         # Layout, nav, CSS
│       ├── login.html        # Login pagina
│       ├── dashboard.html    # Hoofd-dashboard met charts
│       └── routes.html       # Route beheer
├── docs/plans/              # Design documenten
├── wsgi.py                  # Gunicorn entrypoint
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Database Schema

### routes
Gemonitorde vliegroutes.

| Kolom | Type | Beschrijving |
|---|---|---|
| id | INTEGER PK | |
| origin | TEXT | IATA code vertrek (bijv. SGN) |
| destination | TEXT | IATA code aankomst (bijv. PQC) |
| airline | TEXT | Optioneel: IATA airline filter (bijv. TK) |
| created_at | TIMESTAMP | |

### flights
Individuele vluchten per dag, opgehaald via `/schedules` endpoint.

| Kolom | Type | Beschrijving |
|---|---|---|
| id | INTEGER PK | |
| route_id | INTEGER FK | → routes.id |
| date | DATE | Vluchtdatum (uit dep_time) |
| flight_iata | TEXT | Vluchtnummer (bijv. TK1952) |
| dep_time | TEXT | Vertrektijd (lokale luchthaven-tijd) |
| arr_time | TEXT | Aankomsttijd (lokale luchthaven-tijd) |
| arr_time_utc | TEXT | Aankomsttijd in UTC |
| status | TEXT | scheduled/active/landed/cancelled/probably_landed |
| checked_at | TIMESTAMP | |
| UNIQUE | | (route_id, date, flight_iata) |

### route_snapshots
Dagelijkse snapshot van het geplande vliegschema, opgehaald via `/routes` endpoint.

| Kolom | Type | Beschrijving |
|---|---|---|
| id | INTEGER PK | |
| route_id | INTEGER FK | → routes.id |
| date | DATE | Datum van de snapshot |
| planned_count | INTEGER | Totaal aantal geplande vluchten (alle weekdagen) |
| flight_numbers | TEXT (JSON) | Array van {flight_iata, dep_time, arr_time, days[]} |
| checked_at | TIMESTAMP | |
| UNIQUE | | (route_id, date) |

### check_log
Logging van elke API check.

| Kolom | Type | Beschrijving |
|---|---|---|
| id | INTEGER PK | |
| checked_at | TIMESTAMP | |
| routes_checked | INTEGER | |
| flights_found | INTEGER | |
| source | TEXT | "scheduler" of "manual" |

## Data Flow

### Twee databronnen

1. **`/schedules` endpoint** — real-time vluchtstatus
   - Toont vluchten ~10 uur vooruit
   - Geeft status: scheduled → active → landed / cancelled
   - Wordt 3x per dag opgehaald

2. **`/routes` endpoint** — gepland vliegschema
   - Toont alle geplande vluchten op een route, ongeacht datum
   - Bevat welke weekdagen elke vlucht opereert (`days` veld)
   - Wordt 1x per dag opgehaald (baseline)

### Scheduler Jobs

| Job | UTC | NL | Doel |
|---|---|---|---|
| check_morning | 06:00 | 08:00 | Vluchtstatus ophalen |
| check_afternoon | 14:00 | 16:00 | Vluchtstatus ophalen |
| check_evening | 22:00 | 00:00 | Vluchtstatus ophalen |
| check_schema | 04:00 | 06:00 | Gepland schema ophalen |

### API Calls Budget

- Schedules: 3 checks × ~9 routes = 27 calls/dag ≈ 810/maand
- Routes: 1 check × ~9 routes = 9 calls/dag ≈ 270/maand
- Totaal: ≈ 1080/maand (net op de grens van 1000 free tier)

### Stale Flight Detection

Vluchten die uit de `/schedules` feed verdwijnen met status "scheduled" of "active" worden gemarkeerd als "probably_landed". Dit gebeurt bij elke check, voor zowel vandaag als gisteren.

### Weekdag-filtering

Het schema bevat vluchten voor alle weekdagen. Bij weergave en berekeningen wordt gefilterd op de weekdag van de geselecteerde datum. Een vlucht die alleen op ma/wo/vr vliegt, wordt op dinsdag niet getoond als "niet gezien".

## Dashboard

### Secties

1. **Route Cards** — per route: aantal gezien / gepland, progress bar, trend badge
2. **Stacked Bar Charts** — per route per dag: gevlogen (groen) / gecanceld (geel) / geschrapt (rood)
3. **Vluchttabel** — per route gegroepeerd, met Schema-kolom en Status-kolom

### Schema vs Status

| Schema kolom | Betekenis |
|---|---|
| ✅ Gepland | Vlucht staat nog in het schema |
| ❌ Geschrapt | Vlucht stond eerder in schema maar is verdwenen |
| — | Vlucht niet in schema gevonden |

| Status kolom | Betekenis |
|---|---|
| ✅ Arrived | Geland |
| 🛫 Departed | Vertrokken |
| 🕐 Scheduled | Gepland (nog niet vertrokken) |
| ❌ Cancelled | Geannuleerd |
| 🟡 Wsrl. geland | Verdwenen uit feed, waarschijnlijk geland |
| ⚫ Niet gezien | In schema maar niet in real-time feed gezien |

## Deployment

### Coolify Configuratie

- **Repo:** github.com/steino007/flight-monitor
- **Branch:** main
- **Build Pack:** Dockerfile
- **Port:** 5000
- **Domain:** https://flights.stijndriessen.cloud
- **Volume:** `flight-monitor-data` → `/app/data`

### Environment Variables

| Variable | Doel |
|---|---|
| AIRLABS_API_KEY | AirLabs API key |
| FLIGHT_MONITOR_PASSWORD | Login wachtwoord |
| SECRET_KEY | Flask session signing |

### DNS

A-record: `flights.stijndriessen.cloud` → `72.61.179.120`
