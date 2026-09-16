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

FONTS = (
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800"
    "&family=JetBrains+Mono:wght@400;500;600"
    "&family=Newsreader:ital,wght@0,400;0,600;1,400&display=swap"
)

STYLE = """
/* Page-specific only. The palette, page chrome, buttons and footer come from
   ../assets/site.css, which every page of this site loads -- so the write-up is the same
   design as the landing page and the documentation rather than a third one.

   Type follows the same rule as the rest of the site: serif for display, sans for reading.
   This page had it inverted, setting long-form body copy in serif against sans headings,
   which is precisely backwards from the other two pages. */
body{font-family:var(--font-sans);font-size:16px;line-height:1.65}
/* The nav is fixed, so every page has to reserve its height; this one never did and the
   first screen of the write-up rendered underneath it. */
.wrap{max-width:820px;margin:0 auto;padding:calc(var(--nav-height) + 2.5rem) 20px 4rem}
.eyebrow{font-family:var(--font-sans);font-size:.72rem;font-weight:600;letter-spacing:.13em;
  text-transform:uppercase;color:var(--text-muted);margin-bottom:.5rem}
h1,h2,h3{font-family:var(--font-serif);text-wrap:balance;letter-spacing:-.018em;margin:0}
h1{font-size:clamp(1.9rem,4.5vw,2.5rem);font-weight:600;line-height:1.15;margin-bottom:1.5rem}
h2{font-size:1.45rem;font-weight:600;margin:2.4rem 0 .9rem;padding-bottom:.45rem;
  border-bottom:1px solid var(--border)}
h3{font-size:1.1rem;font-weight:600;margin:1.8rem 0 .6rem}
p{margin:0 0 1rem;color:var(--text-secondary)}
strong{color:var(--text);font-weight:600}
blockquote{margin:0 0 1.6rem;padding:1rem 1.25rem;background:var(--accent-bg);
  border-left:3px solid var(--accent);border-radius:var(--radius-sm);color:var(--text)}
blockquote p:last-child{margin:0;color:var(--text)}
code{font-family:var(--font-mono);font-size:.85em;background:var(--bg-code);padding:.1em .38em;
  border-radius:3px;word-break:break-word}
pre{background:var(--bg-code);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:1rem 1.15rem;overflow-x:auto;margin:0 0 1.4rem}
pre code{background:none;padding:0;font-size:.78rem;line-height:1.55}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:0 0 1.5rem;
  background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-sm);
  overflow:hidden}
th,td{text-align:left;padding:.55rem .8rem;border-bottom:1px solid var(--border)}
th{background:var(--bg-code);font-size:.7rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--text-muted)}
td{color:var(--text-secondary)}
tbody tr:last-child td{border-bottom:none}
a{color:var(--accent)}
ul,ol{margin:0 0 1.2rem;padding-left:1.3rem;color:var(--text-secondary)}
li{margin-bottom:.4rem}
hr{border:0;border-top:1px solid var(--border);margin:2rem 0}

/* The download row uses the shared .btn shapes so it reads as the same control as every other
   button on the site, rather than a fourth button style that exists only here. */
.downloads{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 2rem}
.pager{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-top:3rem;
  padding-top:1.2rem;border-top:1px solid var(--border);font-size:.85rem}
.pager a{text-decoration:none;font-weight:600;color:var(--accent)}
.pager .disabled{color:var(--text-muted);opacity:.6}
.steps{display:flex;gap:.4rem;font-size:.78rem;margin-bottom:2rem;flex-wrap:wrap}
.steps a,.steps span{padding:.3rem .7rem;border-radius:99px;border:1px solid var(--border);
  text-decoration:none;color:var(--text-secondary);background:var(--bg-card)}
.steps a:hover{border-color:var(--accent);color:var(--accent)}
.steps .here{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
/* The submission format: a printed document, not a web page. The screen rules above are tuned
   for a monitor -- generous line-height, large rem-scaled headings, sans-serif body copy -- and
   applied to a PDF they cost three pages nobody asked for before the first sentence of content.
   Print gets its own type scale and spacing entirely, in points rather than rem, because nothing
   about "readable on a screen" transfers to "compact on a printed page". */
@media print{
  nav,.downloads,.pager,.steps,footer{display:none}
  *{box-shadow:none!important;text-shadow:none!important}
  body{background:#fff;color:#000;font-family:"Times New Roman",Times,"Liberation Serif",serif;
    font-size:11pt;line-height:1.18}
  .wrap{max-width:none;padding:0}
  .eyebrow{display:none}
  h1,h2,h3{font-family:"Times New Roman",Times,"Liberation Serif",serif;letter-spacing:normal;
    text-wrap:normal;color:#000}
  h1{font-size:16pt;font-weight:700;line-height:1.15;margin:0 0 7pt}
  h2{font-size:12.5pt;font-weight:700;margin:8pt 0 3pt;padding-bottom:2pt;
    border-bottom:.75pt solid #000;page-break-after:avoid}
  h3{font-size:11pt;font-weight:700;margin:7pt 0 2pt;page-break-after:avoid}
  p{margin:0 0 5pt;color:#000;text-align:justify}
  strong{color:#000;font-weight:700}
  blockquote{margin:0 0 8pt;padding:3pt 9pt;background:none;border-left:2pt solid #000;
    border-radius:0;color:#000}
  blockquote p{text-align:left}
  blockquote p:last-child{margin:0;color:#000}
  code{font-family:"Courier New",Courier,monospace;font-size:9pt;background:#eee;
    padding:0 2pt;border-radius:0}
  pre{background:#f4f4f4;border:.5pt solid #999;border-radius:0;padding:5pt 7pt;margin:0 0 7pt;
    page-break-inside:avoid}
  pre code{font-size:8.5pt;line-height:1.25}
  table{font-size:9pt;margin:0 0 8pt;border:.5pt solid #999;border-radius:0}
  /* Rows, not the whole table, hold together -- a table breaking between rows paginates the way
     a printed table normally does; forcing the entire table to one page is what was bouncing this
     six-row reference list wholesale onto a page of its own with a few words for company. */
  tr{page-break-inside:avoid}
  th,td{padding:1pt 6pt;border-bottom:.5pt solid #ccc}
  th{background:#eaeaea;font-size:8pt;letter-spacing:.02em}
  td{color:#000}
  a{color:#000;text-decoration:underline}
  ul,ol{margin:0 0 6pt;padding-left:15pt;color:#000}
  li{margin-bottom:2pt}
  hr{border-top:.5pt solid #999;margin:6pt 0}
  .page-break{page-break-before:always}
}
"""


