#!/usr/bin/env python3
"""Video receiver — этап 1.5.2.a/b/d.

Принимает H.264 RTP-поток через UDP и через GstPadProbe на src-pad `udpsrc`
пишет JSONL-запись на каждый входящий RTP-пакет:

    {"event_type":"video_rx","wall_time":...,"rtp_seq":...,"rtp_ts_90khz":...,"size_bytes":...}

Параллельно (tee) пропускает поток в декодер avdec_h264 ! fakesink — это
нужно как «liveness check» что H.264 GOP/SPS/PPS валидны. По флагу
`--record-mp4` тот же RTP-поток mux'ится в MP4 для демо.

ENV:
    BAS_VIDEO_LISTEN_PORT  — default 5000
    BAS_VIDEO_LOG          — default /work/logs/video_rx.jsonl
    BAS_VIDEO_RECORD_MP4   — optional path to write received H.264 as MP4
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
    """См. video/sender.py: возвращает (seq, ts_90khz, size)."""
    if len(data) < 12:
        return None
    seq = struct.unpack(">H", data[2:4])[0]
    ts = struct.unpack(">I", data[4:8])[0]
    return seq, ts, len(data)


class JsonlWriter:
    """Асинхронный JSONL writer — см. video/sender.py для обоснования."""

    _SENTINEL: object = object()

    def __init__(self, path: Path, max_queue: int = 50000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(path, "a", buffering=1, encoding="utf-8")
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self.dropped_records = 0
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
    """
        udpsrc port=N caps=...rtp h264... !
          tee name=t
            t. ! queue ! rtph264depay ! avdec_h264 ! fakesink sync=false
            t. ! queue ! rtph264depay ! h264parse ! mp4mux ! filesink (optional)
    """
    pipeline_parts = [
        f"udpsrc name=net_src port={args.listen_port} "
        f"caps=\"application/x-rtp,media=video,clock-rate=90000,"
        f"encoding-name=H264,payload=96\" ! "
        f"tee name=t "
        f"t. ! queue leaky=downstream max-size-buffers=200 ! "
        f"rtph264depay ! avdec_h264 ! fakesink sync=false async=false"
    ]
    if args.record_mp4:
        record_path = Path(args.record_mp4)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline_parts.append(
            f"t. ! queue leaky=downstream max-size-buffers=400 ! "
            f"rtph264depay ! h264parse config-interval=-1 ! "
            f"mp4mux faststart=true ! "
            f"filesink location={record_path} async=false"
        )

    pipeline_str = " ".join(pipeline_parts)
    print(f"[video-receiver] pipeline: {pipeline_str}", flush=True)
    pipeline = Gst.parse_launch(pipeline_str)
    assert isinstance(pipeline, Gst.Pipeline)
    return pipeline


def on_rx_buffer_probe(
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
        hdr = parse_rtp_header(bytes(mapinfo.data))
        if hdr is None:
            return Gst.PadProbeReturn.OK
        seq, ts, size = hdr
        wall = time.time()
        writer.write({
            "event_type": "video_rx",
            "tap": "udpsrc_src_pad",
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
    ap.add_argument("--listen-port", type=int,
                    default=int(os.environ.get("BAS_VIDEO_LISTEN_PORT", "5000")))
    ap.add_argument("--log",
                    default=os.environ.get("BAS_VIDEO_LOG", "/work/logs/video_rx.jsonl"))
    ap.add_argument("--record-mp4",
                    default=os.environ.get("BAS_VIDEO_RECORD_MP4", ""))
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args()

    Gst.init(None)

    log_path = Path(args.log)
    writer = JsonlWriter(log_path)
    print(f"[video-receiver] log → {log_path}", flush=True)
    if args.record_mp4:
        print(f"[video-receiver] record mp4 → {args.record_mp4}", flush=True)

    start_wall = time.time()
    writer.write({
        "event_type": "video_rx_meta",
        "wall_time": start_wall,
        "listen_port": args.listen_port,
        "record_mp4": args.record_mp4 or None,
        "tap": "udpsrc_src_pad",
        "timing_source": "gst_pad_probe",
    })

    pipeline = build_pipeline(args)

    net_src = pipeline.get_by_name("net_src")
    assert net_src is not None, "udpsrc net_src не найден"
    src_pad = net_src.get_static_pad("src")
    assert src_pad is not None, "src-pad udpsrc net_src не найден"
    src_pad.add_probe(Gst.PadProbeType.BUFFER, on_rx_buffer_probe, writer, start_wall)

    loop = GLib.MainLoop()

    def on_message(_bus: Gst.Bus, message: Gst.Message) -> bool:
        t = message.type
        if t == Gst.MessageType.EOS:
            print("[video-receiver] EOS", flush=True)
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print(f"[video-receiver] ERROR: {err.message} ({dbg})", file=sys.stderr, flush=True)
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, dbg = message.parse_warning()
            print(f"[video-receiver] WARN: {err.message} ({dbg})", file=sys.stderr, flush=True)
        return True

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_message)

    def shutdown(*_: object) -> None:
        print("[video-receiver] shutdown requested", flush=True)
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
        print("[video-receiver] exited cleanly", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
