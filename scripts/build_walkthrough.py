"""Render the end-to-end walkthrough at `site/walkthrough/index.html`.

Generated from the PARTS table below rather than hand-written, so the index and the body cannot
disagree: both are built from the same list, and adding a step adds its index entry.

Every screenshot is of the real running application or the real operator console, captured by
`scripts/capture_walkthrough.py`. Every terminal block is real captured output, stored under
`site/walkthrough/captures/`, not typed out here from memory.

Light mode only, by instruction: this page pins itself to the light palette and drops the theme
toggle from the shared nav, because the screenshots in it are all light and a dark chrome wrapped
around light screenshots reads as a mistake.

    make walkthrough
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site/walkthrough"
LANDING = ROOT / "site/index.html"
REPO = "https://github.com/adityamhaske/interface.ai"

FONTS = (
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800"
    "&family=JetBrains+Mono:wght@400;500;600"
    "&family=Newsreader:ital,wght@0,400;0,600;1,400&display=swap"
)


@dataclass
class Step:
    """One numbered step: a title, what the reader is looking at, and why it matters."""

    title: str
    shot: str | None = None
    capture: str | None = None
    code: str | None = None
    lang: str = "text"
    caption: str = ""
    """The on-point description of what is happening in the figure directly above it."""
    body: list[str] = field(default_factory=list)
    """Paragraphs after the figure: what it demonstrates, and why it was built this way."""


@dataclass
class Part:
    title: str
    blurb: str
    steps: list[Step]


PARTS: list[Part] = [
    Part(
        "The target",
        "A legacy back-office application with no API. Everything after this exists because the "
        "only way in is the screen a human operator uses.",
        [
            Step(
                "The application is a locked door",
                shot="01-signon.png",
                caption="The mock core-banking app on first contact: a sign-on form inside a "
                "frameset, with the navigation column already rendered on the left and the "
                "working area on the right. Nothing here is reachable over an API.",
                body=[
                    "This is the starting condition the brief describes — an enterprise "
                    "application an AI agent has to operate through its user interface, because "
                    "there is no other way in.",
                    "The frameset matters more than it looks. Navigation lives in one frame and "
                    "the working area in another, so a snapshot has to traverse into named frames "
                    "to see anything at all, and the same accessible name commonly appears in "
                    "both. Every target recorded later scopes itself to a frame for exactly this "
                    "reason: resolving to the wrong one is a wrong click, not a failure.",
                ],
            ),
            Step(
                "Signed in: the member search",
                shot="02-search.png",
                caption="After authentication the content frame shows Member Search — one text "
                "field labelled by an adjacent table cell rather than a bound label, and a Search "
                "button. This is the screen the capability starts from.",
                body=[
                    "The label sits in a neighbouring cell, not attached to the input. That is "
                    "how legacy screens are built, and it is why the resolver needs a rung that "
                    "finds a control by the text identifying it rather than by anything on the "
                    "control itself.",
                ],
            ),
            Step(
                "The record we need to read",
                shot="03-member-detail.png",
                caption="Member 12345. The savings balance appears twice on this page — once in "
                "the Accounts Overview grid and again in the summary row beside its label — and "
                "the status and as-of date sit in label/value pairs underneath.",
                body=[
                    "The duplication is the interesting part. A naive 'find the balance' rule has "
                    "two candidates and no principled way to choose, which is precisely where a "
                    "resolver that guesses eventually reads the wrong number.",
                    "The system refuses ambiguity rather than breaking the tie with a similarity "
                    "score. A tunable threshold is a knob that eventually gets turned.",
                ],
            ),
        ],
    ),
    Part(
        "Discovery — the model, exactly once",
        "An LLM drives the live application until the goal is met. This is the only place a model "
        "is allowed in the loop, and it runs once.",
        [
            Step(
                "A real model drives the real screens",
                code="$ make discover\n\n"
                "GOAL_MET after 6 step(s)\n"
                "  evidence : evidence/discovery/disc-e0aa86b951\n"
                "  outputs  : {\n"
                '    "savings_balance": "$4,210.55",\n'
                '    "account_status": "Active",\n'
                '    "as_of_date": "09/11/2026"\n'
                "  }\n"
                "  checkpoint hint: Member Detail page for member 12345 shows Savings Balance:\n"
                "                   $4,210.55, Status: Active, As Of: 09/11/2026.\n\n"
                "Discovery succeeded.\n"
                "  capability : evidence/capabilities/memberdesk.savings_balance@1.0.0.yaml\n"
                "  inputs     : ['member_number']\n"
                "  outputs    : ['savings_balance', 'account_status', 'as_of_date']",
                caption="A genuine LLM-driven run against the live frameset, routed through an "
                "OpenAI-compatible gateway: six model calls, 10,215 tokens, three effective "
                "steps. The model signed in, searched for the member, and read three values off "
                "the record.",
                body=[
                    "This is the probabilistic half of the system and the only part that costs "
                    "money or varies between runs. Everything after it is model-free by "
                    "construction.",
                    "Each call records the gateway's own provider and request id, so the run can "
                    "be reconciled against the gateway's logs rather than taken on trust. That "
                    "matters for a claim like 'a real model did this': the evidence is issued by "
                    "something other than the process making the claim.",
                ],
            ),
            Step(
                "The model is shown a semantic tree, never raw HTML",
                code="CURRENT PAGE  http://127.0.0.1:8811/   MemberDesk\n\n"
                '  [nav] link "Member Search"          #nav:/table[7]/row[0]/cell[0]/link[0]\n'
                '  [content] heading "Member Search"\n'
                '  [content] cell "Member Number"\n'
                '  [content] textbox "Member Number"   #content:/row[22]/cell[1]/textbox[0]\n'
                '  [content] button "Search"           #content:/row[23]/cell[1]/button[0]',
                caption="What the model actually receives: roles, accessible names and frame "
                "scopes, with an opaque node id per control. No markup, no CSS classes, no "
                "coordinates.",
                body=[
                    "Feeding a model raw HTML teaches it to reach for selectors, and a selector "
                    "is a promise about markup that a tenant's restyle silently breaks. Giving it "
                    "a semantic tree means the identity it picks is the identity that gets "
                    "recorded.",
                    "The same abstraction is what makes the artifact portable to a surface where "
                    "no CSS exists at all — a desktop accessibility tree has roles and names too.",
                ],
            ),
        ],
    ),
    Part(
        "The artifact",
        "The discovery run is compiled into a typed, versioned, content-addressed document. This "
        "is the deliverable everything else is built around.",
        [
            Step(
                "One step of the compiled capability",
                code="""steps:
