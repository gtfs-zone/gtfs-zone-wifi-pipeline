# wifi.gtfs.zone overview

A crowdsourced, open dataset and map of onboard and station wifi, cell
coverage and seat outlets, tied to GTFS trips. Riders see what connectivity
to expect on a trip; operators can be compared against what they advertise.

## Data flow

```
gtfs-zone-wifi-app (Android)
  -> anonymous HTTPS batch upload
gtfs-zone-wifi-ingest (ingest.wifi.gtfs.zone)
  -> raw session JSON in a private Garage bucket
gtfs-zone-wifi-pipeline (Dagster code location in the feed-catalog instance)
  -> snap to GTFS shapes, aggregate, join baseline layers
  -> JSON and PMTiles in a public bucket
gtfs-zone-wifi-viz (viz.wifi.gtfs.zone)
```

## Repos

### gtfs-zone-wifi-app

- Android (Kotlin) collector, offline first
- Logs location, wifi and cell info while a trip is active
- Runs light network tests
- Collects manual inputs: trip, amenities, captive portal details and the
  station wifi checklist

### gtfs-zone-wifi-ingest

- Thin upload service
- Validates sessions against the session schema, which it owns
- Rate-limits by IP and writes raw sessions to the private bucket
- No database, no accounts

### gtfs-zone-wifi-pipeline

- Turns raw sessions into published aggregates
- Holds these docs

### gtfs-zone-wifi-viz

- Map of the aggregates
- "Wifi along this trip" demo of the published data

## Decisions

### Platforms

- Android first. iOS has no public API for wifi or cell signal strength.
- An iOS lite app (network tests, portal checks, manual ratings) may follow.

### Uploads

- Anonymous: no login, no device id; each upload carries only a random
  session UUID.
- IP addresses are used for rate limiting and never stored.
- The app trims each session to the trip (boarding to alighting stop) and
  stations before upload.
- Every raw session is kept, so aggregation can change later. Outliers and
  abuse are trimmed by hand.

### Measurements

- Passive:
  - location
  - wifi SSID, BSSID, RSSI, link speed
  - cell technology, operator, signal level
- Active:
  - reachability and captive portal checks
  - latency
  - a small (~100KB) timed fetch
- No full speed tests in v1. Open-RMBT is the upgrade path.

### Trip identification

- v1: manual pick from nearby departures via the
  [Transitous](https://transitous.org) API, plus an optional car number and
  class.
- Later:
  - matching the trace against GTFS shapes and GTFS-RT vehicle positions
  - reading the train from onboard portal APIs

### Manual inputs

- Onboard:
  - outlets/USB at seat
  - wifi present, working or broken
  - captive portal type, data cap, blocked ports or VPN
- Station wifi checklist:
  - needed email
  - needed a full account
  - needed SMS
  - needed a checkbox
  - needed a password
  - needed to pay
  - could not get it to work

### Scope

- All modes and any GTFS feed, with no geographic lock-in.

### Outputs

- Static artifacts first: per feed, route and shape JSON, plus PMTiles.
- A live "wifi along my trip" API only when static files stop being enough.

### Licenses

- Code: AGPL-3.0-or-later.
- Published data: ODbL-1.0.
- Third-party baseline layers keep their own license and are published
  separately.

## Open questions

- Session schema: sample rate, fields, versioning
- Minimum sample count before an aggregate is published
- Aggregation unit: shape segments, H3 cells, rasters, or several
- Station detection radius and how station sessions differ from trip sessions
- App distribution: Play Store, F-Droid, or both
- Whether to also submit cell and wifi observations to BeaconDB and OpenCelliD

See [market-research.md](market-research.md) for existing tools and data
sources.
