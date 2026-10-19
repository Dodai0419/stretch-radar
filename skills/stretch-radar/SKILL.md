---
name: stretch-radar
description: Diff public sitemaps of AI tool, MCP and skill directories to see which new names they started listing, and present them as click-to-grow stretchtext. Use for spotting new AI products or keywords early.
---

# stretch-radar

Use stretch-radar to find out which new AI products, models, MCP servers or skills public directories started listing since the last run, for example to pick SEO keywords, newsletter topics or competitors to watch. It diffs the sitemaps you list and reports words that 3+ new URLs share and older URLs rarely carried.

## Commands (run from the repo root; Python 3.9+, no dependencies)

```bash
python stretch_radar.py --demo                                   # bundled two-day sample, no network
python stretch_radar.py --config sites.example.json --state state --md radar.md --html radar.html
python stretch_radar.py --config sites.example.json --state state --json   # raw diff
```

The first run on a fresh `--state` only stores each sitemap; new pages show from the second run (run it daily). The state folder uses the same snapshot files as new-term-radar.

## Output
Markdown in cc-stretch markup: `{{short|long}}`. Each line is a term (`**qwen image** · 5 new pages · lift 5.0`) or a site; the long form is the list of new URLs. It is the markup the cc-stretch plugin uses for replies. `--html` writes a page where a click opens it and `«` closes it. When you summarise for a person, quote terms and URLs only from the output.

## Being polite to sites
Each site has a `pause` (seconds between requests to that host). Keep it at or above the site's robots.txt crawl-delay. Sites on the same host are read one after another.
