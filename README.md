# gtfs-zone-wifi-pipeline

Dagster code location that turns raw wifi.gtfs.zone sessions into published
aggregates.

- Reads raw sessions written by gtfs-zone-wifi-ingest
- Snaps measurements to GTFS shapes and stops
- Aggregates wifi, cell and amenity data per trip segment, route and area
- Joins third-party baseline layers such as Ookla open data, kept separate
- Publishes JSON and PMTiles to a public bucket, read by gtfs-zone-wifi-viz

Runs in the feed-catalog Dagster instance.

## Docs

- [Project overview](docs/overview.md): the plan across all wifi.gtfs.zone
  repos
- [Market research](docs/market-research.md): existing tools and data
  sources

## License

Code: AGPL-3.0-or-later. See [LICENSE.txt](LICENSE.txt).

Published data: [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Third-party layers keep their own licenses.
