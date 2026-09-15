"""Publish REPORT.md to the documentation site as paginated HTML, plus PDF and Markdown downloads.

The brief asks for a write-up of about one to three pages. On the site that is taken literally: the
seven sections are dealt onto three pages, so a reader gets the same shape a printed report would
have instead of one unbroken scroll. `REPORT.md` in the repository stays the source of truth; this
script only renders it, so the two cannot disagree.

    python scripts/build_report_page.py

Outputs under `site/report/`: `index.html`, `page-2.html`, `page-3.html`, `REPORT.md`, `REPORT.pdf`.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "REPORT.md"
OUT = ROOT / "site" / "report"

# Which numbered sections land on which page. Grouped by argument rather than by length: the
# schema belongs beside the architecture it serves, and safety beside the escalation it governs.
PAGES: list[tuple[str, tuple[int, ...]]] = [
    ("Architecture & the artifact", (1, 2)),
    ("Determinism, errors & scale", (3, 4)),
    ("Escalation, safety & cuts", (5, 6, 7)),
]

STYLE = """
:root{
  --bg:#faf9f7; --surface:#fff; --surface-2:#f4f2ef; --border:#e8e4de; --border-strong:#d4cec6;
  --text:#1c1a17; --text-secondary:#59544c; --text-tertiary:#6e685f;
  --accent:#146b52; --accent-soft:#e8f2ed; --warning:#8a6410; --danger:#9c3428;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--serif);
  font-size:17px;line-height:1.62;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:0 20px 4rem}
header.top{border-bottom:1px solid var(--border);background:var(--surface);margin-bottom:2.5rem}
header.top .bar{max-width:820px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;
  gap:14px;flex-wrap:wrap;font-family:var(--sans);font-size:13px}
header.top a{color:var(--text-secondary);text-decoration:none}
header.top a:hover{color:var(--accent)}
header.top .brand{font-weight:700;color:var(--text);letter-spacing:-.01em}
header.top .spacer{flex:1}
.eyebrow{font-family:var(--sans);font-size:.72rem;font-weight:600;letter-spacing:.13em;
  text-transform:uppercase;color:var(--text-tertiary);margin-bottom:.5rem}
h1,h2,h3{font-family:var(--sans);text-wrap:balance;letter-spacing:-.018em;margin:0}
h1{font-size:clamp(1.9rem,4.5vw,2.5rem);font-weight:700;line-height:1.1;margin-bottom:1.5rem}
h2{font-size:1.4rem;font-weight:700;margin:2.4rem 0 .9rem;padding-bottom:.45rem;
  border-bottom:1px solid var(--border)}
h3{font-size:1.05rem;font-weight:600;margin:1.8rem 0 .6rem}
p{margin:0 0 1rem}
blockquote{margin:0 0 1.6rem;padding:1rem 1.25rem;background:var(--accent-soft);
  border-left:3px solid var(--accent);border-radius:3px;color:var(--text)}
blockquote p:last-child{margin:0}
code{font-family:var(--mono);font-size:.85em;background:var(--surface-2);padding:.1em .38em;
  border-radius:3px;word-break:break-word}
pre{background:var(--surface-2);border:1px solid var(--border);border-radius:4px;
  padding:1rem 1.15rem;overflow-x:auto;margin:0 0 1.4rem}
pre code{background:none;padding:0;font-size:.78rem;line-height:1.55}
table{border-collapse:collapse;width:100%;font-family:var(--sans);font-size:.88rem;
  margin:0 0 1.5rem;
  background:var(--surface);border:1px solid var(--border);border-radius:4px;overflow:hidden}
th,td{text-align:left;padding:.55rem .8rem;border-bottom:1px solid var(--border)}
th{background:var(--surface-2);font-size:.7rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--text-tertiary)}
tbody tr:last-child td{border-bottom:none}
a{color:var(--accent)}
ul,ol{margin:0 0 1.2rem;padding-left:1.3rem}
li{margin-bottom:.4rem}
hr{border:0;border-top:1px solid var(--border);margin:2rem 0}
.downloads{display:flex;gap:.6rem;flex-wrap:wrap;margin:0 0 2rem;font-family:var(--sans)}
.downloads a{display:inline-flex;align-items:center;gap:.4rem;font-size:.82rem;font-weight:600;
  text-decoration:none;padding:.45rem .85rem;border-radius:5px;
  border:1px solid var(--border-strong);
  background:var(--surface);color:var(--text-secondary)}
.downloads a:hover{border-color:var(--accent);color:var(--accent)}
.pager{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:3rem;
  padding-top:1.2rem;border-top:1px solid var(--border);font-family:var(--sans);font-size:.85rem}
.pager a{text-decoration:none;font-weight:600}
.pager .disabled{color:var(--text-tertiary);opacity:.5}
.steps{display:flex;gap:.4rem;font-family:var(--sans);font-size:.78rem;margin-bottom:2rem;
  flex-wrap:wrap}
.steps a,.steps span{padding:.3rem .7rem;border-radius:99px;border:1px solid var(--border);
  text-decoration:none;color:var(--text-secondary);background:var(--surface)}
