# AGENTS.md

Dagster code location for wifi.gtfs.zone, loaded into the feed-catalog
Dagster instance like gtfs-zone-timetable-sites and gtfs-zone-rt-archive.
It reads raw session JSON from the private bucket written by
gtfs-zone-wifi-ingest, snaps it to GTFS shapes, aggregates it and publishes
JSON and PMTiles for gtfs-zone-wifi-viz.

- **Raw data**: raw sessions are the source of truth and are never modified
  in place; aggregates are rebuilt from them.
- **Licenses**: published wifi.gtfs.zone data is ODbL-1.0. Third-party
  layers (Ookla open data is CC BY-NC-SA) are published separately and never
  merged into ODbL outputs.
- **Docs**: keep `docs/overview.md` in sync with decisions made in any wifi
  repo.

## Conventions

- **Commits**: Conventional Commits, enforced by the `commit-msg` hook. Never add
  Co-Authored-By trailers. Setup is in [CONTRIBUTING.md](CONTRIBUTING.md).
- **Cross-repo work**: sibling gtfs.zone repos live under the same parent
  directory and may be read and edited when a change spans repos.
- **Plans**: the cross-repo plan is `docs/overview.md`. A local
  `CURRENT_PLAN.md` here is neither tracked nor gitignored: never stage or
  commit it.