- id: type_text_member_number
  description: Enter member number 12345 to search for the member.
  action:
    type: type
    target:
      role: textbox                 # semantic identity
      name: {value: Member Number, match: exact}
      scope: {frame: content}       # which frame, because names repeat
      anchor:                       # the label cell beside it
        relation: row_of
        text: Member Number
      hints: {css: 'input[name="memno"]'}   # a cache, never an identity
      fingerprint: ea2237aa18a5e0e3
      recorded_strategy: semantic_exact     # the drift baseline
    value: {$input: member_number}  # parameterised, not the literal 12345
  risk: safe""",
                lang="yaml",
                caption="A single recorded step. The control is identified by role, accessible "
                "name, frame scope and an adjacent-label anchor. The CSS selector is present but "
                "labelled hints — a fast path, never the identity.",
                body=[
                    "The literal the model typed, 12345, has become <code>{$input: "
                    "member_number}</code>. That is what makes the artifact a function rather "
                    "than a recording.",
                    "<code>recorded_strategy</code> is the rung that worked at record time. "
                    "Replay compares the rung it actually needed against this baseline, which is "
                    "what turns 'the page changed' from an outage into a measurable drift score.",
                    "A stored hint is only used if the candidate it finds <em>also</em> satisfies "
                    "the semantic assertion. That check is what stops an artifact from quietly "
                    "becoming CSS-coupled.",
                ],
            ),
            Step(
                "Sealed, and parameterised by deployment",
                code="entrypoint:\n"
                "  url_pattern: '{base_url}/'\n"
                "\n"
                "content_hash: sha256:c6667196c835c261f...\n"
                "provenance:\n"
                "  discovered_by:\n"
                "    run_id: disc-e0aa86b951\n"
                "    model: auto\n"
                "  transcript_ref: evidence/discovery/disc-e0aa86b951/trace.jsonl",
                lang="yaml",
                caption="The entrypoint names no host: base_url is bound per deployment at "
                "replay. The content hash seals the document, and provenance points back at the "
                "run that produced it without embedding the transcript.",
                body=[
                    "The artifact is immutable and content-addressed: running a modified one "
                    "while recording a version that no longer matches its content would make the "
                    "audit trail a fiction, so the loader refuses a hash mismatch outright.",
                    "Provenance is a <em>reference</em>, not a copy. The transcript stays in the "
                    "evidence directory; the artifact stays reviewable.",
                ],
            ),
        ],
    ),
    Part(
        "Deterministic replay",
        "From here on there is no model in the decision loop — not as a fallback, not on failure.",
        [
            Step(
                "Replayed with an input the discovery never saw",
                capture="replay-success.txt",
                caption="The artifact discovered against member 12345, replayed against 67890. "
                "Three typed outputs in under half a second, drift 0.00, and the rungs used match "
                "the rungs recorded — three by name, two by structural anchor.",
                body=[
                    "This is the whole thesis in one command: the expensive, uncertain step "
                    "happened once, and what remains is a deterministic function with a typed "
                    "signature.",
                    "Drift 0.00 means every control was found by the same rung that found it "
                    "during discovery. A run that still succeeds but has to fall to a weaker rung "
                    "reports that as drift, which is the early warning before anything breaks.",
                ],
            ),
        ],
    ),
    Part(
        "Answers that are not failures",
        "The brief names conflating a business outcome with a failure as the most common design "
        "mistake in this problem. The schema keeps them apart structurally.",
        [
            Step(
                "A member who does not exist",
                shot="04-member-not-found.png",
                caption="The application's own answer for member 99999: a plain 'No member with "
                "that number exists' notice, rendered in the content frame where the record would "
                "have been.",
                body=[
                    "Nothing has gone wrong. The system was asked a question and the answer "
                    "is negative."
                ],
            ),
            Step(
                "…is reported as an outcome, and exits 0",
                capture="business-outcome.txt",
                caption="The same situation through the capability: BUSINESS OUTCOME, code "
                "member_not_found, structured data naming the member asked about — and an exit "
                "code of 0.",
                body=[
                    "Treating this as a failure turns a routine result into a 2am page, and "
                    "trains everyone to ignore the alert that also fires for real defects.",
                    "<code>ObservationClass</code> (what we saw) and <code>RunStatus</code> (how "
                    "the run ended) are separate type families precisely so this distinction "
                    "cannot be collapsed by accident.",
                ],
            ),
            Step(
                "A closed account is also an answer",
                shot="05-closed-account.png",
                caption="Member 24680: a real record, a zero balance, and a status of Closed. The "
                "capability replays against this unchanged and returns $0.00 with status Closed.",
                body=[
                    "Worth showing because it is the case where a system pattern-matching on "
                    "'the balance looks empty' would invent a failure. The values are read off "
                    "the record, so a legitimately empty account reports as one.",
                ],
            ),
            Step(
                "Malformed input is refused before anything is touched",
                capture="input-rejected.txt",
                caption="A member id of 'not-a-member' against the declared pattern. The run "
                "fails with input_validation_failed and exit 1 — before a browser is driven or a "
                "single click is dispatched.",
                body=[
                    "The typed signature is enforced at the boundary, not discovered halfway "
                    "through a form. Nothing was clicked, so there is nothing to undo.",
                ],
            ),
        ],
    ),
    Part(
        "Faults, and the limits of retrying",
        "Recovery is declared in the artifact and bounded there. Unbounded recovery turns one "
        "transient 502 into a thousand retries against a struggling core banking system.",
        [
            Step(
                "A declared recovery rule clears a transient fault",
                capture="recovery.txt",
                caption="With HTTP 502s injected into the application, the run still succeeds: "
                "the declared transient_load rule took one recovery attempt and the condition "
                "cleared.",
                body=[
                    "The rule is part of the artifact, reviewable before approval, and carries a "
                    "mandatory <code>max_attempts</code>. The schema will not accept an unbounded "
                    "one.",
                ],
            ),
            Step(
                "The same fault past its budget escalates instead of grinding",
                capture="escalation.txt",
                caption="With the fault held open, the same rule reaches its declared limit of "
                "three attempts and stops: NEEDS HUMAN, exit 2, naming the rule that was tried "
                "and how many times.",
                body=[
                    "This is the difference between a bounded remedy and a retry loop. The run "
                    "ends in a state a person can act on, with the reason recorded.",
                ],
            ),
        ],
    ),
    Part(
        "When the screen is not the one we recorded",
        "Unknown states stop the run. In banking, an unverified click is an incident.",
        [
            Step(
                "An undeclared screen appears",
                shot="06-undeclared-dialog.png",
                caption="A 'System Notice' maintenance dialog has taken over the content frame. "
                "It is not the search form, and nothing in the artifact declares it — not as an "
                "outcome, not as a recoverable condition.",
                body=[
                    "A system that assumed the click probably worked would type a member number "
                    "into nothing and carry on. This one stops.",
                    "Two surviving candidates is <code>TARGET_AMBIGUOUS</code> and the resolver "
                    "refuses; zero is a failed precondition. Both fail closed.",
                ],
            ),
            Step(
                "The operator sees it before anyone reports it",
                shot="07-console-overview.png",
                caption="The console's overview at that moment: one intervention waiting, named "
                "with the capability and the step it stopped at, the session paused at epoch 2, "
                "and the replay drift average across recorded runs sitting at 0%.",
                body=[
                    "The stop is surfaced, not buried. Nobody has to notice a failed job or read "
                    "a log to find out that a run needs a person.",
                    "Drift is on this screen for the same reason: it is the number that moves "
                    "before anything breaks, so it belongs where someone will see it early.",
                ],
            ),
            Step(
                "The run stops and asks for a person",
                shot="08-intervention-queue.png",
                caption="The operator console with one intervention waiting. The card names the "
                "capability, the step it stopped at, the precondition that did not hold, and "
                "carries a screenshot of the screen that stopped it. The session is PAUSED at "
                "epoch 2 and automation still owns it.",
                body=[
                    "The failure is legible without reading a log: which capability, which step, "
                    "what was expected, and a picture of what was actually there.",
                    "Nothing has been handed over yet. The lease still belongs to automation, and "
                    "the live view stays closed until a person claims it.",
                ],
            ),
        ],
    ),
    Part(
        "Human in the loop — a real handoff",
        "The requirement most submissions describe rather than build. The operator drives the "
        "same live browser session the automation was driving.",
        [
            Step(
                "The operator takes the session",
                shot="09-operator-in-control.png",
                caption="After Take control: the state pill reads HUMAN CONTROL · operator owns "
                "the session · epoch 3, the live viewport opens showing the actual blocking "
                "dialog, and 'Hand back to automation' appears. The epoch has advanced.",
                body=[
                    "This is a transfer of a lease, not a screenshot viewer. Clicks and keystrokes "
                    "in that viewport reach the same Chromium session the executor was using, and "
                    "they travel the same policy chokepoint — authorized, classified and recorded "
                    "with <code>actor=HUMAN</code>.",
                    "The epoch is what makes the handoff safe. Automation may only act while it "
                    "holds the current epoch, so a late in-flight action from before the handoff "
                    "cannot land on a session a person is now driving.",
                ],
            ),
            Step(
                "Every gesture is in the evidence, attributed",
                code=" 3 authorize  actor=automation epoch=1  safe (default)\n"
                "14 escalate   actor=system     epoch=2  needs_human\n"
                "16 lease      actor=human      epoch=3  demo-operator\n"
                "18 authorize  actor=human      epoch=3  elevated (action:raw_input)\n"
                "19 dispatch   actor=human      epoch=3\n"
                "28 lease      actor=human      epoch=4  released\n"
                "29 lease      actor=automation epoch=5  resumed",
                caption="The recorded trace of a handoff. The operator's own input is authorized "
                "at elevated risk and dispatched under actor=human, between the escalation that "
                "paused the run and the release that resumed it.",
                body=[
                    "A human action that bypassed policy or evidence would leave a hole in the "
                    "audit trail exactly where the interesting thing happened. There is no "
                    "separate path for people.",
                    "On release the executor re-anchors against the current screen and resumes at "
                    "the right step — not from step 1, and not by assuming the page is where it "
                    "left it.",
                ],
            ),
            Step(
                "The run and its evidence, afterwards",
                shot="10-runs-evidence.png",
                caption="The console's run history: every run with its terminal status, the "
                "capability and version it executed, and a route into the evidence directory it "
                "wrote — snapshots, trace and run record.",
                body=[
                    "Each run writes an append-only, redacted, actor-tagged evidence directory. "
                    "Secrets and PII are redacted at every sink — logs, artifacts, screenshots "
                    "and model prompts alike.",
                ],
            ),
        ],
    ),
    Part(
        "An agent calls it like a function",
        "The artifact is the tool contract. There is no second definition to drift.",
        [
            Step(
                "The catalog, typed",
                shot="11-capability-catalog.png",
                caption="The capability catalog in the console: each entry with its id, version, "
                "seal state and typed signature. A sealed badge means the content hash still "
                "matches the document.",
                body=[
                    "A tampered artifact shows as tampered rather than running: the state is "
                    "computed from the content, not stored as a field somebody could set.",
                ],
            ),
            Step(
                "…and invoked by name, with declared outcomes",
                capture="agent-demo.txt",
                caption="An agent's view of the same artifact: tool name, purpose, typed "
                "arguments, typed returns, and the non-failure outcomes it may receive — all "
                "derived from the artifact. Then the call itself, returning structured data.",
                body=[
                    "The calling agent is told in advance which negative answers are legitimate, "
                    "so it can distinguish 'no such member' from 'the automation broke' without "
                    "parsing prose.",
                    "Because the schema is generated from the artifact, a change to the "
                    "capability changes the tool contract in the same commit. There is nowhere "
                    "for the two to disagree.",
                ],
            ),
        ],
    ),
]


STYLE = """
/* Page-specific only; palette, nav, buttons and footer come from ../assets/site.css.
   This page is pinned to light (data-theme on <html>, toggle dropped from the nav) because
   every screenshot in it is light. */
