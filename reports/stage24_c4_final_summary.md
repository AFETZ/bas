# Stage 2.4 C4 — Final Verification Summary

Route-level **live Sionna RT → ns-3 simulated packet audit** with real payload
through ns-3. This is a **simulated route-level packet audit**, NOT hardware
validation and NOT field PER measurement.

## 1. Provenance

| Field | Value |
|---|---|
| C4 status | **PASS** (24/24 acceptance criteria) |
| run_id | `stage24_rf_stress_final_20260605T132014Z` |
| commit | `9b312f25d2501a5f9b68614b464bcebcaf54e01e` (worktree dirty: audit/figure tooling uncommitted) |
| route-run rc | 0 |
| C4 rerun for this audit? | **No** — see §5 |

## 2. Run command (route-run)

```
sudo env BAS_STAGE24_PACKET_AUDIT=1 BAS_SIONNA_RT_ONLINE=1 \
     BAS_SIONNA_REQUIRE_GPU=1 BAS_MITSUBA_VARIANT=cuda_ad_mono_polarized \
     BAS_SIONNA_TARGET_FLOW=both \
     bash scripts/run_stage24_route_rssi_packet_audit.sh
```

Re-analysis (deterministic, from the run-dir only):

```
.venv/bin/python scripts/analyze_stage24_route_rssi_packet_audit.py \
    --run-dir logs/stage24_rf_stress_final_20260605T132014Z
.venv/bin/python scripts/analyze_stage24_payload_path.py \
    --run-dir logs/stage24_rf_stress_final_20260605T132014Z
.venv/bin/python scripts/audit_stage24_c4_acceptance.py
.venv/bin/python scripts/make_stage24_c4_figures_ieee.py
bash scripts/check_sionna_gpu_runtime.sh
```

## 3. GPU verification

| Field | Value |
|---|---|
| gpu_verified | **true** |
| method | `total_memory_delta_wsl2` |
| backend | Mitsuba `cuda_ad_mono_polarized` (CUDA/OptiX), Dr.Jit 1.3.1 |
| Mitsuba cuda render GPU mem delta | +239 MiB (probe); +195 MiB (independent run) |
| Sionna PathSolver on cuda variant | ran, valid RSSI output |
| TensorFlow GPUs | `[]` (CPU) — **diagnostic only**, NOT used by Sionna RT ray tracing |
| per-process nvidia-smi GPU memory | unavailable under WSL2 (not used) |
| GPU claim load-bearing for C4? | **No** — C4 validity is backend-independent |

GPU is verified at **environment level** (same `sionna_env` + cuda variant + OptiX
preload as the run). The final run's logs recorded the actual cuda variant
(`rt_variant = cuda_ad_mono_polarized × 12022`) but did **not** capture an in-run
GPU-memory delta (the old publisher logged only the variant string). The publisher
now logs `requested/actual variant`, `tf_gpus`, `gpu_verified`, `gpu_mem_delta`
and **fails fast** if `BAS_SIONNA_REQUIRE_GPU=1` and the cuda backend is not
confirmed by memory delta. Evidence: `reports/sionna_gpu_runtime_check.{md,json}`.

## 4. Payload path

| Field | Value |
|---|---|
| payload_bypass | false (`payload_bypass_disabled = true`) |
| require_ns3_payload | true |
| valid_payload_route_audit | true |
| path | Gazebo/video → UAV netns → tap-pload-near → ns-3 payload channel → tap-pload-far → RTP/H.264 receiver → decoded frames |
| payload entering ns-3 | 21433 |
| payload leaving ns-3 | 15811 |
| payload dropped by ns-3 | 5622 |
| post-ns-3 RTP received | 14130 |
| decoded frames | 799 |
| interruptions (> 1 s) | 568 |

Payload metrics are computed **only after ns-3 traversal**. (Note: the ns-3 packet
audit covers the 21433 payload packets that traversed the audited error model
during live-RT windows; goodput figure D shows this ns-3-audited payload subset.)

## 5. Rerun decision

