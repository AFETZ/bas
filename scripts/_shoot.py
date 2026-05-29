#!/usr/bin/env python3
"""Headless screenshot batch runner (Playwright/chromium).

Reads a JSON jobs file: list of
  {"url","out","w","h","wait","full","clicks":[sel...]}
Opens each URL in a fresh page, optionally clicks selectors (e.g. admin tabs),
waits, and writes a PNG. device_scale_factor=2 → crisp retina shots.
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

jobs = json.loads(Path(sys.argv[1]).read_text())
rc = 0
with sync_playwright() as p:
    browser = p.chromium.launch(args=[
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-software-rasterizer", "--hide-scrollbars",
        "--force-color-profile=srgb",
    ])
    for j in jobs:
        out = Path(j["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        pg = browser.new_page(
            viewport={"width": j.get("w", 1440), "height": j.get("h", 900)},
            device_scale_factor=j.get("dsf", 2),
        )
        try:
            pg.goto(j["url"], wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"[shoot] {out.name}: goto warn: {str(e)[:80]}")
        pg.wait_for_timeout(j.get("wait", 2500))
        for sel in j.get("clicks", []):
            try:
                pg.click(sel, timeout=5000)
                pg.wait_for_timeout(j.get("click_wait", 1500))
            except Exception as e:
                print(f"[shoot] {out.name}: click {sel!r} warn: {str(e)[:60]}")
        try:
            pg.screenshot(path=str(out), full_page=j.get("full", False),
                          animations="disabled", timeout=20000)
            sz = out.stat().st_size
            print(f"[shoot] OK {out}  ({sz} bytes)")
            if sz < 2000:
                print(f"[shoot] WARN small screenshot: {out}")
        except Exception as e:
            print(f"[shoot] ERR {out}: {e}")
            rc = 1
        pg.close()
    browser.close()
sys.exit(rc)
