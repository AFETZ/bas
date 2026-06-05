# C4 — Article package (copy-paste ready)

Source of truth: run_id `stage24_rf_stress_final_20260605T132014Z`,
run commit `9b312f25d2501a5f9b68614b464bcebcaf54e01e`, acceptance 24/24.
This is a **simulated route-level packet audit** (not hardware, not field PER).

## A. Five key numbers

1. **RSSI link integrity:** across the 45 116 audited route packets,
   `RSSI_from_Sionna` equals `RSSI_used_for_drop_decision` with a maximum
   difference of **0.0 dB** (0 non-zero-delta rows).
2. **Audited traffic:** **23 091** control and **22 025** payload route packets,
   all from live Sionna RT samples (`rt_online`), drops via `phy_rf_bernoulli`.
3. **Real payload through ns-3 (no bypass):** **21 433** payload packets entered
   the ns-3 payload channel, **5 622** were dropped, **15 811** left it;
   **14 130** RTP packets were received post-ns-3, yielding **799** decoded frames
   and **568** interruptions (> 1 s).
4. **Route-level loss vs RSSI (payload):** **0.536** in the weak/transition bin
   (−80…−78 dBm) and **0.034** in the strong bin (−72…−70 dBm); control behaves
   identically (0.543 → 0.039).
5. **Coverage:** RSSI used for drop decisions spanned the controlled transition
   corridor **−78.813 … −71.463 dBm**, with **0** no-coverage/sentinel floor rows.

## B. Recommended figures (2–3)

| Priority | File (use .pdf/.svg for camera-ready) | Why |
|---|---|---|
| 1 (main) | `figures/stage24_route_rssi_loss_bins_ieee` | quantitative C4 result: route-level loss vs RSSI, control+payload, CI + configured mapping |
| 2 | `figures/stage24_route_rssi_packet_trace_ieee` | shows the live Sionna RSSI(t) driving windowed control/payload loss over the route |
| 3 (optional) | `figures/stage24_route_rssi_map_ieee` | spatial view: route RSSI map (transition corridor) |

Keep `figures/stage24_payload_goodput_ieee` and
`figures/stage24_payload_interruptions_ieee` as supplementary; keep
`figures/rssi_loss_consistency_*` (controlled consistency check) as supplementary
only.

## C. Captions

**Fig. A — `stage24_route_rssi_loss_bins_ieee`.**
Route-level simulated packet loss versus RSSI for the Stage 2.4 live
Sionna-RT-to-ns-3 audit, separated into command (control) and payload channels.
Markers are route-level simulated loss per 2-dB RSSI bin with 95 % confidence
intervals; the dashed line is the configured RSSI-to-loss mapping. Both channels
follow the same monotonic trend. Values are simulated packet-level losses over a
controlled transition corridor, not measured packet-error rate.

**Fig. B — `stage24_route_rssi_packet_trace_ieee`.**
Live Sionna RT RSSI over ns-3 simulation time (top) and the corresponding
windowed (5 s) packet-loss ratio for the control and payload channels (bottom).
Loss rises as route RSSI weakens and recovers as it strengthens, confirming that
route-dependent radio samples propagate to ns-3 packet decisions.

**Fig. C (optional) — `stage24_route_rssi_map_ieee`.**
Route RSSI map of the Stage 2.4 RF-stress trajectory, coloured by live Sionna RT
RSSI and cropped to the travelled corridor (axes not to scale). RSSI weakens with
increasing distance from the ground transmitter, spanning the −78.8…−71.5 dBm
transition corridor used by the audit.

## D. One-paragraph C4 result text

The route-level C4 audit confirms that live Sionna RT RSSI values were used
directly by the ns-3 packet-level drop decision: across the 45 116 audited route
packets the maximum difference between the RSSI reported by Sionna and the RSSI
used for the drop decision was 0.0 dB. The run recorded 23 091 control and 22 025
payload route packets. The payload path was not a bypass path — 21 433 payload
packets entered the ns-3 payload channel, 5 622 were dropped, 15 811 left it, and
14 130 RTP packets were received after ns-3 traversal, decoding 799 frames and
recording 568 interruption events. The RSSI used for drop decisions spanned
−78.813 to −71.463 dBm with no rows assigned to the no-coverage sentinel floor.
The resulting loss-to-RSSI relation is consistent with the configured mapping:
payload packet loss reached 0.536 in the −80…−78 dBm weak-transition bin and fell
to 0.034 in the −72…−70 dBm strong bin, with the control channel showing the same
trend. This is interpreted as a simulated route-level integration audit rather
than a field measurement of packet-error rate; it demonstrates that
route-dependent radio samples propagate to ns-3 packet decisions and then to
post-ns-3 payload metrics.

## E. Mandatory wording / limitations to keep

- "simulated route-level packet audit"; "not hardware validation"; "not field PER
  measurement"; "controlled transition corridor −78.8…−71.5 dBm"; "payload bypass
  disabled"; "payload metrics computed after ns-3 traversal";
  "RSSI_from_Sionna matched RSSI_used_for_drop_decision with max delta 0.0".
- Route warnings (do not hide): `rf_back_07_target_-76dbm` and `return_home` were
  not reached within 160 s; other RF-stress waypoints were reached.
- Sionna RT ray tracing ran on the Mitsuba CUDA/OptiX backend; GPU was verified at
  **environment level** (total GPU-memory delta), **not** as an in-run delta for
  the final run, and is **not load-bearing** for the C4 claim. TensorFlow ran on
  CPU and is not used by the ray-tracing path. Do **not** claim "GPU-accelerated"
  as a performance result.