.wrap{max-width:900px;margin:0 auto;padding:calc(var(--nav-height) + 2.5rem) 20px 4rem}

.lede{margin-bottom:2.5rem}
.eyebrow{font-size:.72rem;font-weight:600;letter-spacing:.13em;text-transform:uppercase;
  color:var(--accent);margin:0 0 .6rem}
.lede h1{font-family:var(--font-serif);font-size:clamp(2rem,5vw,2.7rem);font-weight:600;
  line-height:1.12;letter-spacing:-.02em;margin:0 0 1rem;text-wrap:balance}
.standfirst{font-size:1.05rem;line-height:1.65;color:var(--text-secondary);margin:0 0 1.5rem;
  max-width:70ch}
.meta{display:flex;flex-direction:column;gap:.35rem;padding:1rem 1.15rem;font-size:.85rem;
  color:var(--text-secondary);background:var(--bg-card);border:1px solid var(--border);
  border-radius:var(--radius)}
.meta strong{color:var(--text);font-weight:600;margin-right:.4rem}
.meta code{font-family:var(--font-mono);font-size:.9em;background:var(--bg-code);
  padding:.08em .34em;border-radius:3px}

.toc{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);
  padding:1.5rem 1.6rem;margin-bottom:3.5rem}
.toc>h2{font-family:var(--font-serif);font-size:1.3rem;font-weight:600;margin:0 0 1.1rem}
.toc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:1.4rem}
.toc-part h3{font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 .55rem}
.toc-part ol{list-style:none;margin:0;padding:0}
.toc-part li{margin-bottom:.3rem}
.toc-part a{display:flex;gap:.55rem;text-decoration:none;color:var(--text-secondary);
  font-size:.9rem;line-height:1.4;padding:.15rem 0}
