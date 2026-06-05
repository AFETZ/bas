# Stage 2.4 C4 Acceptance Audit

- **Status:** PASS
- run_id: `stage24_rf_stress_final_20260605T132014Z`
- commit: `9b312f25d2501a5f9b68614b464bcebcaf54e01e`  (worktree dirty: True)
- criteria pass: 24/24  (blocking failures: none)

This audit is derived directly from processed/raw CSVs and the payload path
report, not from a hand summary. It is a **simulated route-level packet audit**,
not hardware validation and not field PER measurement.

## Acceptance table

| # | Criterion | Result | Detail |
|---|---|---|---|
| 1 | single run_id (route+payload) | PASS | route={'stage24_rf_stress_final_20260605T132014Z': 45116} payload={'stage24_rf_stress_final_20260605T132014Z': 22549} |
| 2 | no synthetic RSSI targets (route trace source = ns-3 error model only) | PASS | ns3_trace_source={'ReceiveErrorModel::DoCorrupt': 45116} |
| 3 | only rt_online + is_live + sample_valid rows | PASS | channel_model={'rt_online': 45116} is_live={'True': 45116} sample_valid={'True': 45116} |
| 4 | RSSI_from_Sionna == RSSI_used (max delta ~0) | PASS | max_delta_abs=0.0 count_nonzero_delta=0 |
| 5 | drop_reason = phy_rf_bernoulli for all drops | PASS | drop_reason={'none': 33020, 'phy_rf_bernoulli': 12096} err_reason={'sionna_rt_loss': 45116} |
| 6 | control packets >= 3000 | PASS | control=23091 |
| 7 | payload route packets >= 10000 | PASS | payload_route=22025 |
| 8 | payload entering ns-3 >= 10000 | PASS | entering_ns3=21433 |
| 9 | payload leaving ns-3 > 0 | PASS | leaving_ns3=15811 |
| 10 | payload dropped by ns-3 > 0 | PASS | dropped_by_ns3=5622 |
| 11 | post-ns3 RTP received > 0 | PASS | rtp_received=14130 |
| 12 | decoded frames > 0 | PASS | decoded_frames_csv=799 |
| 13 | interruptions present (>=0, reported) | PASS | interruptions=568 frame_gap_events_csv=798 |
| 14 | payload_bypass = false | PASS | payload_bypass=False payload_bypass_disabled=True |
| 15 | require_ns3_payload = true | PASS | require_ns3_payload=True |
| 16 | valid_payload_route_audit = true | PASS | valid_payload_route_audit=True |
| 17 | no-coverage/sentinel floor rows = 0 in route CSV | PASS | sentinel_rows(RSSI<=-119.0)=0 |
| 18 | RSSI range (excl floor) is a finite transition corridor | PASS | rssi_used_range=-78.813..-71.463 dBm |
| 19 | normal RSSI bins present | PASS | normal_bins=10 floor_bins=0 |
| 20 | bins pass packet-count threshold | PASS | bins_attempted>=200: 10/10; low_confidence_any=False |
| 21 | weak/transition bin loss > 0.2 exists | PASS | weak_bins=[('control', '-80.0', 0.543), ('control', '-78.0', 0.33), ('control', '-76.0', 0.254), ('payload', '-80.0', 0.536), ('payload', '-78.0', 0.346), ('payload', '-76.0', 0.243)] |
| 22 | strong bin loss < 0.05 exists | PASS | strong_bins=[('control', '-72.0', 0.039), ('payload', '-72.0', 0.034)] |
| 23 | IEEE figures regenerated (png+pdf+svg present) | PASS | figures_present={'stage24_route_rssi_loss_bins_ieee': True, 'stage24_route_rssi_map_ieee': True, 'stage24_route_rssi_packet_trace_ieee': True, 'stage24_payload_goodput_ieee': True, 'stage24_payload_interruptions_ieee': True} |
| 24 | no old/failed run mixed (all processed CSV single run_id) | PASS | processed route+payload CSVs carry exactly one run_id |

## Key numbers

- control packets: 23091
- payload route packets: 22025
- payload entering ns-3: 21433
- payload leaving ns-3: 15811
- payload dropped by ns-3: 5622
- post-ns3 RTP received: 14130
- decoded frames: 799
- interruptions: 568
- RSSI used range: -78.813..-71.463 dBm
- max RSSI_from_Sionna vs RSSI_used delta: 0.0
- no-coverage/sentinel floor rows: 0
- normal bins / floor bins: 10 / 0

Outputs: `reports/stage24_c4_acceptance_audit.json` (machine-readable).
