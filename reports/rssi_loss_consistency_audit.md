# RSSI Loss Consistency Audit

## Scope

This audit separates two questions that were previously mixed in one figure:

- mission RF propagation over the Stage 2.4 trajectory;
- packet-level consistency between a configured RSSI-to-loss mapping and the
  actual ns-3 packet drop decisions.

The new result is the second item only. It is a simulated packet-level ns-3
consistency check, not hardware-measured PER and not field RF calibration.

## Repository RSSI-to-loss Mapping

The repository mapping is defined in `scripts/sionna_channel_publisher.py`:

```python
def rss_to_loss_ratio(rss_db: float) -> float:
    return 1.0 / (1.0 + math.exp(0.5 * (rss_db + 78.0)))
```

It is a logistic heuristic:

- center: `-78 dBm`;
- slope coefficient: `0.5`;
- high loss below roughly `-90 dBm`;
- near-zero loss above roughly `-65 dBm`.

The mapping is not a hardware-calibrated packet-error table. It is a
repository-level abstraction used to convert Sionna RSSI into packet loss and
delay inputs.

## Stage 2.4 Data Path

The live mission path is:

1. `scripts/sionna_channel_publisher.py` reads UAV flight events.
2. It computes RSSI from cached radio-map lookup or live Sionna RT, depending
   on launch mode.
3. It computes `loss_ratio = rss_to_loss_ratio(rss_db)`.
4. It writes JSON with `rss_db`, `path_loss_db`, `loss_ratio`, and
   `extra_delay_ms`.
5. `ns3/scenarios/two_channel.cc::sionna_poll_tick()` reads that JSON.
6. `sionna_poll_tick()` applies `loss_ratio` to the selected
   `RateErrorModel::ErrorRate`.
7. `two_channel.cc` counts aggregate `packets_rx` on `PhyRxEnd` and
   `packets_dropped_phy` on `PhyRxDrop`.
8. `two_channel.cc::emit_stats()` writes aggregate network rows to
   `ns3_events.jsonl`.

That path is suitable for mission evidence, but `RateErrorModel` does not
expose the per-packet random draw or a per-packet RSSI value. The Stage 2.4
logs therefore cannot prove that a plotted point used the exact RSSI at the
drop decision for that packet.

## Controlled Consistency Scenario

The new controlled scenario is `ns3/scenarios/rssi_loss_consistency.cc`.

It does not use the arbitrary Stage 2.4 trajectory. It creates fixed RSSI
states:

`-95, -90, -85, -82, -80, -78, -76, -74, -72, -70, -68, -65, -60 dBm`

For each RSSI target, flow, and seed, it sends a fixed number of packets through
a CSMA link. The receiver device uses `RssiLossDecisionErrorModel`, a custom
`ErrorModel` whose `DoCorrupt()` method:

- receives the packet being evaluated by ns-3;
- computes the same repository logistic mapping;
- draws a uniform random number;
- makes the actual drop/receive decision;
- writes one CSV row containing `packet_uid`, timestamp, flow, channel type,
  tx/rx nodes, `rssi_db_used`, expected probability, random draw, final
  decision, drop reason, seed, and RSSI target.

Because the CSV row is written inside `DoCorrupt()`, `rssi_db_used` is the RSSI
used for the drop decision in this controlled scenario. It is not merely a
publisher-side Sionna RT sample.

## Drop Reason

The controlled scenario labels RF drops with:

`drop_reason=phy_rf_bernoulli`

It does not model queue overflow, MAVLink bridge loss, application timeouts, or
orchestration-layer failures. Therefore the binned loss ratio is a check of the
ns-3 RF/drop implementation only.

## Payload Status

`two_channel.cc` has both `control` and `payload` ns-3 channels. The controlled
scenario checks both `control` and `payload` as ns-3 flows. This verifies the
packet-level drop implementation for payload-like traffic in ns-3. It does not
by itself prove that every high-rate FPV/video path in a full Stage 2.4 mission
is traversing ns-3; that remains a separate end-to-end integration check.

## Commands

Run the controlled experiment and analysis:

```bash
bash scripts/run_rssi_loss_consistency.sh
```

The wrapper records a reproducible command in
`reports/rssi_loss_consistency_summary.md`. It accepts these environment
variables: `RUN_ID`, `TARGETS`, `FLOWS`, `SEEDS`, `PACKETS_PER_POINT`,
`PACKET_BYTES`, `INTERVAL_MS`, `BASE_SEED`, `NS3_IMAGE`, and
`CONTAINER_NAME`.

Re-run only the analysis for an existing raw log:

```bash
.venv/bin/python scripts/analyze_rssi_loss_consistency.py \
  --input logs/<RUN_ID>/rssi_loss_consistency_per_packet.csv
```

## Verified Run

- Commit hash: `9b312f25d2501a5f9b68614b464bcebcaf54e01e`
- Worktree state during analysis: dirty
- Run ID: `rssi_loss_consistency_20260605T063505Z`
- Raw log:
  `logs/rssi_loss_consistency_20260605T063505Z/rssi_loss_consistency_per_packet.csv`
- Command:
  `RUN_ID=rssi_loss_consistency_20260605T063505Z TARGETS='-95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60' FLOWS='control,payload' SEEDS=5 PACKETS_PER_POINT=1000 PACKET_BYTES=256 INTERVAL_MS=0.2 BASE_SEED=1337 NS3_IMAGE=bas/ns3:dev bash scripts/run_rssi_loss_consistency.sh`
- RSSI targets:
  `-95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60`
- Flows: `control,payload`
- Seeds / independent ns-3 RNG streams per RSSI/flow: `5`
- Packets per RSSI/flow/seed: `1000`

## Outputs

- `data/processed/rssi_loss_consistency_per_packet.csv`
- `data/processed/rssi_loss_consistency_bins.csv`
- `figures/rssi_loss_consistency_ieee.png`
- `figures/rssi_loss_consistency_ieee.pdf`
- `figures/rssi_loss_consistency_ieee.svg`
- `reports/rssi_loss_consistency_summary.md`

## Limitations

- This is simulation-based and does not validate the logistic mapping against
  hardware measurements.
- Fixed RSSI targets isolate packet loss implementation consistency; they do
  not replace a live Sionna RT mission scenario.
- If empirical points fall outside the binomial confidence interval around the
  configured mapping, the result must be reported as an implementation mismatch
  or a statistical failure, not hidden by smoothing.
