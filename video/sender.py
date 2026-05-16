#!/usr/bin/env python3
"""Video sender — этап 1.5.2.a/b/d.

Строит GStreamer-пайплайн (источник видео → H.264 → RTP/UDP) и через
GstPadProbe на sink-pad `udpsink` пишет JSONL-запись на каждый RTP-пакет,
который реально уходит в UDP:

    {"event_type":"video_tx","wall_time":...,"rtp_seq":...,"rtp_ts_90khz":...,"size_bytes":...}

Это нужно, чтобы receiver затем смог сматчить пакеты по rtp_seq и посчитать
end-to-end latency / frame_loss.

Источник видео выбирается флагом `--source`:
    * videotestsrc (default) — синтетический шар, для 1.5.2.a smoke
    * camera — alias для udpsrc:${BAS_CAMERA_UDP_PORT:-5600}
    * udpsrc:<port> — приём готового H.264 RTP с loopback'а (если Gazebo-плагин
      уже формирует RTP), для 1.5.2.b
    * gz_image — отдельная ветка реализации, заглушка пока

ENV-параметры:
    BAS_VIDEO_DEST_HOST   — default 10.20.0.3
    BAS_VIDEO_DEST_PORT   — default 5000
    BAS_VIDEO_BITRATE_KBPS — default 2000
    BAS_VIDEO_FPS         — default 30
    BAS_VIDEO_WIDTH       — default 640
    BAS_VIDEO_HEIGHT      — default 480
    BAS_VIDEO_LOG         — default /work/logs/video_tx.jsonl
    BAS_VIDEO_SOURCE      — default videotestsrc
    BAS_CAMERA_UDP_PORT   — default 5600 (только для BAS_VIDEO_SOURCE=camera)
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import struct
import sys
import threading
import time
from pathlib import Path

import gi  # type: ignore
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # type: ignore  # noqa: E402


def parse_rtp_header(data: bytes) -> tuple[int, int, int] | None:
    """Возвращает (seq, ts_90khz, payload_size).

    RFC 3550 §5.1, фиксированный заголовок 12 байт:
        bytes 0-1  : V/P/X/CC/M/PT (нам не нужно)
        bytes 2-3  : sequence number (uint16 big-endian)
        bytes 4-7  : timestamp (uint32 big-endian, 90 kHz для H.264)
        bytes 8-11 : SSRC

    Возвращает None если буфер короче 12 байт.
    """
    if len(data) < 12:
        return None
    seq = struct.unpack(">H", data[2:4])[0]
    ts = struct.unpack(">I", data[4:8])[0]
    return seq, ts, len(data)


class JsonlWriter:
    """Асинхронный JSONL writer.

    `write()` лишь кладёт запись в очередь и возвращает управление сразу.
    Реальная сериализация и I/O идут в отдельном потоке. Это критично для
    GstPadProbe callback'а: probe вызывается синхронно в pipeline-thread'е,
    и блокировка на JSON-сериализации тормозит видеопоток, что отнимает
    CPU у orchestrator-MAVLink listener'а (см. INVALID_SEQUENCE regression
    в docs/stage_1_5_2_plan.md). Queue максимум 50k записей, при overflow
    drop-сохраняется счётчик `dropped_records`.
    """

    _SENTINEL: object = object()

    def __init__(self, path: Path, max_queue: int = 50000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(path, "a", buffering=1, encoding="utf-8")
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.dropped_records = 0
        self._stop = False
        self._thread = threading.Thread(
            target=self._drain, name=f"jsonl-{path.name}", daemon=True
        )
        self._thread.start()

    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is JsonlWriter._SENTINEL:
                break
            try:
                self._fp.write(item + "\n")
            except Exception:
                pass

    def write(self, record: dict) -> None:
        try:
            line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            return
        try:
            self._q.put_nowait(line)
        except queue.Full:
            self.dropped_records += 1

    def close(self) -> None:
        try:
            self._q.put(JsonlWriter._SENTINEL, timeout=5.0)
        except queue.Full:
            pass
        self._thread.join(timeout=10.0)
        try:
            self._fp.close()
        except Exception:
            pass


def build_pipeline(args: argparse.Namespace) -> Gst.Pipeline:
    """Собирает GStreamer pipeline:

        <source> ! videoconvert ! x264enc ! rtph264pay !
            udpsink name=net_sink host=<dest_host> port=<dest_port>

    Для smoke (`videotestsrc`) добавляем явный caps-filter с framerate/size.
    """
    if args.source == "camera":
        args.source = f"udpsrc:{os.environ.get('BAS_CAMERA_UDP_PORT', '5600')}"

    if args.source == "videotestsrc":
        source_chain = (
            f"videotestsrc pattern=ball is-live=true ! "
            f"video/x-raw,width={args.width},height={args.height},framerate={args.fps}/1 ! "
            f"videoconvert ! "
            f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={args.bitrate_kbps} "
            f"key-int-max={args.fps} ! "
            f"rtph264pay config-interval=1 pt=96 mtu=1200"
        )
    elif args.source.startswith("udpsrc:"):
        # Пере-RTP'ить готовый поток с loopback. Тут пакеты УЖЕ RTP H.264.
        # Просто перепакуем udpsink в нужный peer.
        port = int(args.source.split(":", 1)[1])
        source_chain = (
            f"udpsrc port={port} "
            f"caps=\"application/x-rtp,media=video,clock-rate=90000,"
            f"encoding-name=H264,payload=96\""
        )
    else:
        raise SystemExit(f"unknown --source: {args.source}")

    pipeline_str = (
        f"{source_chain} ! "
        f"queue leaky=downstream max-size-buffers=200 ! "
        f"udpsink name=net_sink host={args.dest_host} port={args.dest_port} "
        f"sync=false async=false"
    )

    print(f"[video-sender] pipeline: {pipeline_str}", flush=True)
    pipeline = Gst.parse_launch(pipeline_str)
    assert isinstance(pipeline, Gst.Pipeline)
    return pipeline


def on_tx_buffer_probe(
    _pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    writer: JsonlWriter,
    start_wall: float,
) -> Gst.PadProbeReturn:
    buf = info.get_buffer()
    if buf is None:
        return Gst.PadProbeReturn.OK
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        return Gst.PadProbeReturn.OK
    try:
        header = parse_rtp_header(bytes(mapinfo.data))
        if header is None:
            return Gst.PadProbeReturn.OK
        seq, ts, size = header
        wall = time.time()
        writer.write({
            "event_type": "video_tx",
            "tap": "udpsink_sink_pad",
            "timing_source": "gst_pad_probe",
            "wall_time": wall,
            "wall_dt": wall - start_wall,
            "rtp_seq": seq,
            "rtp_ts_90khz": ts,
            "size_bytes": size,
        })
    finally:
        buf.unmap(mapinfo)
    return Gst.PadProbeReturn.OK


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.environ.get("BAS_VIDEO_SOURCE", "videotestsrc"))
    ap.add_argument("--dest-host", default=os.environ.get("BAS_VIDEO_DEST_HOST", "10.20.0.3"))
    ap.add_argument("--dest-port", type=int,
                    default=int(os.environ.get("BAS_VIDEO_DEST_PORT", "5000")))
    ap.add_argument("--bitrate-kbps", type=int,
                    default=int(os.environ.get("BAS_VIDEO_BITRATE_KBPS", "2000")))
    ap.add_argument("--fps", type=int,
                    default=int(os.environ.get("BAS_VIDEO_FPS", "30")))
    ap.add_argument("--width", type=int,
                    default=int(os.environ.get("BAS_VIDEO_WIDTH", "640")))
    ap.add_argument("--height", type=int,
                    default=int(os.environ.get("BAS_VIDEO_HEIGHT", "480")))
    ap.add_argument("--log",
                    default=os.environ.get("BAS_VIDEO_LOG", "/work/logs/video_tx.jsonl"))
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="auto-stop after N seconds (0 = run forever)")
    args = ap.parse_args()

    Gst.init(None)

    log_path = Path(args.log)
    writer = JsonlWriter(log_path)
    print(f"[video-sender] log → {log_path}", flush=True)

    start_wall = time.time()
    writer.write({
        "event_type": "video_tx_meta",
        "wall_time": start_wall,
        "source": args.source,
        "dest": f"{args.dest_host}:{args.dest_port}",
        "bitrate_kbps": args.bitrate_kbps,
        "fps": args.fps,
        "resolution": f"{args.width}x{args.height}",
        "codec": "h264",
        "tap": "udpsink_sink_pad",
        "timing_source": "gst_pad_probe",
    })

    pipeline = build_pipeline(args)

    net_sink = pipeline.get_by_name("net_sink")
    assert net_sink is not None, "udpsink net_sink не найден в pipeline"
    sink_pad = net_sink.get_static_pad("sink")
    assert sink_pad is not None, "sink-pad udpsink net_sink не найден"
    sink_pad.add_probe(Gst.PadProbeType.BUFFER, on_tx_buffer_probe, writer, start_wall)

    loop = GLib.MainLoop()

    def on_message(_bus: Gst.Bus, message: Gst.Message) -> bool:
        t = message.type
        if t == Gst.MessageType.EOS:
            print("[video-sender] EOS", flush=True)
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print(f"[video-sender] ERROR: {err.message} ({dbg})", file=sys.stderr, flush=True)
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, dbg = message.parse_warning()
            print(f"[video-sender] WARN: {err.message} ({dbg})", file=sys.stderr, flush=True)
        return True

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_message)

    def shutdown(*_: object) -> None:
        print("[video-sender] shutdown requested", flush=True)
        pipeline.send_event(Gst.Event.new_eos())
        GLib.timeout_add(2000, lambda: (loop.quit(), False)[1])

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if args.max_seconds > 0:
        GLib.timeout_add(int(args.max_seconds * 1000), lambda: (shutdown(), False)[1])

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        writer.close()
        print("[video-sender] exited cleanly", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
