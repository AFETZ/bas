# Stage 2.4 RF-Stress Route Pre-Scan

This pre-scan selects route coordinates only. It does not create synthetic RSSI targets for the route-run packet CSV.

## Inputs

- Mode: `live_sionna_rt`
- Radio map: `/home/afetz/bas-prototype/radio_maps/iris_runway.npz`
- RT scene: `/home/afetz/bas-prototype/scene/iris_runway.xml`
- Route/live RT TX/GCS position: `0,-600,1.5`
- Cached map TX position: `not used`
- Altitude: `10.0` m
- Bounds north/east: `0.0..0.0` / `-240.0..240.0` m
- Grid step: `60.0` m
- No-coverage floor excluded from waypoint selection: `-120.0` dBm
- No-coverage avoid radius: `45.0` m
- Route order: `target_desc`
- Targets for waypoint selection: `-80, -78, -76, -74, -72` dBm

## Pre-Scan Coverage

- Valid scan points: `9`
- RSSI range in pre-scan: `-78.815..-71.458` dBm

## Selected Waypoints

- target -72 dBm -> north=0.0 m, east=-240.0 m, pre-scan RSSI=-71.458 dBm, error=0.542 dB, status=ok
- target -74 dBm -> north=0.0 m, east=-120.0 m, pre-scan RSSI=-73.955 dBm, error=0.045 dB, status=ok
- target -76 dBm -> north=0.0 m, east=0.0 m, pre-scan RSSI=-75.893 dBm, error=0.107 dB, status=ok
- target -78 dBm -> north=0.0 m, east=180.0 m, pre-scan RSSI=-78.172 dBm, error=0.172 dB, status=ok
- target -80 dBm -> north=0.0 m, east=240.0 m, pre-scan RSSI=-78.815 dBm, error=1.185 dB, status=ok

## Route-Run Rule

The generated trajectory JSON may include `prescan_target_rssi_db` metadata for traceability, but the Stage 2.4 route-run must use live Sionna RT RSSI from the current UAV/GCS coordinates. Acceptance must be decided from `RSSI_used_for_drop_decision` in the ns-3 packet audit CSV.

## Artifacts

- Pre-scan points CSV: `/home/afetz/bas-prototype/data/processed/stage24_rf_stress_prescan_points.csv`
- Trajectory JSON: `/home/afetz/bas-prototype/data/processed/stage24_rf_stress_waypoints.json`
- Pre-scan map: `/home/afetz/bas-prototype/figures/stage24_rf_stress_prescan_map.png/.pdf/.svg`