# gtfs-zone-wifi-pipeline

Dagster code location that turns raw wifi.gtfs.zone sessions into published
aggregates. Runs in the feed-catalog Dagster instance.

## Assets

Rebuilt from the raw bucket on every run (hourly schedule `wifi`):

| Asset | What it does |
|---|---|
| `raw_sessions` | Reads `raw/` in `wifi-raw`, validates against the pinned `session.v1.json`, reports invalid objects, skips ids in `config/excluded_sessions.yaml` |
| `trip_shapes` | Geometry and stops per Transitous trip from `/api/v6/trip`, cached under `cache/trips/` in `wifi-raw` |
| `skeleton` | Merges the trip shapes into a skeleton network (rasterize, thin, trace) and cuts each edge into 500 m bins |
| `snapped_samples` | Projects samples onto the trip shape, drops off-line and out-of-trip points, maps the rest to bins; station samples within 150 m |
| `route_aggregates` | Per route and bin, and per bin over all routes |
| `station_aggregates` | Checklist counts and wifi values per station |
| `publish` | Writes `index.json`, `routes/`, `stations.json`, `bins.pmtiles` and `LICENSE` to `data.wifi.gtfs.zone` |

The rules behind each step are in [docs/overview.md](docs/overview.md).

## Configuration

Environment:

| Variable | Default |
|---|---|
| `S3_ENDPOINT` | required (`file://<dir>` uses a local directory) |
| `S3_REGION` | `garage` |
| `RAW_S3_BUCKET`, `RAW_S3_ACCESS_KEY`, `RAW_S3_SECRET_KEY` | `wifi-raw`, required, required |
| `PUBLIC_S3_BUCKET`, `PUBLIC_S3_ACCESS_KEY`, `PUBLIC_S3_SECRET_KEY` | `data.wifi.gtfs.zone`, required, required |
| `TRANSITOUS_URL` | `https://api.transitous.org` |
| `WIFI_MIN_SESSIONS` | `1` |
| `WIFI_BIN_M` | `500` |
| `WIFI_EXCLUDED_SESSIONS` | `config/excluded_sessions.yaml` |

`tippecanoe` must be on `PATH` for `publish`; the image has it.

## Local run

Start Garage from [gtfs-zone-dev-stack](https://github.com/gtfs-zone/gtfs-zone-dev-stack)
with the `wifi-raw` bucket set up as in the gtfs-zone-wifi-ingest README, and
create the public bucket:

```bash
cd ../gtfs-zone-dev-stack
docker compose exec garage /garage -c /etc/garage.toml bucket create data.wifi.gtfs.zone
docker compose exec garage /garage -c /etc/garage.toml \
  bucket allow --read --write data.wifi.gtfs.zone --key GK0123456789abcdef01234567
```

Then:

```bash
cp .env.example .env
set -a; . ./.env; set +a
uv run dagster dev -m gtfs_zone_wifi_pipeline.pipeline.definitions
```

and materialize all assets in the UI, or launch the job with
`uv run dagster job launch -j wifi -m gtfs_zone_wifi_pipeline.pipeline.definitions`
while `dagster dev` runs.

## Development

```bash
uv sync
scripts/check.sh
```

`scripts/check.sh` runs ruff, vulture, deptry and pytest. The tests run
offline on the fixture sessions and recorded Transitous responses in
`tests/fixtures/`. The PMTiles test is skipped when `tippecanoe` is not
installed (`apt install tippecanoe`).

The schema in `src/gtfs_zone_wifi_pipeline/schemas/` and the sessions in
`tests/fixtures/sessions/` are copies from gtfs-zone-wifi-ingest.

## Docs

- [Project overview](docs/overview.md): the plan across all wifi.gtfs.zone
  repos
- [Market research](docs/market-research.md): existing tools and data
  sources

## License

Code: AGPL-3.0-or-later. See [LICENSE.txt](LICENSE.txt).

Published data: [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Third-party layers keep their own licenses.
