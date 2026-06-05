# Stage 2.4 Route RSSI Packet Summary

This is a Stage 2.4 route-level Sionna-to-ns-3 packet audit using live RT route samples. It is simulated packet-level evidence, not hardware validation.

## Counts

- Live RT route packets used: 45116
- Received: 33020
- Dropped: 12096
- RSSI range used for packet decisions: -78.813..-71.463 dBm
- Normal RSSI range above no-coverage floor: -78.813..-71.463 dBm
- RSSI bins: 10
- Normal RSSI bins: 10
- No-coverage floor bins: 0
- Recorder route warnings: 2
- control: attempted=23091 received=16892 dropped=6199
- payload: attempted=22025 received=16128 dropped=5897
- Payload status: payload packets traversed ns-3 and are included.

## Route Execution Warnings

- `[recorder] WARN: rf_back_07_target_-76dbm not reached within 160.0s`
- `[recorder] WARN: return_home not reached within 160.0s`
- Route RSSI coverage limitation: the final RF-stress corridor covers -78.813..-71.463 dBm; it avoids no-coverage blackout but does not span the full -90..-65 dBm envelope.

## Artifacts

- `data/processed/stage24_route_rssi_packets.csv`
- `data/processed/stage24_route_rssi_bins.csv`
- `figures/stage24_route_rssi_packet_trace.png` / `.pdf` / `.svg`
- `figures/stage24_route_rssi_map.png` / `.pdf` / `.svg`
- `figures/stage24_route_rssi_loss_bins.png` / `.pdf` / `.svg`
