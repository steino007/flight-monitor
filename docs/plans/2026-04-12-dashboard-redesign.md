# Flight Monitor Dashboard Redesign

## Doel

Vervang het huidige tabel-only dashboard door een strak, informatief single-page dashboard dat twee vragen beantwoordt:
1. **Zijn mijn routes nog gezond?** — trendoverzicht per route
2. **Wat is de status vandaag?** — vluchttabel met statusinfo

## Design

### Layout (single scrollable page)

```
┌─────────────────────────────────────────────────────┐
│  Flight Monitor          Vandaag ▼    [Nu checken]  │
│  Laatste check: 12 apr 16:00 (scheduler)            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │SGN → PQC│ │AMS → IST│ │IST → AMS│ │IST → SGN│  │
│  │ 8 today  │ │ 3 today │ │ 3 today │ │ 1 today │  │
│  │ ━━━━━━━  │ │ ━━━━━━━ │ │ ━━━━━━━ │ │ ━━━━━━━ │  │
│  │ (spark)  │ │ (spark) │ │ (spark) │ │ (spark) │  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘  │
│                                                     │
├─────────────────────────────────────────────────────┤
│  Trend          [7d] [14d] [30d]                    │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │  Line chart: flights per route per day       │    │
│  │  X: dates, Y: count, one line per route      │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
├─────────────────────────────────────────────────────┤
│  Vluchten vandaag                    [datum filter]  │
│                                                     │
│  Route    │ Vlucht │ Vertrek │ Aankomst │ Status    │
│  SGN→PQC  │ VJ321  │ 08:50   │ 09:55    │ ✅ Arrived│
│  ...      │ ...    │ ...     │ ...      │ ...       │
└─────────────────────────────────────────────────────┘
```

### Sectie 1: Route Summary Cards

- Grid van kaarten, 1 per gemonitorde route
- Elke kaart toont: route (origin → dest), aantal vluchten vandaag, sparkline (7 dagen)
- Kleurcodering: groen = normaal, geel = daling, rood = significante daling of 0
- Klikbaar → scrollt naar vluchttabel gefilterd op die route

### Sectie 2: Trendgrafiek

- Chart.js lijndiagram via CDN
- Eén lijn per route, X-as = datum, Y-as = aantal vluchten
- Toggle: 7d / 14d / 30d
- Hover toont exacte waarden
- Routes als legenda met klikbare items (toggle zichtbaarheid)

### Sectie 3: Vluchttabel

- Dezelfde data als nu, maar beter opgemaakt
- Gegroepeerd per route (visuele scheiding)
- Datumfilter
- Status met kleur-badges ipv plain text
- Kolom "Datum" weg (staat al in filter)

### Routes beheren

- Apart pagina (via nav), zelden gebruikt
- Huidige functionaliteit behouden

### Visueel

- Clean light theme (wit/grijs) met accent kleuren per status
- Kaarten met subtiele schaduw en border-radius
- System font stack
- Geen framework, puur CSS
- Chart.js via CDN voor grafieken

## Technisch

### Nieuwe endpoint

- `GET /api/trend?days=7` — JSON: `{route_name: {date: count, ...}, ...}` voor Chart.js

### Template wijzigingen

- `base.html` — nieuw kleurschema, verbeterde nav
- `dashboard.html` — volledig herschrijven met 3 secties
- `login.html` — bijwerken naar nieuwe stijl
- `routes.html` — bijwerken naar nieuwe stijl

### Dependencies

- Chart.js 4.x via CDN (geen npm)
