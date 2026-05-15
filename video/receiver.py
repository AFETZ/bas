#!/usr/bin/env python3
"""Video receiver — этап 1.5.2.a/b.

Принимает H.264 RTP-поток через UDP и пишет JSONL-запись на каждый
входящий пакет:

    {"event_type":"video_rx","wall_time":...,"rtp_seq":...,"rtp_ts_90khz":...,"size_bytes":...}

Параллельно (tee) пропускает поток в декодер avdec_h264 ! fakesink — это
нужно как «liveness check» что H.264 GOP/SPS/PPS валидны. На метрики
прямо не влияет.

ENV:
    BAS_VIDEO_LISTEN_PORT — default 5000
    BAS_VIDEO_LOG        — default /work/logs/video_rx.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
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
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(path, "a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            self._fp.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass


def build_pipeline(args: argparse.Namespace) -> Gst.Pipeline:
    """
        udpsrc port=N caps=...rtp h264... !
          tee name=t
            t. ! queue ! appsink name=rx_tap emit-signals=true sync=false
            t. ! queue ! rtph264depay ! avdec_h264 ! fakesink sync=false
    """
    pipeline_str = (
        f"udpsrc port={args.listen_port} "
        f"caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" ! "
        f"tee name=t "
        f"t. ! queue leaky=downstream max-size-buffers=200 ! "
        f"appsink name=rx_tap emit-signals=true sync=false drop=false max-buffers=200 "
        f"t. ! queue leaky=downstream max-size-buffers=200 ! "
        f"rtph264depay ! avdec_h264 ! fakesink sync=false async=false"
    )
    print(f"[video-receiver] pipeline: {pipeline_str}", flush=True)
    pipeline = Gst.parse_launch(pipeline_str)
    assert isinstance(pipeline, Gst.Pipeline)
    return pipeline


def on_new_sample(appsink: Gst.Element, writer: JsonlWriter, start_wall: float) -> Gst.FlowReturn:
    sample = appsink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.OK
    buf = sample.get_buffer()
    if buf is None:
        return Gst.FlowReturn.OK
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        return Gst.FlowReturn.OK
    try:
        hdr = parse_rtp_header(bytes(mapinfo.data))
        if hdr is None:
            return Gst.FlowReturn.OK
        seq, ts, size = hdr
        writer.write({
            "event_type": "video_rx",
            "wall_time": time.time(),
            "wall_dt": time.time() - start_wall,
            "rtp_seq": seq,
            "rtp_ts_90khz": ts,
            "size_bytes": size,
        })
    finally:
        buf.unmap(mapinfo)
    return Gst.FlowReturn.OK


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen-port", type=int,
                    default=int(os.environ.get("BAS_VIDEO_LISTEN_PORT", "5000")))
    ap.add_argument("--log",
                    default=os.environ.get("BAS_VIDEO_LOG", "/work/logs/video_rx.jsonl"))
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args()

    Gst.init(None)

    log_path = Path(args.log)
    writer = JsonlWriter(log_path)
    print(f"[video-receiver] log → {log_path}", flush=True)

    start_wall = time.time()
    writer.write({
        "event_type": "video_rx_meta",
        "wall_time": start_wall,
        "listen_port": args.listen_port,
    })

    pipeline = build_pipeline(args)

    appsink = pipeline.get_by_name("rx_tap")
    assert appsink is not None, "appsink rx_tap не найден"
    appsink.connect("new-sample", on_new_sample, writer, start_wall)

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