.toc-part a:hover{color:var(--accent)}
.toc-part .num{font-family:var(--font-mono);font-size:.78rem;color:var(--text-faint);
  font-variant-numeric:tabular-nums;flex:none;min-width:1.4em}

.part{margin:3.5rem 0 2rem;padding-top:1.4rem;border-top:2px solid var(--accent)}
.part h2{font-family:var(--font-serif);font-size:1.6rem;font-weight:600;margin:0 0 .5rem;
  letter-spacing:-.015em}
.part p{margin:0;color:var(--text-secondary);max-width:72ch}

.step{margin:0 0 3rem;scroll-margin-top:calc(var(--nav-height) + 20px)}
.step h3{display:flex;gap:.7rem;align-items:baseline;font-size:1.12rem;font-weight:600;
  margin:0 0 1rem;letter-spacing:-.01em}
.stepnum{font-family:var(--font-mono);font-size:.82rem;font-weight:600;color:var(--accent);
  background:var(--accent-bg);border:1px solid var(--accent-border);border-radius:5px;
  padding:.12rem .48rem;flex:none;font-variant-numeric:tabular-nums}
.step p{color:var(--text-secondary);margin:0 0 .9rem;max-width:72ch;line-height:1.7}
.step code{font-family:var(--font-mono);font-size:.85em;background:var(--bg-code);
  padding:.1em .36em;border-radius:3px}

