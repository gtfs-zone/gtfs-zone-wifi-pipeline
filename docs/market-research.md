# Market research

What exists, what can be reused, and where wifi.gtfs.zone fits. Researched
November 2026.

## Summary

- No public, crowdsourced dataset of onboard wifi quality by operator, route
  or trip was found.
- Existing work is either operator-side tooling, one-off studies, or cell
  coverage maps that do not know which vehicle the phone was on.
- Most building blocks exist, so the custom parts are:
  - linking measurements to GTFS trips
  - captive portal and amenity reporting
  - the aggregation and publishing

## Closest existing work

### Icomera

- Onboard wifi supplier.
- Staff carry an in-house measurement app on trains.
- Not public. [Railway News](https://railway-news.com/icomera-measuring-wi-fi-quality-from-the-passengers-perspective/)

### NetForecast QMap Rail

- Continuous onboard wifi measurement, sold to train operators.
- Not public. [TrainComms](https://www.traincomms.com/news_detail.cfm?item=21406)

### Ookla

- Report on European train wifi from Speedtest Intelligence data. Sweden led
  in Q2 2025.
- It found that backhaul (roof antennas to mobile networks) drives
  performance more than cabin wifi.
- Commercial data. [Euronews](https://www.euronews.com/2025/10/14/wifi-speed-on-trains-in-europe-which-countries-provide-the-fastest-internet)

### Ofcom and UK studies

- Mobile performance on trains was poor in 58% to 83% of tests.
- A UK passenger study of rail connectivity, based on passively collected app
  data. [Mobile World Live](https://www.mobileworldlive.com/regulation/uk-regulator-bemoans-train-mobile-signal-failures/), [Keeping connected](https://d3cez36w5wymxj.cloudfront.net/wp-content/uploads/2020/07/23175047/Keeping-connected-passengers%E2%80%99-experience-of-internet-connectivity-on-Great-Britain%E2%80%99s-railways-FINAL.pdf)

### Hotspot finders

- WiFi Map, wifi.live.
- Fixed locations only, no moving vehicles. [WiFi Map](https://www.wifimap.io/blog/wifi-map-is-the-crowdsourced-app-that-helps-users-find-free-secure-internet-connectivity-around-the-world)

### CoverageMap

- Crowdsourced cellular speed tests in the US, CA, UK and PR.
- Has a paid API; no open data license found. [coveragemap.com](https://coveragemap.com/guides/how-does-coveragemap-work)

## Reusable pieces

### Android scanning

#### NeoStumbler

- MIT, Kotlin.
- Collects wifi, cell and Bluetooth observations, pauses when the device is
  not moving, and submits to BeaconDB. [GitHub](https://github.com/mjaakko/NeoStumbler)
- Reference code for the app's scanning service.

#### Tower Collector

- MPL-2.0.
- Cell tower logging; uploads to OpenCelliD and BeaconDB; CSV/GPX/KML export. [GitHub](https://github.com/zamojski/TowerCollector)

#### BeaconDB

- Open replacement for Mozilla Location Service, which shut down in 2024.
- Still experimental; public dumps are planned. [GitHub](https://github.com/beacondb/beacondb)
- Optional upload target.

### Speed tests

#### Open-RMBT

- Apache-2.0.
- The measurement stack behind RTR-Netztest (Austria): Android, iOS and JS
  clients, measurement servers, open data. [GitHub](https://github.com/rtr-nettest/open-rmbt-android)
- Upgrade path if light tests are not enough.

### Baseline cell layer

#### Ookla open data

- Quarterly z16 tiles (about 610m) of mobile and fixed performance.
- Parquet on `s3://ookla-open-data`.
- CC BY-NC-SA 4.0: non-commercial, so it stays a separate layer from the ODbL
  data. [ookla-open-data](https://awesome.ecosyste.ms/projects/github.com%2Fteamookla%2Fookla-open-data)

### Trip identification

#### Transitous

- Community-run global routing on MOTIS 2, built from public GTFS feeds.
- Usage policy:
  - open-source, non-commercial projects only
  - a User-Agent with contact info
  - a visible link to its sources page
  - contact the team before heavy use

  [API](https://transitous.org/api/)
- Source of departures for manual trip pick.

#### Onboard portal APIs

- DB ICE Portal and ÖBB Railnet expose train number, speed, position and
  connectivity state to devices on the train's wifi.
- Clients exist:
  - [onboardapis](https://pypi.org/project/onboardapis/)
  - [wifi-on-ice-portal-client](https://github.com/derhuerst/wifi-on-ice-portal-client)
  - [ICE-Buddy Train-API-Swift-Package](https://github.com/ICE-Buddy/Train-API-Swift-Package)
- Later: automatic trip identification and the provider's own connectivity
  state.

#### Träwelling

- AGPL check-in service for transit journeys, mostly DACH. [GitHub](https://github.com/Traewelling/traewelling)
- Not a dependency.

### Sync model

#### Mapillary

- Uploads capture sequences in batches; mapillary_tools zips each sequence.
  [mapillary_tools](https://github.com/mapillary/mapillary_tools)
- Same shape as wifi.gtfs.zone: a local queue of sessions, uploaded when on an
  unmetered network.

#### Syncthing

- Peer-to-peer folder sync.
- Not a fit for many phones uploading to one server.

## Platform constraints

### iOS

- No public API for wifi RSSI or cellular signal strength.
- `NEHotspotNetwork.signalStrength` is only filled in for apps with the
  NEHotspotHelper entitlement. [Apple forums](https://developer.apple.com/forums/thread/835163)
- An iOS app is limited to:
  - network tests
  - captive portal checks
  - manual ratings

### Android

- Exposes wifi scan results, the connected network's RSSI and link speed, and
  cell info.
- Wifi scans are throttled for background apps.