.steps .here{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
@media print{
  header.top,.downloads,.pager,.steps{display:none}
  body{background:#fff;font-size:11.5pt}
  .wrap{max-width:none;padding:0}
  h2{page-break-after:avoid} pre,table,blockquote{page-break-inside:avoid}
  .page-break{page-break-before:always}
}
"""


REPO_BLOB = "https://github.com/adityamhaske/interface.ai/blob/main/"
REPO_TREE = "https://github.com/adityamhaske/interface.ai/tree/main/"


def _absolutise(html: str) -> str:
    """Point repository-relative links at GitHub.

    REPORT.md lives in the repository, so its links are written relative to it -- `evidence/`,
    `src/cua/...`, `AGENTS.md`. Those are correct on GitHub and dead on the site, which publishes
    only `site/`: the rendered pages were shipping 404s to the two directories the write-up most
    wants a reader to open. Rewriting here rather than in the source keeps REPORT.md readable in
    the repository, which is where most people will actually read it.

    A trailing slash means a directory, which GitHub serves under /tree/; everything else is a
    file, under /blob/.
    """

    def repoint(match: re.Match[str]) -> str:
        href = match.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        base = REPO_TREE if href.endswith("/") else REPO_BLOB
        return f'href="{base}{href}"'

    return re.sub(r'href="([^"]+)"', repoint, html)


def _render(md_text: str) -> str:
    html = MarkdownIt("commonmark", {"html": True}).enable("table").render(md_text)
    return _absolutise(html)


def _split_sections(md_text: str) -> tuple[str, dict[int, str]]:
    """The preamble, and each numbered `## N.` section keyed by its number."""
    parts = re.split(r"^(## \d+\..*)$", md_text, flags=re.M)
    preamble = parts[0]
    sections: dict[int, str] = {}
    for heading, body in zip(parts[1::2], parts[2::2], strict=True):
        number = int(re.match(r"## (\d+)\.", heading).group(1))  # type: ignore[union-attr]
        sections[number] = heading + body
    return preamble, sections


def _chrome(index: int, title: str, inner: str, *, for_print: bool = False) -> str:
    nav = "".join(
        f'<span class="here">{n + 1}. {t}</span>'
        if n == index
        else f'<a href="{"index.html" if n == 0 else f"page-{n + 1}.html"}">{n + 1}. {t}</a>'
        for n, (t, _) in enumerate(PAGES)
    )
    prev_href = "index.html" if index == 1 else f"page-{index}.html"
    next_href = f"page-{index + 2}.html"
    prev = (
        f'<a href="{prev_href}">← {PAGES[index - 1][0]}</a>'
        if index > 0
        else '<span class="disabled">← Start of the write-up</span>'
    )
    nxt = (
        f'<a href="{next_href}">{PAGES[index + 1][0]} →</a>'
        if index < len(PAGES) - 1
        else '<span class="disabled">End of the write-up</span>'
    )
    head = (
        ""
        if for_print
        else """<header class="top"><div class="bar">
  <a class="brand" href="../index.html">CUA</a>
  <a href="../index.html">Home</a><a href="../docs/index.html">Documentation</a>
  <span class="spacer"></span>
  <a href="https://github.com/adityamhaske/interface.ai">GitHub</a>
</div></header>"""
    )
    downloads = (
        ""
        if for_print
        else """<div class="downloads">
  <a href="REPORT.pdf" download>Download PDF</a>
  <a href="REPORT.md" download>Download Markdown</a>
  <a href="https://github.com/adityamhaske/interface.ai/blob/main/REPORT.md">View on GitHub</a>
</div>"""
    )
    steps = "" if for_print else f'<nav class="steps">{nav}</nav>'
    pager = "" if for_print else f'<div class="pager">{prev}{nxt}</div>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — CUA design write-up</title>
<meta name="description" content="Design write-up for the CUA computer-use automation system.">
<style>{STYLE}</style></head><body>
{head}<div class="wrap">{steps}{downloads}{inner}{pager}</div></body></html>"""


def main() -> int:
    preamble, sections = _split_sections(SOURCE.read_text(encoding="utf-8"))
    missing = [n for _, nums in PAGES for n in nums if n not in sections]
    if missing:
        print(f"REPORT.md is missing section(s) {missing}; expected 1-7", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, OUT / "REPORT.md")

    for index, (title, numbers) in enumerate(PAGES):
        body = "".join(sections[n] for n in numbers)
        inner = (_render(preamble) if index == 0 else f"<h1>{title}</h1>") + _render(body)
        name = "index.html" if index == 0 else f"page-{index + 1}.html"
        (OUT / name).write_text(_chrome(index, title, inner), encoding="utf-8")
        print(f"  site/report/{name}")

    whole = _render(preamble) + "".join(
        ('<div class="page-break"></div>' if i else "")
        + _render("".join(sections[n] for n in nums))
        for i, (_, nums) in enumerate(PAGES)
    )
    print_html = OUT / "_print.html"
    print_html.write_text(_chrome(0, "Design write-up", whole, for_print=True), encoding="utf-8")
    print("  site/report/REPORT.md")

    # Printed with the Chromium Playwright already installs, so the PDF is generated from the same
    # HTML a reader sees rather than from a second, drifting template.
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as play:
            browser = play.chromium.launch()
            page = browser.new_page()
            page.goto(print_html.resolve().as_uri(), wait_until="load")
            page.pdf(
                path=str(OUT / "REPORT.pdf"),
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            )
            browser.close()
        print("  site/report/REPORT.pdf")
    except Exception as exc:  # pragma: no cover - the HTML and Markdown are still published
        print(f"  (PDF skipped: {exc})", file=sys.stderr)
    finally:
        print_html.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
