#!/usr/bin/env python3
"""stretch-radar: what AI tool and MCP directories added since your last run, as stretchtext you can skim.

  python stretch_radar.py --demo --html radar.html              # bundled two-day sample, no network
  python stretch_radar.py --config sites.json --state state --html radar.html --md radar.md

Signal: new-term-radar's sitemap diff (words that 3+ new URLs share and older URLs rarely carried).
Sites on different hosts are read in parallel; each host keeps its own `pause` between requests.
Output: cc-stretch markup, `{{short|long}}`: one line per term or site, each opens in place to its URLs.
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import sys
import tempfile
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

UA = "stretch-radar/0.1 (+https://github.com/Dodai0419/stretch-radar)"
TIMEOUT, RETRIES, RETRY_PAUSE, DEFAULT_PAUSE = 40, 3, 10, 3
MIN_URLS, TOP, MIN_LEN = 3, 25, 3
LANGS = ("en|zh|tw|ja|ko|fr|de|es|pt|it|ru|ar|tr|vi|th|id|nl|pl|sv|da|fi|no|nb|cs|hu|ro|uk|el|he|hi|ms|fa|bn|"
         "ur|ta|te|sk|bg|hr|sr|sl|lt|lv|et|ca|tl|fil|sw")
_LOCALE = re.compile(rf"^(https?://[^/]+)/(?:{LANGS})(?:[-_][A-Za-z]{{2,4}})?(?=/)")
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)\s*(?:\]\]>)?\s*</loc>")
_SPLIT, _CAMEL = re.compile(r"[^a-z0-9.+#]+"), re.compile(r"(?<=[a-z])(?=[A-Z])")
# Words that are everywhere in AI tooling (new-term-radar's list): a surge in them is never a new term.
STOP = set("""
a an and are as at be by can for from has have in into is it its of on or that the this to with your you
using use used via based built make makes made new open source opensource simple fast free first best
ai agent agents agentic llm llms mcp server servers skill skills tool tools toolkit plugin plugins claude code
codex cursor gpt openai anthropic model models app apps api cli sdk framework library project repo repository
github python typescript javascript node rust go web based local self hosted hosted powered support supports
your all any more most one two three get set run runs build builder generate generator help helper helps
assistant assistants chat bot bots automation automate automated workflow workflows system systems data
md readme docs example examples demo test tests template templates collection awesome list curated
""".split())


# ---------- sitemap diff (new-term-radar sitemaps.py + terms.py) ----------

def fetch(url: str, sleep=time.sleep) -> str:
    req = urllib.request.Request(url.replace("&amp;", "&"), headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                body = res.read()
            return (gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body).decode("utf-8", "replace")
        except (OSError, ValueError):
            if attempt == RETRIES - 1:
                raise
            sleep(RETRY_PAUSE * (attempt + 1))
    return ""


def site_urls(s: dict, fetch, sleep) -> set[str]:
    shards, pause = s.get("shards"), s.get("pause", DEFAULT_PAUSE)
    files = [s["index"]] if not shards else \
        [u for u in _LOC.findall(fetch(s["index"])) if re.search(shards, u.replace("&amp;", "&"))]
    urls: set[str] = set()
    for i, u in enumerate(files):
        if i or shards:
            sleep(pause)
        urls.update(x.replace("&amp;", "&") for x in _LOC.findall(fetch(u)))
    return {_LOCALE.sub(r"\1", u) for u in urls} if s.get("strip_locale") else urls


def slug_text(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
    return re.sub(r"[/_\-]+", " ", re.sub(r"\b[0-9a-f]{8,}\b|\.(html|xml)$", " ", path))


def _words(text: str) -> list[str]:
    return [w for w in (w.strip(".") for w in _SPLIT.split(text.lower())) if len(w) >= MIN_LEN or any(c.isdigit() for c in w)]


def terms(text: str) -> set[str]:
    words, parts = _words(text or ""), _words(_CAMEL.sub(" ", text or ""))
    keep = {w for w in words + parts if w not in STOP and not w.isdigit()}
    return keep | {f"{a} {b}" for a, b in zip(words, words[1:])
                   if a != b and a not in STOP and b not in STOP and not a.isdigit() and not b.isdigit()}


def by_source(items) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for source, text in items:
        for t in terms(text):
            out[t].add(source)
    return out


def surges(recent, baseline, ratio) -> list[dict]:
    rows = [{"term": t, "recent": len(s), "baseline": len(baseline.get(t, ())),
             "lift": round(len(s) / (len(baseline.get(t, ())) * ratio + 1), 1)} for t, s in recent.items() if len(s) >= MIN_URLS]
    # ties: the longer phrase first, so "qwen image" is kept and its parts are dropped (upstream: set order decides)
    rows.sort(key=lambda r: (-r["lift"], -r["recent"], -len(r["term"].split()), r["term"]))
    kept: list[dict] = []
    for r in rows:
        if not any(r["term"] in k["term"].split() and r["recent"] <= k["recent"] for k in kept):
            kept.append(r)
    return kept[:TOP]


def _snap(path: Path):
    return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None


def diff(cfg: dict, state: Path, fetch=fetch, sleep=time.sleep, detail: dict | None = None) -> dict:
    """New URLs per site against the stored snapshot (same files as new-term-radar), then the surging terms."""
    sites = cfg.get("sitemaps") or {}
    if not sites:
        return {"skipped": True}
    hosts: dict[str, list[str]] = defaultdict(list)
    for site, s in sites.items():
        hosts[urlparse(s["index"]).netloc].append(site)
    got: dict[str, object] = {}

    def crawl(names):  # one host: its sites in order, `pause` seconds apart
        for j, site in enumerate(names):
            if j:
                sleep(sites[site].get("pause", DEFAULT_PAUSE))
            try:
                got[site] = site_urls(sites[site], fetch, sleep)
            except Exception as exc:  # noqa: BLE001 - one site down must not stop the others
                got[site] = exc

    with ThreadPoolExecutor(len(hosts)) as ex:
        list(ex.map(crawl, hosts.values()))
    report, recent, baseline = {}, [], []
    for site in sites:  # config order, as upstream
        now = got[site]
        if isinstance(now, Exception):
            report[site] = {"error": str(now)[:120]}
            continue
        if not now:
            report[site] = {"error": "no URLs read"}
            continue
        path = state / "sitemaps" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', site)}.json.gz"
        stored = _snap(path)
        old = set(stored) if stored is not None else None
        new = sorted(now - old) if old is not None else []
        report[site] = {"urls": len(now), "new": len(new), "first_run": old is None, "sample": new[:8]}
        recent += [(u, slug_text(u)) for u in new]
        baseline += [(u, slug_text(u)) for u in (old or set())]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(sorted(now), ensure_ascii=False).encode()))
    rec = by_source(recent)
    if detail is not None:
        detail.update({t: sorted(s) for t, s in rec.items()})
    return {"sites": report, "terms": surges(rec, by_source(baseline), len(recent) / max(len(baseline), 1))}


# ---------- stretchtext (cc-stretch parse.ts) ----------

def _open_fence(line: str):
    m = re.match(r"[ \t]*(`{3,}|~{3,})", line)
    return m.group(1) if m else None


def _fence_end(text: str, i: int):
    end = lambda a: len(text) if text.find("\n", a) == -1 else text.find("\n", a)
    fence = _open_fence(text[i:end(i)])
    if fence is None:
        return None
    at = end(i) + 1
    while at < len(text):
        line = text[at:end(at)].strip()
        if line.startswith(fence) and line == fence[0] * len(line):
            return min(end(at) + 1, len(text))
        at = end(at) + 1
    return len(text)


def parse(text: str) -> list:
    """`{{short|long}}` -> segments: str or {key, short, long (None without '|'), isClosed}; code is copied as written."""
    root, stack, root_sib = [], [], {}

    def write(chunk):
        if stack and stack[-1][0]["long"] is None:
            stack[-1][0]["short"] += chunk
            return
        segs = stack[-1][0]["long"] if stack else root
        if segs and isinstance(segs[-1], str):
            segs[-1] += chunk
        else:
            segs.append(chunk)

    i = 0
    while i < len(text):
        in_short = bool(stack) and stack[-1][0]["long"] is None
        stop = _fence_end(text, i) if i == 0 or text[i - 1] == "\n" else None
        if stop is None and text[i] == "`":
            run = re.match(r"`+", text[i:]).group()
            close = text.find(run, i + len(run))
            stop = len(text) if close == -1 else close + len(run)
        if stop is not None:
            write(text[i:stop])
            i = stop
        elif text.startswith("{{", i) and not in_short:
            s = {"key": "", "short": "", "long": None, "isClosed": False}
            (stack[-1][0]["long"] if stack else root).append(s)
            stack.append((s, {}))
            i += 2
        elif in_short and text[i] == "|":
            s, sib = stack[-1][0], (stack[-2][1] if len(stack) > 1 else root_sib)
            name = s["short"].strip().replace("\n", "↵")
            sib[name] = sib.get(name, 0) + 1
            own = name if sib[name] == 1 else f"{name}~{sib[name]}"
            s["key"] = f"{stack[-2][0]['key']} › {own}" if len(stack) > 1 else own
            s["long"] = []
            i += 1
        elif stack and text.startswith("}}", i):
            stack.pop()[0]["isClosed"] = True
            i += 2
        else:
            write(text[i])
            i += 1
    return root


def to_markdown(res: dict, day: str, detail: dict | None = None) -> str:
    esc = lambda s: s.replace("{{", "{ {").replace("}}", "} }").replace("|", "/")
    lines = [f"# Sitemap radar · {day}", ""]
    for r in res.get("terms", []):
        urls = (detail or {}).get(r["term"]) or []
        body = "\n".join(f"- {esc(u)}" for u in urls) or f"in {r['recent']} new URLs, {r['baseline']} before"
        lines.append(f"{{{{**{esc(r['term'])}** · {r['recent']} new pages · lift {r['lift']}|\n{body}\n}}}}")
    if not res.get("terms"):
        lines.append("No surging term today.")
    lines += ["", "## Sites", ""]
    for site, s in res.get("sites", {}).items():
        if "error" in s:
            lines.append(f"- {esc(site)}: error, {esc(s['error'])}")
        elif s["first_run"]:
            lines.append(f"- {esc(site)}: {s['urls']} URLs stored, new pages show from the next run")
        else:
            sample = "\n".join(f"- {esc(u)}" for u in s["sample"]) or "nothing new"
            lines.append(f"{{{{{esc(site)} · {s['urls']} URLs, {s['new']} new|\n{sample}\n}}}}")
    return "\n".join(lines) + "\n"


def to_html(md: str, title: str) -> str:
    """self-contained page: a stretch shows its short form; click to open it in place, « to close"""
    def render(segs):
        out = []
        for s in segs:
            if isinstance(s, str):
                out.append(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s)))
            elif s["long"] is None or not s["isClosed"]:
                out.append(html.escape(s["short"]))
            else:
                short = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s["short"]))
                out.append(f'<span class="st"><a href="#" class="short">{short}</a><span class="long" hidden>'
                           f'{render(s["long"])} <a href="#" class="close">«</a></span></span>')
        return "".join(out)
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>body{{font:15px/1.6 ui-monospace,Consolas,monospace;max-width:860px;margin:2rem auto;padding:0 16px;white-space:pre-wrap;
background:#fff;color:#1e1e1e}}a{{color:#1971c2}}.long{{background:#f1f3f5}}
@media (prefers-color-scheme:dark){{body{{background:#161616;color:#e9ecef}}a{{color:#74c0fc}}.long{{background:#25262b}}}}</style>
<body>{render(parse(md))}<script>
document.addEventListener("click", e => {{
  const a = e.target.closest("a.short, a.close"); if (!a) return; e.preventDefault();
  const st = a.closest(".st"), open = a.classList.contains("short");
  st.querySelector(":scope > .short").hidden = open; st.querySelector(":scope > .long").hidden = !open;
}});
</script>"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stretch_radar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help='JSON with "sitemaps": {name: {index, shards?, pause?, strip_locale?}}')
    p.add_argument("--state", default="state", help="snapshot folder (same layout as new-term-radar)")
    p.add_argument("--demo", action="store_true", help="bundled two-day sample, no network")
    p.add_argument("--md", help="write the stretchtext markdown here")
    p.add_argument("--html", help="write a self-contained stretchtext page here")
    p.add_argument("--json", action="store_true", help="print the raw diff instead of markdown")
    a = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    detail: dict = {}
    if a.demo:
        fx = Path(__file__).resolve().parent / "fixtures"
        cfg = json.loads((fx / "sites.json").read_text())
        with tempfile.TemporaryDirectory() as st:
            for day in ("day1", "day2"):
                res = diff(cfg, Path(st), fetch=lambda u, d=day: (fx / d / u.rsplit("/", 1)[1]).read_text(),
                           sleep=lambda s: None, detail=detail)
    elif a.config:
        res = diff(json.loads(Path(a.config).read_text(encoding="utf-8")), Path(a.state), detail=detail)
    else:
        p.error("give --config sites.json or --demo")
    md = to_markdown(res, date.today().isoformat(), detail)
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
    if a.html:
        Path(a.html).write_text(to_html(md, "Sitemap radar"), encoding="utf-8")
    print(json.dumps(res, indent=1) if a.json else md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