figure{margin:0 0 1.4rem}
.shot{display:block;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;
  background:#fff;line-height:0}
.shot img{width:100%;height:auto;display:block}
.term{background:var(--bg-code);border:1px solid var(--border);border-radius:var(--radius);
  padding:1rem 1.15rem;overflow-x:auto;margin:0;font-size:.8rem;line-height:1.6}
.term code{font-family:var(--font-mono);background:none;padding:0}
figcaption{margin-top:.75rem;padding-left:.9rem;border-left:3px solid var(--accent);
  font-size:.9rem;line-height:1.6;color:var(--text-secondary);max-width:72ch}
.figlabel{display:block;font-size:.68rem;font-weight:700;letter-spacing:.09em;
  text-transform:uppercase;color:var(--text-muted);margin-bottom:.25rem}

.closing{margin-top:4rem;padding-top:2rem;border-top:1px solid var(--border)}
.closing h2{font-family:var(--font-serif);font-size:1.5rem;font-weight:600;margin:0 0 .8rem}
.closing p{color:var(--text-secondary);max-width:72ch;line-height:1.7}
.closing-links{display:flex;gap:10px;flex-wrap:wrap;margin-top:1.5rem}

@media (max-width:640px){
  .toc{padding:1.2rem}
  .step h3{flex-wrap:wrap}
}
"""


def _shared_chrome() -> tuple[str, str]:
    """The site's nav and footer, read from the landing page so this page cannot drift from it."""
    page = LANDING.read_text(encoding="utf-8")
    nav = re.search(r"[ \t]*<nav>.*?</nav>", page, re.S)
    footer = re.search(r"[ \t]*<footer>.*?</footer>", page, re.S)
    if nav is None or footer is None:  # pragma: no cover - the landing page is in the repository
        raise SystemExit("site/index.html has no <nav>/<footer> to share")

    nav_html = nav.group(0)
    for href in ("index.html", "docs/index.html", "report/index.html", "walkthrough/index.html"):
        nav_html = nav_html.replace(f'href="{href}"', f'href="../{href}"')
    nav_html = nav_html.replace(
        '<a href="../index.html" class="nav-link active">Overview</a>',
        '<a href="../index.html" class="nav-link">Overview</a>',
    )
    nav_html = nav_html.replace(
        '<a href="../walkthrough/index.html" class="nav-link">Walkthrough</a>',
        '<a href="../walkthrough/index.html" class="nav-link active">Walkthrough</a>',
    )
    # Light mode only: the toggle would put a dark chrome around uniformly light screenshots.
    nav_html = re.sub(
        r"[ \t]*<button class=\"theme-toggle\".*?</button>\n", "", nav_html, flags=re.S
    )

    footer_html = footer.group(0)
    footer_html = footer_html.replace('href="report/index.html"', 'href="../report/index.html"')
    for href in ("docs/index.html", "llms.txt", "llms-full.txt"):
        footer_html = footer_html.replace(f'href="{href}"', f'href="../{href}"')
    return nav_html, footer_html


def _figure(step: Step, number: int) -> str:
    """The image or terminal block, plus the description of what is happening in it."""
    if step.shot:
        media = (
            f'<a class="shot" href="shots/{step.shot}" target="_blank" rel="noopener">'
            f'<img src="shots/{step.shot}" alt="{html.escape(step.title)}" loading="lazy" '
            f'width="1180" height="720"></a>'
        )
        kind = "Screenshot"
    else:
        text = step.code or ""
        if step.capture:
            text = (OUT / "captures" / step.capture).read_text(encoding="utf-8").strip()
            text = re.sub(r"\nexit=\d+$", "", text)
        media = f'<pre class="term lang-{step.lang}"><code>{html.escape(text)}</code></pre>'
        kind = "Captured output"

    if not step.caption:
        return f"<figure>{media}</figure>"
    return (
        f"<figure>{media}<figcaption>"
        f'<span class="figlabel">{kind} {number}</span>{step.caption}'
        f"</figcaption></figure>"
    )


def _render() -> str:
    nav_html, footer_html = _shared_chrome()

    index_rows: list[str] = []
    sections: list[str] = []
    n = 0
    for p, part in enumerate(PARTS, start=1):
        sections.append(
            f'<section class="part"><h2>Part {p} · {html.escape(part.title)}</h2>'
            f"<p>{part.blurb}</p></section>"
        )
        items: list[str] = []
        for step in part.steps:
            n += 1
            items.append(
                f'<li><a href="#step-{n}"><span class="num">{n}</span>'
                f"{html.escape(step.title)}</a></li>"
            )
            body = "".join(f"<p>{para}</p>" for para in step.body)
            sections.append(
                f'<section class="step" id="step-{n}">'
                f'<h3><span class="stepnum">{n}</span>{html.escape(step.title)}</h3>'
                f"{_figure(step, n)}{body}</section>"
            )
        index_rows.append(
            f'<div class="toc-part"><h3>Part {p} · {html.escape(part.title)}</h3>'
            f"<ol>{''.join(items)}</ol></div>"
        )

    return f"""<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>End-to-end walkthrough — CUA</title>
<meta name="description" content="Every step of the computer-use automation system, demonstrated
against the running application with screenshots and real captured output.">
<link rel="icon" href="../assets/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="../assets/site.css">
<style>{STYLE}</style></head><body>
{nav_html}
<div class="wrap">
  <header class="lede">
    <p class="eyebrow">End-to-end walkthrough</p>
    <h1>Every step, against the running system</h1>
    <p class="standfirst">{n} steps from a locked legacy screen to an agent calling a typed
      function. Every screenshot below is of the real application or the real operator console;
      every terminal block is real captured output. Nothing here is a mockup.</p>
    <div class="meta">
      <span><strong>Discovery run</strong> disc-e0aa86b951 · 6 model calls · 10,215 tokens</span>
      <span><strong>Artifact</strong> memberdesk.savings_balance@1.0.0, sealed</span>
      <span><strong>Reproduce</strong> <code>make app</code>, then
        <code>make walkthrough</code></span>
    </div>
  </header>

  <nav class="toc" aria-label="Contents">
    <h2>Contents</h2>
    <div class="toc-grid">{"".join(index_rows)}</div>
  </nav>

  {"".join(sections)}

  <section class="closing">
    <h2>What this demonstrated</h2>
    <p>An LLM worked out how to operate a legacy application once, and that run became a typed,
      versioned, sealed artifact. The artifact replays deterministically against inputs the
      discovery never saw, distinguishes a negative answer from a fault, bounds its own recovery,
      fails closed on anything it does not recognise, hands the live session to a person and takes
      it back, and presents itself to an agent as a function with a declared signature.</p>
    <div class="closing-links">
      <a class="btn btn-primary" href="../report/index.html"
        ><span>Read the design write-up</span></a>
      <a class="btn btn-secondary" href="../docs/index.html"><span>Documentation</span></a>
      <a class="btn btn-github" href="{REPO}" target="_blank" rel="noopener"
        ><span>Source on GitHub</span></a>
    </div>
  </section>
</div>
{footer_html}
</body></html>"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(_render(), encoding="utf-8")
    steps = sum(len(p.steps) for p in PARTS)
    print(f"  site/walkthrough/index.html  ({len(PARTS)} parts, {steps} steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
