"""Вычисление метрик по контурам.

Метрики берутся из таблицы 1 и таблицы 5 архитектурного документа:
полётный контур, канал управления и телеметрии, видеоданные / полезная нагрузка,
сетевой контур, синхронизация.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlowMetrics:
    flow_id: str
    packets_total: int = 0
    packets_delivered: int = 0
    packets_lost: int = 0
    packets_in_outage: int = 0
    delays_s: list[float] = field(default_factory=list)
    jitters_s: list[float] = field(default_factory=list)
    bytes_sent: int = 0
    bytes_delivered: int = 0
    duration_s: float = 0.0
    configured_delay_s: float | None = None

    @property
    def pdr(self) -> float:
        if self.packets_total == 0:
            return 0.0
        return self.packets_delivered / self.packets_total

    @property
    def loss_ratio(self) -> float:
        if self.packets_total == 0:
            return 0.0
        return self.packets_lost / self.packets_total

    @property
    def mean_delay_ms(self) -> float:
        if not self.delays_s and self.configured_delay_s is not None:
            return 1000.0 * self.configured_delay_s
        return 1000.0 * statistics.mean(self.delays_s) if self.delays_s else 0.0

    @property
    def p95_delay_ms(self) -> float:
        if not self.delays_s:
            if self.configured_delay_s is not None:
                return 1000.0 * self.configured_delay_s
            return 0.0
        sorted_d = sorted(self.delays_s)
        k = max(0, int(0.95 * len(sorted_d)) - 1)
        return 1000.0 * sorted_d[k]

    @property
    def mean_jitter_ms(self) -> float:
        return 1000.0 * statistics.mean(j for j in self.jitters_s if j is not None) if self.jitters_s else 0.0

    @property
    def goodput_bps(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return 8.0 * self.bytes_delivered / self.duration_s


@dataclass
class FlightMetrics:
    samples: int = 0
    final_mode: str = ""
    landed: bool = False
    duration_s: float = 0.0
    waypoints_planned: int = 0
    waypoints_reached: int = 0
    final_position: dict[str, float] = field(default_factory=dict)
    position_format: str = ""   # "xyz" (stub) или "geodetic" (real)
    distance_flown_m: float = 0.0
    max_altitude_m: float = 0.0
    max_speed_mps: float = 0.0


@dataclass
class SyncMetrics:
    samples: int = 0
    mean_rtf: float = 0.0
    max_position_desync_m: float = 0.0


@dataclass
class VideoGap:
    lost_packets: int
    duration_ms: float
    approx_sim_start_s: float | None = None
    approx_sim_end_s: float | None = None
    near_payload_outage: bool = False
    nearest_outage: str = ""
    nearest_outage_distance_s: float | None = None


@dataclass
class PayloadOutageWindow:
    start_s: float
    end_s: float

    @property
    def label(self) -> str:
        return f"{self.start_s:.1f}-{self.end_s:.1f}s"


@dataclass
class VideoMetrics:
    """Frame-level метрики видео-канала (этап 1.5.2.a-metrics/d).

    Новые логи 1.5.2.d пишут TX/RX timestamps через GstPadProbe:
    `udpsink:sink` на sender-side и `udpsrc:src` на receiver-side. Для старых
    логов 1.5.2.a/b analyzer остаётся совместимым: если tap не указан, считаем
    timing source старым `tee → appsink`, а e2e latency помечаем как
    approximate subset.
    """
    flow_id: str = "video"
    enabled: bool = False             # ставим True если есть хоть какие-то rx/tx записи
    tx_tap: str = ""
    rx_tap: str = ""
    e2e_latency_precise: bool = False
    e2e_latency_window_s: float = 1.0

    tx_packets: int = 0               # уникальных (rtp_seq, rtp_ts_90khz)
    rx_packets: int = 0               # уникальных (rtp_seq, rtp_ts_90khz)

    frame_loss_ratio: float = 0.0     # на основе gap-detection в rx (wraparound-aware)
    matched_packets: int = 0          # сколько rx-пакетов нашли пару в tx
    synchronous_pairs: int = 0        # e2e-пары после sanity-window фильтра

    e2e_latency_ms_min: float = 0.0   # минимум по synchronous_pairs — реальная transit latency
    e2e_latency_ms_p50: float = 0.0   # median (может содержать rx-side rate-paced buffering)
    e2e_latency_ms_p95: float = 0.0
    e2e_latency_ms_max: float = 0.0

    jitter_ms_rfc3550: float = 0.0    # упрощённый RFC 3550 jitter (rx-only), ms
    fps_received: float = 0.0         # уникальных rtp_ts_90khz в rx / duration_s
    duration_s: float = 0.0           # длительность rx-потока

    bitrate_tx_appsink_bps: float = 0.0   # legacy name: фактически tx tap bitrate
    bitrate_rx_goodput_bps: float = 0.0   # авторитетно — байты дошедшие до rx

    longest_gap_packets: int = 0      # самый длинный пропуск rtp_seq подряд
    longest_gap_ms: float = 0.0       # его время по rx wall_time-разнице

    # 1.5.2.c: корреляция payload outage ↔ video RX gaps. Так как старые
    # ns-3 aggregate events не содержат wall_time, video timeline привязываем
    # к ns-3 sim_time приближённо: первый активный payload burst ↔ первый
    # принятый video RX пакет. Для realtime ns-3 этого достаточно, чтобы
    # показать попадание больших видео-gap'ов в outage-окна.
    payload_outage_windows: list[PayloadOutageWindow] = field(default_factory=list)
    outage_correlation_available: bool = False
    outage_tolerance_s: float = 2.0
    video_timeline_origin_sim_s: float | None = None
    video_gap_events: int = 0
    video_gap_packets_total: int = 0
    video_gap_events_near_outage: int = 0
    video_gap_packets_near_outage: int = 0
    video_gap_packets_outside_outage: int = 0
    top_gaps: list[VideoGap] = field(default_factory=list)


@dataclass
class RunReport:
    run_id: str
    scenario_id: str
    config_hash: str
    seed: int
    flows: dict[str, FlowMetrics]
    flight: FlightMetrics
    sync: SyncMetrics
    scenario_status: str
    versions: dict[str, str]
    video: VideoMetrics = field(default_factory=VideoMetrics)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _percentile(sorted_vals: list[float], q: float) -> float:
    """q in [0,1], sorted_vals must be sorted ascending. Linear-interp percentile."""
    if not sorted_vals:
        return 0.0
    k = q * (len(sorted_vals) - 1)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _wrap_signed_diff(cur: int, prev: int, modulus: int) -> int:
    """Возвращает (cur - prev) с учётом wraparound по `modulus`,
    в диапазоне [-modulus/2, modulus/2). Положительное значение — cur после prev,
    отрицательное — cur до prev (reorder)."""
    half = modulus // 2
    d = (cur - prev) % modulus
    if d > half:
        d -= modulus
    return d


def _parse_outage_spec(spec: str) -> list[PayloadOutageWindow]:
    windows: list[PayloadOutageWindow] = []
    for raw in spec.split(","):
        part = raw.strip()
        if not part or "-" not in part:
            continue
        start_raw, end_raw = part.split("-", 1)
        try:
            start_s = float(start_raw)
            end_s = float(end_raw)
        except ValueError:
            continue
        if end_s < start_s:
            start_s, end_s = end_s, start_s
        windows.append(PayloadOutageWindow(start_s=start_s, end_s=end_s))
    return windows


def _payload_outage_windows(events: list[dict[str, Any]] | None) -> list[PayloadOutageWindow]:
    if not events:
        return []

    for ev in events:
        if ev.get("event_type") == "component" and ev.get("component") == "ns3":
            pload = ev.get("pload", {})
            spec = str(pload.get("outage", "") or "")
            windows = _parse_outage_spec(spec)
            if windows:
                return windows

    # Fallback for logs where only aggregate network samples have outage_state.
    samples = sorted(
        (
            float(ev["sim_time"])
            for ev in events
            if ev.get("event_type") == "network"
            and ev.get("flow_id") == "payload"
            and ev.get("outage_state")
            and ev.get("sim_time") is not None
        )
    )
    if not samples:
        return []

    windows: list[PayloadOutageWindow] = []
    start = prev = samples[0]
    for t in samples[1:]:
        if t <= prev + 1.5:
            prev = t
            continue
        windows.append(PayloadOutageWindow(start_s=start, end_s=prev + 1.0))
        start = prev = t
    windows.append(PayloadOutageWindow(start_s=start, end_s=prev + 1.0))
    return windows


def _first_active_payload_sim_time(events: list[dict[str, Any]] | None) -> float | None:
    if not events:
        return None

    samples = sorted(
        (
            ev for ev in events
            if ev.get("event_type") == "network"
            and ev.get("flow_id") == "payload"
            and ev.get("sim_time") is not None
        ),
        key=lambda ev: float(ev.get("sim_time", 0.0)),
    )
    if not samples:
        return None

    fallback: float | None = None
    prev_tx: int | None = None
    prev_bytes: int | None = None
    for ev in samples:
        sim_time = float(ev.get("sim_time", 0.0))
        tx = int(ev.get("packets_tx", 0) or 0)
        bytes_tx = int(ev.get("bytes_tx", 0) or 0)
        if fallback is None and tx > 0:
            fallback = sim_time
        if prev_tx is not None and prev_bytes is not None:
            delta_tx = tx - prev_tx
            delta_bytes = bytes_tx - prev_bytes
            # Ignore small ARP/housekeeping traffic; the camera RTP stream appears
            # as a visible payload burst in the 1 Hz ns-3 aggregate samples.
            if delta_tx >= 10 or delta_bytes >= 10_000:
                return sim_time
        prev_tx = tx
        prev_bytes = bytes_tx
    return fallback


def _interval_distance_s(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    if a_start <= b_end and a_end >= b_start:
        return 0.0
    if a_end < b_start:
        return b_start - a_end
    return a_start - b_end


def _classify_gap_outage(
    gap_start_s: float | None,
    gap_end_s: float | None,
    windows: list[PayloadOutageWindow],
    tolerance_s: float,
) -> tuple[bool, str, float | None]:
    if gap_start_s is None or gap_end_s is None or not windows:
        return False, "", None
    if gap_end_s < gap_start_s:
        gap_start_s, gap_end_s = gap_end_s, gap_start_s

    best_window = windows[0]
    best_distance = _interval_distance_s(gap_start_s, gap_end_s, best_window.start_s, best_window.end_s)
    for window in windows[1:]:
        distance = _interval_distance_s(gap_start_s, gap_end_s, window.start_s, window.end_s)
        if distance < best_distance:
            best_window = window
            best_distance = distance
    return best_distance <= tolerance_s, best_window.label, best_distance


def compute_video(
    tx_events: list[dict[str, Any]],
    rx_events: list[dict[str, Any]],
    network_events: list[dict[str, Any]] | None = None,
) -> VideoMetrics:
    """Вычисляет VideoMetrics из video_tx.jsonl / video_rx.jsonl.

    Алгоритм:

    1. Дедуплицируем по rtp_seq на обеих сторонах (берём самый ранний wall_time
       и rtp_ts_90khz для каждого seq).

    2. На rx-side сортируем packets по `rtp_ts_90khz` — это монотонный 32-битный
       счётчик RTP-клока 90 kHz, wrap происходит каждые ~13 часов, так что в
       рамках одного прогона он строго возрастает (даёт sender-order). По нему
       обходим соседние rtp_seq и считаем wraparound-aware gap. Reordering
       пакетов (gap < 0) игнорируем.

    3. e2e latency = rx.wall_time - tx.wall_time по матчингу rtp_seq, но **только
       по «синхронным» парам** где `d ∈ [0, 1 s]`. Это нужно, потому что
       tx-таппинг через `tee → appsink` не синхронен с pipeline-clock'ом: один
       buffer может уйти в udpsink сразу, но в appsink-callback'е появиться
       через много секунд (GMainLoop busy). Несинхронные пары делают tx_wall
       произвольным относительно rx_wall и портят оценку. Отчёт показывает
       сколько пар попало в synchronous_pairs.

    4. RFC 3550 jitter — rx-only: J(i) = J(i-1) + (|D| - J(i-1))/16, где
       D = (arrival_i - arrival_{i-1}) - (ts_i - ts_{i-1}). Здесь arrival в ms
       (wall_time), ts в ms (90 kHz → /90). Не зависит от tx-side.

    5. FPS_rx = |{rtp_ts_90khz в rx}| / duration_rx_s.
    """
    vm = VideoMetrics()

    rx = [e for e in rx_events if e.get("event_type") == "video_rx"]
    tx = [e for e in tx_events if e.get("event_type") == "video_tx"]

    if not rx and not tx:
        return vm
    vm.enabled = True

    # ---- Detect timing source (1.5.2.d precision marker) ----
    # Старые логи 1.5.2.a/b пишут через `tee → appsink` (асинхронный с
    # pipeline-clock'ом → нужен широкий sanity-window). Новые 1.5.2.d пишут
    # через GstPadProbe на udpsink:sink и udpsrc:src (синхронные с
    # pipeline-clock'ом → можно сузить window до ~200 мс).
    #
    # Берём `tap` / `timing_source` из meta-event'а если есть, иначе из
    # первого packet-event'а. Default — legacy appsink ("tee_appsink").
    def _first_tag(events: list[dict[str, Any]], meta_type: str, key: str) -> str:
        for ev in events:
            if ev.get("event_type") == meta_type and key in ev:
                return str(ev.get(key) or "")
        for ev in events:
            if key in ev:
                return str(ev.get(key) or "")
        return ""

    vm.tx_tap = _first_tag(tx_events, "video_tx_meta", "tap") or "tee_appsink"
    vm.rx_tap = _first_tag(rx_events, "video_rx_meta", "tap") or "tee_appsink"
    tx_timing = _first_tag(tx_events, "video_tx_meta", "timing_source") or "tee_appsink"
    rx_timing = _first_tag(rx_events, "video_rx_meta", "timing_source") or "tee_appsink"
    vm.e2e_latency_precise = (
        tx_timing == "gst_pad_probe" and rx_timing == "gst_pad_probe"
    )
    # Адаптивное окно. Для precise GstPadProbe берём 500 мс: первые пакеты
    # реально приходят за ~30-50 мс (см. min latency ниже), но GStreamer
    # `udpsrc → tee → queue` буферизует и под нагрузкой dispatch-thread может
    # отставать на сотни ms (rate-paced scheduling). 500 мс ловит первые
    # ~30% пар с реальным transit; min e2e = чистая network latency без
    # rx-side rate-paced artifact'а.
    # Для legacy appsink держим 1 с (там tx_wall может отставать на секунды).
    vm.e2e_latency_window_s = 0.5 if vm.e2e_latency_precise else 1.0

    # ---- TX side: dedupe by rtp_seq -> earliest wall_time ----
    tx_first_wall: dict[int, float] = {}
    tx_total_size = 0
    for ev in tx:
        seq = ev.get("rtp_seq")
        if seq is None:
            continue
        wt = float(ev.get("wall_time", 0.0))
        if seq not in tx_first_wall or wt < tx_first_wall[seq]:
            tx_first_wall[seq] = wt
        tx_total_size += int(ev.get("size_bytes", 0))
    vm.tx_packets = len(tx_first_wall)

    if tx:
        wt = [float(e.get("wall_time", 0.0)) for e in tx]
        dur_tx = max(wt) - min(wt) if len(wt) > 1 else 0.0
        if dur_tx > 0:
            vm.bitrate_tx_appsink_bps = 8.0 * tx_total_size / dur_tx

    # ---- RX side: dedupe (earliest wall_time per seq) ----
    rx_first_wall: dict[int, float] = {}
    rx_first_ts: dict[int, int] = {}
    rx_ts_set: set[int] = set()
    rx_total_size = 0
    for ev in rx:
        seq = ev.get("rtp_seq")
        if seq is None:
            continue
        wt = float(ev.get("wall_time", 0.0))
        if seq not in rx_first_wall or wt < rx_first_wall[seq]:
            rx_first_wall[seq] = wt
            rx_first_ts[seq] = int(ev.get("rtp_ts_90khz", 0))
        ts = ev.get("rtp_ts_90khz")
        if ts is not None:
            rx_ts_set.add(int(ts))
        rx_total_size += int(ev.get("size_bytes", 0))
    vm.rx_packets = len(rx_first_wall)

    if not rx_first_wall:
        return vm

    rx_wall_times = sorted(rx_first_wall.values())
    duration_s = rx_wall_times[-1] - rx_wall_times[0] if len(rx_wall_times) > 1 else 0.0
    vm.duration_s = duration_s
    if duration_s > 0:
        vm.bitrate_rx_goodput_bps = 8.0 * rx_total_size / duration_s
        vm.fps_received = len(rx_ts_set) / duration_s

    # ---- Frame loss + longest gap + outage correlation ----
    # Sort by rtp_ts_90khz (sender-order, монотонный 32-bit clock); это даёт
    # корректный порядок отправки, по которому считаем gap'ы. uint16 rtp_seq
    # wraparound обрабатывается через _wrap_signed_diff.
    by_ts = sorted(
        rx_first_wall.items(),
        key=lambda kv: (rx_first_ts.get(kv[0], 0), kv[1]),
    )
    outage_windows = _payload_outage_windows(network_events)
    timeline_origin_sim = _first_active_payload_sim_time(network_events)
    vm.payload_outage_windows = outage_windows
    vm.outage_correlation_available = timeline_origin_sim is not None and bool(by_ts)
    vm.video_timeline_origin_sim_s = timeline_origin_sim

    total_gaps = 0
    longest_gap_pkts = 0
    longest_gap_wt = 0.0
    gap_details: list[VideoGap] = []
    prev_seq = by_ts[0][0]
    prev_wall = by_ts[0][1]
    first_rx_wall = by_ts[0][1]
    for seq, wall in by_ts[1:]:
        signed = _wrap_signed_diff(seq, prev_seq, 65536)
        if signed > 0:
            gap = signed - 1   # 0 если соседние
            if gap > 0:
                total_gaps += gap
                gap_wall_start = min(prev_wall, wall)
                gap_wall_end = max(prev_wall, wall)
                gap_duration_s = gap_wall_end - gap_wall_start
                approx_start_s: float | None = None
                approx_end_s: float | None = None
                if timeline_origin_sim is not None:
                    approx_start_s = timeline_origin_sim + (gap_wall_start - first_rx_wall)
                    approx_end_s = timeline_origin_sim + (gap_wall_end - first_rx_wall)
                near_outage, nearest_label, nearest_distance = _classify_gap_outage(
                    approx_start_s,
                    approx_end_s,
                    outage_windows,
                    vm.outage_tolerance_s,
                )
                if near_outage:
                    vm.video_gap_events_near_outage += 1
                    vm.video_gap_packets_near_outage += gap
                else:
                    vm.video_gap_packets_outside_outage += gap
                gap_details.append(VideoGap(
                    lost_packets=gap,
                    duration_ms=1000.0 * gap_duration_s,
                    approx_sim_start_s=approx_start_s,
                    approx_sim_end_s=approx_end_s,
                    near_payload_outage=near_outage,
                    nearest_outage=nearest_label,
                    nearest_outage_distance_s=nearest_distance,
                ))
                if gap > longest_gap_pkts:
                    longest_gap_pkts = gap
                    longest_gap_wt = gap_duration_s
            prev_seq = seq
            prev_wall = wall
        elif signed < 0:
            # reorder — текущий пакет «старше» предыдущего, пропускаем
            # (не обновляем prev_seq, чтобы продолжать сравнивать с monotonic).
            continue
    denom = total_gaps + len(by_ts)
    vm.frame_loss_ratio = total_gaps / denom if denom > 0 else 0.0
    vm.longest_gap_packets = longest_gap_pkts
    vm.longest_gap_ms = 1000.0 * longest_gap_wt
    vm.video_gap_events = len(gap_details)
    vm.video_gap_packets_total = total_gaps
    vm.top_gaps = sorted(
        gap_details,
        key=lambda gap: (gap.lost_packets, gap.duration_ms),
        reverse=True,
    )[:5]

    # ---- E2E latency: pad-probe → precise, иначе approximate ----
    # Для precise (GstPadProbe и на tx, и на rx) tx_wall ≈ момент ухода буфера
    # на UDP, rx_wall ≈ момент прихода → latency честная. Sanity-window
    # `vm.e2e_latency_window_s` (200 мс) служит только защитой от часовых
    # дёрганий — реальный канал даёт <50 мс.
    #
    # Для legacy appsink tx_wall может отставать на секунды (асинхронный
    # callback), поэтому window=1 s и пары вне его отбрасываются как мусор.
    window_s = vm.e2e_latency_window_s
    e2e_all: list[float] = []
    e2e_sync: list[float] = []
    for seq, rx_wall in rx_first_wall.items():
        tx_wall = tx_first_wall.get(seq)
        if tx_wall is None:
            continue
        d = rx_wall - tx_wall
        e2e_all.append(d)
        if 0.0 <= d <= window_s:
            e2e_sync.append(d)
    vm.matched_packets = len(e2e_all)
    vm.synchronous_pairs = len(e2e_sync)
    if e2e_sync:
        e2e_sorted = sorted(e2e_sync)
        vm.e2e_latency_ms_min = 1000.0 * e2e_sorted[0]
        vm.e2e_latency_ms_p50 = 1000.0 * _percentile(e2e_sorted, 0.50)
        vm.e2e_latency_ms_p95 = 1000.0 * _percentile(e2e_sorted, 0.95)
        vm.e2e_latency_ms_max = 1000.0 * e2e_sorted[-1]

    # ---- RFC 3550 jitter (rx-only) ----
    # Считаем по same sender-order (by_ts), используя rx wall_time как arrival
    # и rtp_ts_90khz как send-time. Independent of tx_wall_time, поэтому
    # авторитетно.
    j = 0.0
    prev_arr_ms: float | None = None
    prev_ts_ms: float | None = None
    for seq, wall in by_ts:
        arr_ms = wall * 1000.0
        ts_ms = rx_first_ts.get(seq, 0) / 90.0
        if prev_arr_ms is not None and prev_ts_ms is not None:
            d = abs((arr_ms - prev_arr_ms) - (ts_ms - prev_ts_ms))
            j += (d - j) / 16.0
        prev_arr_ms = arr_ms
        prev_ts_ms = ts_ms
    vm.jitter_ms_rfc3550 = j

    return vm


def compute(events: list[dict[str, Any]]) -> RunReport:
    flows: dict[str, FlowMetrics] = {}
    flight = FlightMetrics()
    sync = SyncMetrics()
    rtf_samples: list[float] = []
    desync_samples: list[float] = []

    run_id = ""
    scenario_id = ""
    config_hash = ""
    seed = 0
    versions: dict[str, str] = {}
    scenario_status = "unknown"

    first_pkt_time: dict[str, float] = {}
    last_pkt_time: dict[str, float] = {}
    prev_aggregate_tx: dict[str, int] = {}
    configured_delays_s: dict[str, float] = {}

    # для подсчёта траектории по двум форматам позиции
    prev_pos_xyz: tuple[float, float, float] | None = None
    prev_pos_geo: tuple[float, float, float] | None = None

    for ev in events:
        et = ev.get("event_type")

        if et == "run_start":
            run_id = ev.get("run_id", "")
            scenario_id = ev.get("scenario_id", "")
            config_hash = ev.get("config_hash", "")
            seed = ev.get("seed", 0)
            versions = ev.get("versions", {})

        elif et == "scenario":
            scenario_status = ev.get("status", scenario_status)
            if ev.get("status") == "success" and ev.get("reason") == "mission_landed":
                flight.landed = True

        elif et == "component":
            if ev.get("component") == "ns3" and ev.get("phase") == "start":
                ctrl = ev.get("ctrl", {})
                pload = ev.get("pload", {})
                if "delay_ms" in ctrl:
                    configured_delays_s["control"] = float(ctrl["delay_ms"]) / 1000.0
                if "delay_ms" in pload:
                    configured_delays_s["payload"] = float(pload["delay_ms"]) / 1000.0
            # Реальный режим: mission-runner emits waypoint_start/_done/mission_started с n_waypoints.
            if ev.get("component", "").startswith("mission-runner"):
                phase = ev.get("phase")
                if phase == "mission_started":
                    flight.waypoints_planned = ev.get("n_waypoints", flight.waypoints_planned)
                elif phase == "waypoint_done":
                    flight.waypoints_reached = max(flight.waypoints_reached, ev.get("idx", -1) + 1)
                elif phase == "mission_complete":
                    if ev.get("final_state") == "landed":
                        flight.landed = True
                    if "last_seq" in ev:
                        flight.waypoints_reached = max(flight.waypoints_reached, int(ev["last_seq"]) + 1)

        elif et == "flight":
            flight.samples += 1
            flight.final_mode = ev.get("flight_mode", flight.final_mode)
            if ev.get("mission_state") == "landed":
                flight.landed = True
            flight.duration_s = max(flight.duration_s, ev.get("sim_time", 0.0))

            pos = ev.get("position", {})
            # Stub-формат: {x, y, z}.
            if "x" in pos and "y" in pos and "z" in pos:
                flight.position_format = "xyz"
                flight.final_position = pos
                cur = (float(pos["x"]), float(pos["y"]), float(pos["z"]))
                if prev_pos_xyz is not None:
                    dx = cur[0] - prev_pos_xyz[0]
                    dy = cur[1] - prev_pos_xyz[1]
                    dz = cur[2] - prev_pos_xyz[2]
                    flight.distance_flown_m += math.sqrt(dx * dx + dy * dy + dz * dz)
                prev_pos_xyz = cur
                flight.max_altitude_m = max(flight.max_altitude_m, float(pos["z"]))
                # Stub: legacy поле waypoint_index в полётных событиях.
                flight.waypoints_reached = max(flight.waypoints_reached, ev.get("waypoint_index", 0))
            # Real-режим: {lat, lon, alt_amsl_m, alt_rel_m}.
            elif "lat" in pos and "lon" in pos:
                flight.position_format = "geodetic"
                flight.final_position = pos
                alt_rel = float(pos.get("alt_rel_m", 0.0))
                cur = (float(pos["lat"]), float(pos["lon"]), alt_rel)
                if prev_pos_geo is not None:
                    horiz = _haversine_m(prev_pos_geo[0], prev_pos_geo[1], cur[0], cur[1])
                    dz = cur[2] - prev_pos_geo[2]
                    flight.distance_flown_m += math.sqrt(horiz * horiz + dz * dz)
                prev_pos_geo = cur
                flight.max_altitude_m = max(flight.max_altitude_m, alt_rel)

            vel = ev.get("velocity_mps", {})
            # Real: {vx, vy, vz}; Stub: scalar в поле velocity_mps.
            if isinstance(vel, dict):
                vx = float(vel.get("vx", 0.0))
                vy = float(vel.get("vy", 0.0))
                vz = float(vel.get("vz", 0.0))
                flight.max_speed_mps = max(flight.max_speed_mps, math.sqrt(vx*vx + vy*vy + vz*vz))
            elif isinstance(vel, (int, float)):
                flight.max_speed_mps = max(flight.max_speed_mps, float(vel))

        elif et == "control_telemetry":
            if ev.get("message_type") == "MISSION_ITEM_REACHED":
                flight.waypoints_reached = max(flight.waypoints_reached, int(ev.get("seq", -1)) + 1)

        elif et in ("network", "payload"):
            flow_id = ev.get("flow_id", "<unknown>")
            fm = flows.setdefault(flow_id, FlowMetrics(flow_id=flow_id))
            if flow_id in configured_delays_s:
                fm.configured_delay_s = configured_delays_s[flow_id]

            # ns-3 currently emits one cumulative counter sample per second.
            if "packets_tx" in ev or "packets_rx" in ev:
                tx = int(ev.get("packets_tx", fm.packets_total) or 0)
                rx = int(ev.get("packets_rx", fm.packets_delivered) or 0)
                bytes_tx = int(ev.get("bytes_tx", fm.bytes_sent) or 0)
                bytes_rx = int(ev.get("bytes_rx", fm.bytes_delivered) or 0)

                prev_tx = prev_aggregate_tx.get(flow_id, tx)
                if ev.get("outage_state"):
                    fm.packets_in_outage += max(0, tx - prev_tx)
                prev_aggregate_tx[flow_id] = tx

                fm.packets_total = max(fm.packets_total, tx)
                fm.packets_delivered = max(fm.packets_delivered, rx)
                fm.packets_lost = max(0, fm.packets_total - fm.packets_delivered)
                fm.bytes_sent = max(fm.bytes_sent, bytes_tx)
                fm.bytes_delivered = max(fm.bytes_delivered, bytes_rx)

                t = ev.get("sim_time")
                if t is not None:
                    first_pkt_time.setdefault(flow_id, t)
                    last_pkt_time[flow_id] = t
                continue

            fm.packets_total += 1
            fm.bytes_sent += ev.get("size_bytes", 0)
            if ev.get("drop_reason") is None and ev.get("rx_time") is not None:
                fm.packets_delivered += 1
                fm.bytes_delivered += ev.get("size_bytes", 0)
                fm.delays_s.append(ev.get("delay", 0.0))
                jitter = ev.get("jitter")
                if jitter is not None:
                    fm.jitters_s.append(jitter)
            else:
                fm.packets_lost += 1
                if ev.get("drop_reason") == "outage":
                    fm.packets_in_outage += 1
            t = ev.get("tx_time", ev.get("sim_time"))
            if t is not None:
                first_pkt_time.setdefault(flow_id, t)
                last_pkt_time[flow_id] = t

        elif et == "sync":
            sync.samples += 1
            rtf = ev.get("real_time_factor")
            if rtf is not None:
                rtf_samples.append(rtf)
            ds = ev.get("position_desync_m")
            if ds is not None:
                desync_samples.append(ds)

    for flow_id, fm in flows.items():
        fm.duration_s = max(0.0, last_pkt_time.get(flow_id, 0.0) - first_pkt_time.get(flow_id, 0.0))

    if rtf_samples:
        sync.mean_rtf = statistics.mean(rtf_samples)
    if desync_samples:
        sync.max_position_desync_m = max(desync_samples)

    return RunReport(
        run_id=run_id,
        scenario_id=scenario_id,
        config_hash=config_hash,
        seed=seed,
        flows=flows,
        flight=flight,
        sync=sync,
        scenario_status=scenario_status,
        versions=versions,
    )
