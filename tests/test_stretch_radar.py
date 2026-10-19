import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "fixtures"
sys.path.insert(0, str(ROOT))
import stretch_radar as sr  # noqa: E402

CFG = json.loads((FX / "sites.json").read_text())


def day_fetch(day):
    return lambda url: (FX / day / url.rsplit("/", 1)[1]).read_text()


def plain(segs):
    """parse() output in the JSON shape cc-stretch's parse.ts produces (no 'long' when there is none)."""
    out = []
    for s in segs:
        if isinstance(s, str):
            out.append(s)
        else:
            d = {"key": s["key"], "short": s["short"]}
            if s["long"] is not None:
                d["long"] = plain(s["long"])
            d["isClosed"] = s["isClosed"]
            out.append(d)
    return out


class ParseParity(unittest.TestCase):
    def test_same_segments_as_cc_stretch(self):
        for case in json.loads((ROOT / "tests" / "fixtures" / "parse_expected.json").read_text(encoding="utf-8")):
            self.assertEqual(plain(sr.parse(case["input"])), case["segments"], case["input"])


class RadarParity(unittest.TestCase):
    def test_same_diff_as_new_term_radar(self):
        want = json.loads((ROOT / "tests" / "fixtures" / "radar_expected.json").read_text())
        with tempfile.TemporaryDirectory() as st:
            got = [sr.diff(CFG, Path(st), fetch=day_fetch(d), sleep=lambda s: None) for d in ("day1", "day2")]
        self.assertEqual(got, want)


class Politeness(unittest.TestCase):
    def test_same_host_waits_other_hosts_overlap(self):
        cfg = {"sitemaps": {"a1": {"index": "https://a.example/1.xml", "pause": 0.3},
                            "a2": {"index": "https://a.example/2.xml", "pause": 0.3},
                            "b": {"index": "https://b.example/1.xml", "pause": 0.3}}}
        log, lock = [], threading.Lock()

        def fetch(url):
            with lock:
                log.append((url, time.perf_counter()))
            time.sleep(0.2)
            return f"<urlset><url><loc>{url}x</loc></url></urlset>"

        with tempfile.TemporaryDirectory() as st:
            t0 = time.perf_counter()
            sr.diff(cfg, Path(st), fetch=fetch)
            wall = time.perf_counter() - t0
        at = dict(log)
        self.assertGreaterEqual(abs(at["https://a.example/2.xml"] - at["https://a.example/1.xml"]), 0.2 + 0.3 - 0.02)
        self.assertLess(abs(at["https://b.example/1.xml"] - at["https://a.example/1.xml"]), 0.15)  # in parallel
        self.assertLess(wall, 3 * 0.2 + 2 * 0.3)  # one after another (upstream) takes at least 1.2 s


class Stretch(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as st:
            for d in ("day1", "day2"):
                self.res = sr.diff(CFG, Path(st), fetch=day_fetch(d), sleep=lambda s: None)

    def test_markdown_is_stretchtext(self):
        md = sr.to_markdown(self.res, "2026-10-19")
        stretches = [s for s in sr.parse(md) if isinstance(s, dict)]
        shorts = " ".join(s["short"] for s in stretches)
        self.assertIn("qwen image", shorts)
        self.assertIn("toolhub", shorts)
        self.assertTrue(all(s["isClosed"] and s["long"] for s in stretches))
        longs = json.dumps([plain(s["long"]) for s in stretches])
        self.assertIn("https://toolhub.example/tools/qwen-image-2-1-studio", longs)

    def test_html_escapes_and_has_viewer(self):
        md = "Hi {{<b>x</b>|<script>alert(1)</script> and **bold**}}"
        page = sr.to_html(md, "t")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn('class="st"', page)

    def test_cli_demo(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "radar.html"
            r = subprocess.run([sys.executable, str(ROOT / "stretch_radar.py"), "--demo", "--html", str(out)],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("{{", r.stdout)
            self.assertIn("qwen image", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
