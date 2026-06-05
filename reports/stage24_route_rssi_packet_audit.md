# Stage 2.4 Route RSSI Packet Audit

This audit is for the route-level Stage 2.4 RT-online pipeline. It is separate from the controlled RSSI target consistency check.

## Code Path

1. `scripts/gcs_web_ui_server.py` writes `event_type=flight` rows from the live Stage 2.4 Web GCS telemetry.
2. `scripts/sionna_channel_publisher.py --rt-online` tails those flight rows, runs live Sionna RT PathSolver, and writes current `rss_db`, position, GCS/tx position, and `loss_ratio` to the RT JSON file.
3. `ns3/scenarios/two_channel.cc::sionna_poll_tick()` polls the RT JSON and updates the selected `AuditedRateErrorModel` instances for `control`, `payload`, or `both`.
4. `AuditedRateErrorModel::DoCorrupt()` performs the packet-level Bernoulli decision and writes the packet audit CSV row.

Because the CSV row is written inside `DoCorrupt()`, `RSSI_used_for_drop_decision` is the value stored in the ns-3 error model at the packet decision point.

## Evidence

- Commit hash: `9b312f25d2501a5f9b68614b464bcebcaf54e01e`
- Worktree state: `dirty`
- Run IDs: `stage24_rf_stress_final_20260605T132014Z`
- Raw packet CSV: `logs/stage24_rf_stress_final_20260605T132014Z/stage24_route_rssi_packets_raw.csv`
- Sionna history: `logs/stage24_rf_stress_final_20260605T132014Z/sionna_rt_history.jsonl`
- Events JSONL: `logs/stage24_rf_stress_final_20260605T132014Z/events.jsonl`
- ns-3 events JSONL: `logs/stage24_rf_stress_final_20260605T132014Z/ns3_events.jsonl`
- Full route-run command: `sudo env BAS_STAGE24_PACKET_AUDIT=1 BAS_SIONNA_RT_ONLINE=1 BAS_SIONNA_REQUIRE_GPU=1 BAS_MITSUBA_VARIANT=cuda_ad_mono_polarized BAS_SIONNA_TARGET_FLOW=both bash scripts/run_stage24_route_rssi_packet_audit.sh`
- Reanalysis command: `.venv/bin/python scripts/analyze_stage24_route_rssi_packet_audit.py --run-dir logs/stage24_rf_stress_final_20260605T132014Z`
- No-coverage floor threshold: `-120.0` dBm
- Packet rows total before live-RT filtering: `45712`
- Live RT route packet rows used: `45116`
- Sionna RT history rows: `12022`
- Sionna RT variants: `{'cuda_ad_mono_polarized': 12022}`
- Sionna channel model counts: `{'': 6, 'rt_online_warmup': 590, 'rt_online': 45116}`
- Max absolute RSSI_from_Sionna vs RSSI_used delta: `0.0`
- Recorder route warnings: `2`

## RSSI Link Check

Verified: every live RT packet row has `RSSI_from_Sionna == RSSI_used_for_drop_decision` within numeric precision.

## Route Execution Warnings

- `[recorder] WARN: rf_back_07_target_-76dbm not reached within 160.0s`
- `[recorder] WARN: return_home not reached within 160.0s`

## Limitations

- This is packet-level simulated loss over a Stage 2.4 route-run, not hardware-measured PER.
- Binned loss-vs-RSSI is secondary and depends on the RSSI distribution actually visited by the route.
- Payload is reported only if packets actually traverse the ns-3 payload channel during the run.