REPO_BLOB = "https://github.com/adityamhaske/computer-use-automation/blob/main/"
REPO_TREE = "https://github.com/adityamhaske/computer-use-automation/tree/main/"


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


LANDING = ROOT / "site/index.html"


def _shared_chrome() -> tuple[str, str]:
    """The site's navigation bar and footer, lifted from the landing page at build time.

    Read rather than copied. These three pages had drifted into three different headers, and a
    second copy of the markup living in this script would simply restart that drift -- silently,
    because nothing would fail when the landing page changed. Parsing it here means the write-up
    cannot disagree with the rest of the site: there is one copy, and this is not it.

    Only the link depth and the current-page marker differ, which is what the rewriting below is
    for -- these pages sit one directory down.
    """
    html = LANDING.read_text(encoding="utf-8")
    nav = re.search(r"[ \t]*<nav>.*?</nav>", html, re.S)
    footer = re.search(r"[ \t]*<footer>.*?</footer>", html, re.S)
    if nav is None or footer is None:  # pragma: no cover - the landing page is in the repository
        raise SystemExit("site/index.html has no <nav>/<footer> to share")

    nav_html = nav.group(0)
    for href in ("index.html", "docs/index.html", "report/index.html", "walkthrough/index.html"):
        nav_html = nav_html.replace(f'href="{href}"', f'href="../{href}"')
    nav_html = nav_html.replace(
        '<a href="../index.html" class="nav-link active">Home</a>',
        '<a href="../index.html" class="nav-link">Home</a>',
    ).replace(
        '<a href="../report/index.html" class="nav-link">Design</a>',
        '<a href="../report/index.html" class="nav-link active">Design</a>',
    )

    footer_html = footer.group(0)
    footer_html = footer_html.replace('href="report/index.html"', 'href="index.html"')
    for href in ("docs/index.html", "llms.txt", "llms-full.txt"):
        footer_html = footer_html.replace(f'href="{href}"', f'href="../{href}"')
    return nav_html, footer_html


