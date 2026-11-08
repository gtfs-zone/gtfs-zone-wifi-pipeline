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
- Raw layout in `wifi-raw`: `raw/v<schema_version>/<yyyy>/<mm>/<dd>/<session_id>.json`,
  dated by upload time (UTC), plus an `ids/<session_id>` marker holding the
  raw key, which makes uploads idempotent across days. The pipeline reads
  only `raw/`.
- Ingest limits: 2 MB per upload (sent and decompressed), gzip accepted, 30
  uploads per client IP per hour, held in memory.

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
- Rates: one passive sample every 10 s while a trip or station session is
  active, and one network test every 60 s.

### Session schema

- `schemas/session.v1.json` in gtfs-zone-wifi-ingest (JSON Schema 2020-12),
  with example sessions in its `tests/fixtures/sessions/`.
- The app and pipeline pin a copy and test against it.
- `schema_version` is an integer. Changes that break old sessions get a new
  version; raw sessions stay in the version they were uploaded in.

### Trip identification

- v1: manual pick from nearby departures via the
  [Transitous](https://transitous.org) API, plus an optional car number and
  class.
- Sessions store the raw Transitous ids (trip, route, stops) plus the GTFS
  feed, trip, route, agency and stop ids and service date derived from them,
  so they survive a change in the Transitous id format. The id formats are
  in gtfs-zone-wifi-ingest `schemas/README.md`.
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

### Stations

- A station session is started by hand at a stop picked from nearby
  Transitous stops, and carries the station checklist.
- Only samples within 150 m of the picked stop count toward it.
- Station aggregates are keyed by the stop id (the parent station when
  Transitous gives one).

### Aggregation

- Unit: fixed 500 m bins (configurable) along a skeleton of the trip
  shapes.
- The skeleton merges shapes that run along the same track, so trips with
  different shapes over the same line share bins. It uses the rasterize,
  skeletonize and trace method from gtfs-zone-timetable-sites
  `sandbox/maps/skeleton.py` (2 m cells, 24 m brush, 30 m spur pruning,
  bridge ratio 1.5).
- Samples are snapped to the trip's shape, then to the skeleton edge, then
  binned by distance along that edge.
- Per bin and route: share of samples with working wifi, median wifi RSSI,
  median latency, network test success rate, cell level by technology,
  outlet and portal report counts, and session count.
- Minimum session count before a bin is published: 1 for the test build,
  configurable.
- H3 and raster aggregates are out of scope until after the field test.

### Distribution

- Field test: debug APK installed by sideload.
- A store listing (Play Store, F-Droid or both) comes after the field test.
- No BeaconDB or OpenCelliD export yet.

### Scope

- All modes and any GTFS feed, with no geographic lock-in.

### Outputs

- Static artifacts first, in the public `data.wifi.gtfs.zone` bucket.
- A live "wifi along my trip" API only when static files stop being enough.

### Public artifacts

All under the root of `data.wifi.gtfs.zone`, ODbL-1.0:

- `index.json`: `updated_at` and the list of routes, each with its
  `route_key`, name, mode, agency, feed and session count.
- `routes/<route_key>.json`: the route's bins in order along the route,
  each with its distance range, medians and counts, plus stop positions
  along the route for the "wifi along this trip" strip.
- `stations.json`: per station, the checklist counts, session count and
  wifi results.
- `bins.pmtiles`: bin geometries with the same values, for the map.
- `LICENSE`: the ODbL notice and attribution.

`route_key` is `<feed>_<gtfs route_id>`, made URL-safe: characters outside
`[A-Za-z0-9._-]` are percent-encoded.

Third-party layers (such as Ookla open data) are published under their own
prefix with their own license file and never merged into these files.

### Licenses

- Code: AGPL-3.0-or-later.
- Published data: ODbL-1.0.
- Third-party baseline layers keep their own license and are published
  separately.

## Open questions

None block the v1 field test. Revisit after it:

- H3 or raster aggregates next to the bins
- Store listing: Play Store, F-Droid or both
- BeaconDB and OpenCelliD export

See [market-research.md](market-research.md) for existing tools and data
sources.
