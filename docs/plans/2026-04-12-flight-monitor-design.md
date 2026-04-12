# Flight Monitor — Design

## Doel

Dagelijks monitoren of specifieke vliegroutes nog operationeel zijn, om het effect van het kerosinetekort op geboekte vluchten te volgen.

## Routes

- SGN → PQC (Ho Chi Minh - Phu Quoc)
- PQC → DAD (Phu Quoc - Da Nang)
- DAD → HPH (Da Nang - Hai Phong)
- AMS → IST (Amsterdam - Istanbul)
- IST → SGN (Istanbul - Ho Chi Minh)
- HAN → IST (Hanoi - Istanbul)
- IST → AMS (Istanbul - Amsterdam)

## Stack

- **Backend:** Python + Flask
- **Database:** SQLite
- **Data bron:** AirLabs API (free tier, 1000 calls/maand)
- **Hosting:** Coolify op Hostinger VPS (72.61.179.120)
- **Scheduler:** APScheduler (in-process, 1x per dag)

## Pagina's

| Route | Functie |
|---|---|
| `/login` | Simpele wachtwoord-login (env var `FLIGHT_MONITOR_PASSWORD`) |
| `/` | Dashboard: overzicht routes met laatste status + trend |
| `/route/<id>` | Detail: dagelijkse snapshots per route |
| `/routes/add` | Route toevoegen (origin, destination, airline IATA codes) |

## Database schema

### routes

| Kolom | Type | Beschrijving |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| origin | TEXT | IATA code vertrek |
| destination | TEXT | IATA code aankomst |
| airline | TEXT | IATA code maatschappij |
| created_at | TIMESTAMP | Aanmaakdatum |

### snapshots

| Kolom | Type | Beschrijving |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| route_id | INTEGER FK | Verwijst naar routes.id |
| date | DATE | Datum van de check |
| flights_found | INTEGER | Aantal vluchten gevonden |
| flight_data | TEXT (JSON) | Vluchtnummers, tijden, statussen |
| checked_at | TIMESTAMP | Tijdstip van de check |

## Login

- Één vast wachtwoord via `FLIGHT_MONITOR_PASSWORD` env var
- Flask session cookie na succesvolle login
- Geen gebruikersbeheer

## Dagelijkse check

- APScheduler draait 1x per dag (bijv. 06:00 UTC)
- Per route: AirLabs `/schedules` endpoint
- Slaat snapshot op met aantal vluchten + JSON details
- Bouwt historiek op voor trendanalyse

## Dashboard

Per route toont:
- Route (bijv. SGN → PQC)
- Maatschappij
- Aantal vluchten vandaag
- Trend indicator (↑ ↓ →) t.o.v. vorige dagen
- Laatste check tijdstip