**C4 was NOT rerun.** All acceptance criteria pass on the existing final run; the
C4 scientific claim does not depend on GPU acceleration; GPU is verified at
environment level; the article will not claim performance acceleration. Rerunning
a fragile multi-component stack (SITL+Gazebo+ns-3+video+Sionna) for a non-load-
bearing GPU claim would risk regressing a passing state. Provenance preserved: all
processed CSVs and figures carry exactly one run_id; no old/failed run data mixed.

## 6. RSSI audit

| Field | Value |
|---|---|
| RSSI_from_Sionna vs RSSI_used_for_drop_decision | identical, max delta **0.0 dB**, 0 nonzero-delta rows |
| live RT route packets used | 45116 (all `sionna_channel_model=rt_online`, `is_live_rt_route_packet=true`, `sionna_sample_valid=true`) |
| drop reason | `phy_rf_bernoulli` for all 12096 drops; `error_rate_reason=sionna_rt_loss` for all rows |
| RSSI used range | -78.813 … -71.463 dBm (controlled transition corridor) |
| no-coverage / sentinel floor rows | 0 |
| RSSI bins (normal / floor) | 10 / 0; all 10 ≥ 200 packets, none low-confidence |

## 7. Key final numbers (block for manual insertion)

```
run_id = stage24_rf_stress_final_20260605T132014Z
commit = 9b312f25d2501a5f9b68614b464bcebcaf54e01e
control packets               = 23091  (received 16892, dropped 6199)
payload route packets         = 22025  (received 16128, dropped 5897)
RSSI_from_Sionna == RSSI_used : max delta = 0.0 dB (0 nonzero-delta rows)
payload entering ns-3         = 21433
payload dropped by ns-3       = 5622
payload leaving ns-3          = 15811
post-ns-3 RTP received        = 14130
decoded frames                = 799
interruptions (>1 s)          = 568
RSSI used range               = -78.813 .. -71.463 dBm  (transition corridor)
no-coverage/sentinel floor rows = 0
route-level loss, weak bin  (-80..-78 dBm): control 0.543 | payload 0.536
route-level loss, strong bin(-72..-70 dBm): control 0.039 | payload 0.034
GPU backend = Mitsuba cuda_ad_mono_polarized (CUDA/OptiX), gpu_verified=true
             (env-level, total-memory delta; TensorFlow=CPU, not used by RT)
```

## 8. Figures for the article

| Figure | File (png/pdf/svg) | Role |
|---|---|---|
| A | `figures/stage24_route_rssi_loss_bins_ieee` | **main C4** — route-level simulated loss vs RSSI, control+payload panels, 95% CI + configured mapping |
| B | `figures/stage24_route_rssi_map_ieee` | route RSSI map (cropped to trajectory) |
| C | `figures/stage24_route_rssi_packet_trace_ieee` | live RSSI(t) + windowed control/payload loss |
| D | `figures/stage24_payload_goodput_ieee` | ns-3-audited payload goodput (offered vs delivered) |
| E | `figures/stage24_payload_interruptions_ieee` | post-ns-3 decoded-frame interruptions (log scale) |

Supplementary only (not main C4 figures): `rssi_loss_consistency*` (controlled
consistency check), `check_sionna_gpu_runtime` output, pre-scan map.

## 9. Limitations

- Simulated route-level packet audit; **not hardware validation**, **not field PER**.
- Covers a **controlled transition corridor -78.8 … -71.5 dBm**, not the full
  -90 … -65 dBm envelope; route avoids no-coverage blackout by design.
- Two recorder route warnings (not hidden):
  `rf_back_07_target_-76dbm not reached within 160.0 s`;
  `return_home not reached within 160.0 s`. Outbound RF-stress waypoints
  (strong/intermediate/weak-transition) were reached.
- GPU is **environment-level verified**, not run-level captured in the final run;
  GPU is **not load-bearing** for the C4 scientific claim.
- ns-3 payload audit covers the 21433 packets that traversed the audited error
  model during live-RT windows (≥10000 validity threshold met).

## 10. Reports / artifacts

