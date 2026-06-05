# Stage 2.4 Payload Path Audit

This report only treats payload metrics as valid when the video stream is received after the ns-3 payload channel.

## Path

Gazebo/video source -> UAV network namespace eth1 -> tap-pload-near -> ns-3 payload channel -> tap-pload-far -> GCS payload namespace -> RTP/H.264 receiver -> decoded frames/MJPEG Web GCS.

## Evidence

- Commit hash: `9b312f25d2501a5f9b68614b464bcebcaf54e01e`
- Worktree state: `dirty`
- Run dir: `/home/afetz/bas-prototype/logs/stage24_rf_stress_final_20260605T132014Z`
- Raw packet CSV: `logs/stage24_rf_stress_final_20260605T132014Z/stage24_route_rssi_packets_raw.csv`
- video_tx JSONL: `/home/afetz/bas-prototype/logs/stage24_rf_stress_final_20260605T132014Z/video_tx.jsonl`
- video_rx JSONL: `/home/afetz/bas-prototype/logs/stage24_rf_stress_final_20260605T132014Z/video_rx.jsonl`
- Runtime check: `/home/afetz/bas-prototype/logs/stage24_rf_stress_final_20260605T132014Z/payload_path_runtime.md`
- Stack log: `/home/afetz/bas-prototype/logs/stage24_rf_stress_final_20260605T132014Z/auto_demo_stack.log`
- Bypass disabled: `true`
- Bypass evidence source: `auto_demo_stack.log`
- Require ns-3 payload: `true`
- Require evidence source: `auto_demo_stack.log`
- Common wall-time interval: `1780665687.096596..1780668167.983590`
- Common analysis duration: `2480.887` s
- Time-sync mode: `video_tx+ns3_payload+video_rx_overlap`
- Time overlap valid: `true`
- Payload packet directions: `{'near_to_far': 21433, 'far_to_near': 1116}`
- Payload packets entering ns-3: `21433`
- Payload packets dropped by ns-3: `5622`
- Payload packets leaving ns-3: `15811`
- Payload ns-3 rows with explicit RTP sequence: `19766`
- RTP/video packets transmitted by sender: `175825`
- RTP/video packets received after ns-3: `14130`
- RTP tx sequences not observed at post-ns3 receiver: `50400`
- Decoded frames: `799`
- Frame gaps over 0.500s: `798`
- Interruption intervals over 1.000s: `568`
- Average post-ns3 goodput Mbps: `0.039567`
- Valid payload route audit: `true`

## Validity Rule

Payload route audit is valid only when bypass is disabled, BAS_REQUIRE_NS3_PAYLOAD=1, decoded post-ns3 video exists, and payload packets through ns-3 are >= 10000.

## Artifacts

- `data/processed/stage24_payload_packets.csv`
- `data/processed/stage24_payload_frames.csv`
- `figures/stage24_payload_goodput_trace.png` / `.pdf` / `.svg`
- `figures/stage24_payload_interruption_trace.png` / `.pdf` / `.svg`
- `figures/stage24_payload_rssi_loss_bins.png` / `.pdf` / `.svg`