def _absolutise_markdown(md_text: str) -> str:
    """The same repository-relative -> GitHub rewrite as `_absolutise`, for Markdown link syntax."""

    def repoint(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        base = REPO_TREE if href.endswith("/") else REPO_BLOB
        return f"[{label}]({base}{href})"

    return re.sub(r"\[([^\]]*)\]\(([^)]+)\)", repoint, md_text)


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
    nav_html, footer_html = _shared_chrome()
    head = "" if for_print else nav_html
    downloads = (
        ""
        if for_print
        else """<div class="downloads">
  <a class="btn btn-primary" href="REPORT.pdf" download><span>Download PDF</span></a>
  <a class="btn btn-secondary" href="REPORT.md" download><span>Download Markdown</span></a>
  <a class="btn btn-github" href="https://github.com/adityamhaske/computer-use-automation/blob/main/REPORT.md"
    target="_blank" rel="noopener"><span>View on GitHub</span></a>
</div>"""
    )
    steps = "" if for_print else f'<nav class="steps">{nav}</nav>'
    pager = "" if for_print else f'<div class="pager">{prev}{nxt}</div>'
    footer = "" if for_print else footer_html
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — CUA design write-up</title>
<meta name="description" content="Design write-up for the CUA computer-use automation system.">
<link rel="icon" href="../assets/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="../assets/site.css">
<style>{STYLE}</style></head><body>
{head}<div class="wrap">{steps}{downloads}{inner}{pager}</div>{footer}
<script src="../assets/theme.js"></script></body></html>"""


def main() -> int:
    preamble, sections = _split_sections(SOURCE.read_text(encoding="utf-8"))
    missing = [n for _, nums in PAGES for n in nums if n not in sections]
    if missing:
        print(f"REPORT.md is missing section(s) {missing}; expected 1-7", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    # Not a straight copy. REPORT.md's links are written relative to the repository root, which
    # is right where it lives and wrong the moment someone downloads it from the site: every
    # `evidence/` and `config/` link in the downloaded file pointed at nothing. The published
    # copy gets the same absolutising the HTML pages get.
    OUT.joinpath("REPORT.md").write_text(
        _absolutise_markdown(SOURCE.read_text(encoding="utf-8")), encoding="utf-8"
    )

    for index, (title, numbers) in enumerate(PAGES):
        body = "".join(sections[n] for n in numbers)
        inner = (_render(preamble) if index == 0 else f"<h1>{title}</h1>") + _render(body)
        name = "index.html" if index == 0 else f"page-{index + 1}.html"
        (OUT / name).write_text(_chrome(index, title, inner), encoding="utf-8")
        print(f"  site/report/{name}")

    # No forced break between the three web-page groupings here: a fixed break at a content
    # boundary that does not line up with how much text actually fits leaves whatever remains of
    # the page nearly empty -- found by counting words per rendered PDF page and seeing one carry
    # nine lines. Letting Chromium paginate on the real content, with only the CSS break-avoidance
    # rules above (do not orphan a heading, do not split a table or code block), uses the page.
    whole = _render(preamble) + _render(
        "".join(sections[n] for _, nums in PAGES for n in nums)
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
                # "Narrow" margins, in the sense a word processor's own narrow-margin preset
                # means it: 12mm ~= 0.47in, close to Word's 0.5in narrow preset.
                margin={"top": "9mm", "bottom": "9mm", "left": "11mm", "right": "11mm"},
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