- `reports/stage24_c4_acceptance_audit.md` / `.json` (24/24 PASS)
- `reports/sionna_gpu_runtime_check.md` / `.json` (gpu_verified=true)
- `reports/stage24_route_rssi_packet_audit.md`, `stage24_route_rssi_packet_summary.md`
- `reports/stage24_payload_path_audit.md`, `stage24_payload_metrics_summary.md`
- `data/processed/stage24_route_rssi_packets.csv`, `stage24_route_rssi_bins.csv`
- `data/processed/stage24_payload_packets.csv`, `stage24_payload_frames.csv`

## 11. Reproducibility / provenance

- **run_id:** `stage24_rf_stress_final_20260605T132014Z`
- **run commit:** `9b312f25d2501a5f9b68614b464bcebcaf54e01e` (worktree dirty at run time)
- **tooling commit:** `8d151bfe0b7bd636e772eb5f53d3faef92e1b455`
  (introduces the C4 evidence package: audit/figure/GPU scripts + reports +
  processed CSVs; parent = run commit `9b312f2`).

### Committed processed data (SHA-256)

| File | Size | SHA-256 |
|---|---|---|
| `data/processed/stage24_route_rssi_packets.csv` | 22 M | `0c40fb7f335c105ff043cabb6d2c1cced8fba9e0d59b6f3a8fe277f42bc9bc41` |
| `data/processed/stage24_route_rssi_bins.csv` | 4 K | `ec1bcc1646a1b79122a19dcecba4b717974d74438271c9a8fdd6fd221ef82758` |
| `data/processed/stage24_payload_packets.csv` | 7.4 M | `89ba94c02506baee1424d40faa5ac3f58b0f0d5bd0eafb6efe3aef1ab4879086` |
| `data/processed/stage24_payload_frames.csv` | 164 K | `96439d28aa633929868d2cea8868350d3779692438453fb8ce9e6c25ce42ef2c` |

### Raw run-dir evidence (gitignored — NOT committed, recorded by checksum)

Path prefix: `logs/stage24_rf_stress_final_20260605T132014Z/`

| File | Size | SHA-256 |
|---|---|---|
| `stage24_route_rssi_packets_raw.csv` | 19 M | `7ee085e331b441c7cbba1c629580a74089822b7db68b768ed6630be93d30ad49` |
| `sionna_rt_history.jsonl` | 7.6 M | `c8c41a617eee86630feb8d64ae14d588bd48bb2ca38706505b34e9c43573b02c` |
| `ns3_events.jsonl` | — | `c4ec6efa2b1c593b780adb4c63dfd3762d7e8e0fca2bdf9fd09ed7e485b17117` |
| `events.jsonl` | — | `ca3c498f760a019192421893e1d8833b34ac534edd77553e0f276fb960a3f298` |
| `video_rx.jsonl` | 3.0 M | `8b3fc0c8019b16e6388998d538f651a53b4e25800106be2c1d6e11e087755581` |
| `video_tx.jsonl` | 34 M | (large; size recorded, not committed) |

### Working-tree pipeline changes present at run time (dirty, NOT in this commit)

The run was produced with a dirty worktree. These Stage 2.4 pipeline files were
modified relative to the run commit and are **left as working-tree changes**
(not swept into the C4 evidence commit; not reviewed here; out of C4 audit scope):
`docker-compose.shared-netns.yml`, `orchestrator/src/orchestrator/real_components.py`,
`orchestrator/src/orchestrator/run.py`, `scripts/auto_demo_recorder.py`,
`scripts/gcs_web_ui_server.py`, `scripts/run_stage_1_5_2_mission.sh`,
`scripts/run_stage_2_1_sionna.sh`, `scripts/run_stage_2_4_auto_demo.sh`,
`scripts/run_stage_2_4_mavproxy_gcs.sh`, `scripts/run_stage_2_4_rt_online_demo.sh`,
`video/receiver.py`. The C4 **audit and figures are fully reproducible from the
committed processed CSVs**; full from-scratch re-run additionally requires these
pipeline files. Article sources (`PAPER/`, `PAPER_V2/`) were not touched.
