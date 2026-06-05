# RSSI Loss Consistency Summary

This is a simulated packet-level ns-3 consistency check. It is not a hardware-measured PER result and it is not a field calibration of the radio channel.

## Result

- Status: 2 bins have the configured mapping outside the 95% CI; record this as an implementation/statistical mismatch candidate.
- Total attempted packets: 130000
- Received packets: 74605
- Dropped packets: 55395
- RSSI/flow bins passing thresholds: 26 / 26
- Minimum packet threshold per RSSI/flow bin: 1000
- Minimum seed threshold: 5
- Overall MAE over passing bins: 0.002365
- Maximum absolute error over passing bins: 0.009197
- control MAE: 0.002950
- payload MAE: 0.001780

## Mismatch Candidates

The configured mapping lies outside the 95% binomial confidence interval for the following bins. This may be a statistical tail under multiple comparisons, but it is reported explicitly rather than hidden.

- `control` at `-74.0 dBm`: empirical `0.128400`, expected `0.119203`, CI95 `[0.119412, 0.137959]`, abs error `0.009197`
- `payload` at `-60.0 dBm`: empirical `0.000600`, expected `0.000123`, CI95 `[0.000204, 0.001763]`, abs error `0.000477`

## Reproducibility

- Commit hash: `9b312f25d2501a5f9b68614b464bcebcaf54e01e`
- Worktree state: `dirty`
- Run IDs: `rssi_loss_consistency_20260605T063505Z`
- RSSI targets: `-95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60`
- Flows: `control,payload`
- Wrapper env vars: `RUN_ID`, `TARGETS`, `FLOWS`, `SEEDS`, `PACKETS_PER_POINT`, `PACKET_BYTES`, `INTERVAL_MS`, `BASE_SEED`, `NS3_IMAGE`, `CONTAINER_NAME`
- Raw per-packet log: `logs/rssi_loss_consistency_20260605T063505Z/rssi_loss_consistency_per_packet.csv`
- Processed per-packet CSV: `data/processed/rssi_loss_consistency_per_packet.csv`
- Processed bins CSV: `data/processed/rssi_loss_consistency_bins.csv`
- Figure prefix: `figures/rssi_loss_consistency_ieee`
- Command: `RUN_ID=rssi_loss_consistency_20260605T063505Z TARGETS='-95,-90,-85,-82,-80,-78,-76,-74,-72,-70,-68,-65,-60' FLOWS='control,payload' SEEDS=5 PACKETS_PER_POINT=1000 PACKET_BYTES=256 INTERVAL_MS=0.2 BASE_SEED=1337 NS3_IMAGE=bas/ns3:dev bash scripts/run_rssi_loss_consistency.sh`

## Interpretation

The RSSI value in the per-packet CSV is `rssi_db_used`, written inside `RssiLossDecisionErrorModel::DoCorrupt()`. Therefore it is the RSSI used for the drop decision in this controlled scenario, not merely a publisher-side Sionna RT sample.

Rows with `final_decision=dropped` use `drop_reason=phy_rf_bernoulli`; the scenario does not model application timeouts, queues, MAVLink bridge failures, or orchestration-layer loss.

Payload is checked as a controlled ns-3 payload flow in the calibration scenario. This does not by itself prove that every full Stage 2.4 high-rate video path is traversing ns-3 in a live mission.

## Artifacts

- `data/processed/rssi_loss_consistency_per_packet.csv`
- `data/processed/rssi_loss_consistency_bins.csv`
- `figures/rssi_loss_consistency_ieee.png`
- `figures/rssi_loss_consistency_ieee.pdf`
- `figures/rssi_loss_consistency_ieee.svg`
