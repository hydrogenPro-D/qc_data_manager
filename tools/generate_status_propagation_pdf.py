"""Builds docs/status-propagation.pdf: the propagation rules, with drawings.

Emits a self-contained HTML page with inline SVG, then prints it through
headless Chrome or Edge. Run it again after editing the scenarios below.
"""

from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_HTML = ROOT / "docs" / "status-propagation.html"
OUT_PDF = ROOT / "docs" / "status-propagation.pdf"

TEAL = "#14B8A6"
INK = "#1F2933"
MUTED = "#6B7280"

# One palette for every drawing and every legend swatch: label, ink, fill, edge.
STATUS = {
    "passed": ("Tested, passed", "#15803D", "#DCFCE7", "#15803D"),
    "failed": ("Tested, failed", "#B91C1C", "#FEE2E2", "#B91C1C"),
    "rejected": ("Rejected", "#166534", "#E3F5E8", "#5FA87B"),
    "scrapped": ("Scrapped", "#9A3412", "#FDECE2", "#F0A981"),
    "needs": ("Needs testing", "#92400E", "#FEF3C7", "#E3B341"),
    "none": ("Not Tested", "#6B7280", "#F3F4F6", "#D8DCE1"),
    "ongoing": ("Test ongoing", "#0F766E", "#D7F5F1", TEAL),
}

ROW_H, ROW_GAP = 30.0, 5.0
X_MARK, X_BAR, W_BAR, X_NOTE = 0.0, 74.0, 250.0, 338.0
X_BAND, X_BAND_LABEL, CANVAS_W = 520.0, 540.0, 700.0


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _lines(x: float, y: float, css: str, text: str, step: float = 15.0) -> str:
    """Several tspan-free lines, so the caption wraps where we want it to."""
    out = []
    for index, line in enumerate(text.split("|")):
        out.append(
            f'<text x="{x}" y="{y + index * step:.1f}" class="{css}">{_esc(line)}</text>'
        )
    return "".join(out)


def ladder(rows: list[dict], bands: list[dict] | None = None) -> str:
    """A column of electrodes. Row keys: marking, status, note, flag, set, vat."""
    bands = bands or []
    height = len(rows) * (ROW_H + ROW_GAP) + 6
    parts = [
        f'<svg class="fig" viewBox="0 0 {CANVAS_W:.0f} {height:.0f}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    ]
    for index, row in enumerate(rows):
        y = index * (ROW_H + ROW_GAP)
        label, ink, fill, edge = STATUS[row["status"]]
        set_here = row.get("set")
        parts.append(
            f'<text x="{X_MARK}" y="{y + 20:.1f}" class="mk">{_esc(row["marking"])}</text>'
        )
        if row.get("vat"):
            parts.append(
                f'<text x="47" y="{y + 20:.1f}" class="vat">{_esc(row["vat"])}</text>'
            )
        parts.append(
            f'<rect x="{X_BAR}" y="{y:.1f}" width="{W_BAR}" height="{ROW_H}" rx="7" '
            f'fill="{fill}" stroke="{edge}" stroke-width="{2.2 if set_here else 1.0}"/>'
            f'<text x="{X_BAR + 12}" y="{y + 20:.1f}" class="st" fill="{ink}">'
            f"{_esc(label)}</text>"
        )
        if row.get("flag"):
            parts.append(
                f'<rect x="{X_BAR + W_BAR - 76}" y="{y + 6:.1f}" width="66" height="18" '
                f'rx="9" fill="#FFFFFF" stroke="{ink}" stroke-width="0.9"/>'
                f'<text x="{X_BAR + W_BAR - 43}" y="{y + 19:.1f}" class="fl" '
                f'fill="{ink}">test first</text>'
            )
        note = row.get("note", "")
        if set_here:
            parts.append(
                f'<path d="M {X_BAR + W_BAR + 8} {y + ROW_H / 2:.1f} l 11 -6 v 12 z" '
                f'fill="{INK}"/>'
            )
            note = note or "you set this"
        if note:
            parts.append(
                f'<text x="{X_NOTE}" y="{y + 20:.1f}" '
                f'class="{"nt strong" if set_here else "nt"}">{_esc(note)}</text>'
            )
    for band in bands:
        top = band["from"] * (ROW_H + ROW_GAP)
        bottom = band["to"] * (ROW_H + ROW_GAP) + ROW_H
        mid = (top + bottom) / 2
        color = band.get("color", TEAL)
        label = band["label"]
        first = mid + 4 - (len(label.split("|")) - 1) * 6.5
        parts.append(
            f'<path d="M {X_BAND + 8} {top:.1f} h -8 V {bottom:.1f} h 8" fill="none" '
            f'stroke="{color}" stroke-width="1.6"/>'
            f'<path d="M {X_BAND} {mid:.1f} h 12" stroke="{color}" stroke-width="1.6"/>'
        )
        for index, line in enumerate(label.split("|")):
            parts.append(
                f'<text x="{X_BAND_LABEL}" y="{first + index * 13:.1f}" class="bd" '
                f'fill="{color}">{_esc(line)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def vat_diagram() -> str:
    """The line runs left to right, the vats are the two lanes underneath."""
    markings = ["B14462", "B14463", "B14464", "B14465", "B14466", "B14467", "B14468"]
    vats = ["28", "30", "28", "30", "28", "30", "28"]
    box_w, gap, left, top = 80.0, 12.0, 44.0, 34.0
    lane_y = {"28": 122.0, "30": 178.0}
    parts = [
        '<svg class="fig" viewBox="0 0 700 236" xmlns="http://www.w3.org/2000/svg">',
        '<text x="0" y="16" class="ax">the production line, in order</text>',
    ]
    for index, (marking, vat) in enumerate(zip(markings, vats)):
        x = left + index * (box_w + gap)
        failed = marking == "B14466"
        hit = vat == "28"
        fill = STATUS["failed"][2] if failed else "#FFFFFF"
        edge = STATUS["failed"][3] if failed else "#CBD3DA"
        dot = "#B91C1C" if failed else ("#F0A981" if hit else "#E3E7EB")
        y = lane_y[vat]
        parts.append(
            f'<rect x="{x}" y="{top}" width="{box_w}" height="28" rx="7" fill="{fill}" '
            f'stroke="{edge}" stroke-width="{2.0 if failed else 1.0}"/>'
            f'<text x="{x + box_w / 2}" y="{top + 19}" class="mkc">{marking}</text>'
            f'<path d="M {x + box_w / 2} {top + 30} V {y - 11}" stroke="#9AA5B1" '
            f'stroke-width="1" stroke-dasharray="3 3"/>'
            f'<circle cx="{x + box_w / 2}" cy="{y}" r="9" fill="{dot}" '
            f'stroke="{"#B91C1C" if failed else "#FFFFFF"}" stroke-width="1.5"/>'
        )
    for vat, y in lane_y.items():
        parts.append(
            f'<path d="M 30 {y} H 672" stroke="#CBD3DA" stroke-width="1.2"/>'
            f'<text x="0" y="{y - 15}" class="ax">vat {vat}</text>'
        )
    parts.append(
        _lines(
            30,
            214,
            "cap",
            "B14466 fails. The doubt travels along the vat 28 lane only, so B14464 and "
            "B14468 are|scrapped while B14465 and B14467, its neighbours on the line, "
            "are left alone.",
        )
    )
    parts.append("</svg>")
    return "".join(parts)


def direction_diagram() -> str:
    """Scenario 1 in one picture: a pass is not symmetric."""
    return (
        '<svg class="fig" viewBox="0 0 700 172" xmlns="http://www.w3.org/2000/svg">'
        "<defs>"
        '<marker id="ar-green" markerWidth="9" markerHeight="9" refX="8" refY="4.5" '
        'orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#166534"/></marker>'
        '<marker id="ar-amber" markerWidth="9" markerHeight="9" refX="8" refY="4.5" '
        'orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#92400E"/></marker>'
        "</defs>"
        '<rect x="252" y="62" width="196" height="34" rx="8" fill="#DCFCE7" '
        'stroke="#15803D" stroke-width="2"/>'
        '<text x="350" y="84" class="mkc" fill="#15803D">Tested, passed</text>'
        '<path d="M 246 79 H 40" stroke="#166534" stroke-width="2" '
        'marker-end="url(#ar-green)"/>'
        '<path d="M 454 79 H 660" stroke="#92400E" stroke-width="2" '
        'marker-end="url(#ar-amber)"/>'
        '<text x="40" y="40" class="hd" fill="#166534">behind it, it rejects</text>'
        + _lines(
            40,
            122,
            "cap",
            "the vat was plating properly right up to|"
            "here, so everything untested behind it|becomes Rejected",
        )
        + '<text x="660" y="40" class="hd end" fill="#92400E">'
        "ahead of it, it only clears doubt</text>"
        + "".join(
            f'<text x="660" y="{122 + i * 15}" class="cap end">{_esc(line)}</text>'
            for i, line in enumerate(
                [
                    "a test at 09:00 says nothing about what",
                    "the vat did at 11:00, so doubt is lifted",
                    "but nothing is vouched for",
                ]
            )
        )
        + "</svg>"
    )


def retest_diagram() -> str:
    """Scenario 4: a guess older than the verdict it names is thrown away."""

    def card(x: int, title: str, lines: list[str], key: str) -> str:
        _, ink, fill, edge = STATUS[key]
        body = "".join(
            f'<text x="{x + 14}" y="{56 + i * 15}" class="cap">{_esc(line)}</text>'
            for i, line in enumerate(lines)
        )
        return (
            f'<rect x="{x}" y="16" width="196" height="{44 + len(lines) * 15}" rx="10" '
            f'fill="{fill}" stroke="{edge}" stroke-width="1.4"/>'
            f'<text x="{x + 14}" y="{38}" class="hd" fill="{ink}">{_esc(title)}</text>'
            + body
        )

    arrow = (
        '<path d="M {a} 62 H {b}" stroke="#52606D" stroke-width="1.6" '
        'marker-end="url(#ar-ink)"/>'
    )
    return (
        '<svg class="fig" viewBox="0 0 700 128" xmlns="http://www.w3.org/2000/svg">'
        '<defs><marker id="ar-ink" markerWidth="9" markerHeight="9" refX="8" refY="4.5" '
        'orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#52606D"/></marker></defs>'
        + card(
            0,
            "B14466 fails",
            ["its neighbours are scrapped,", "each stamped: because of", "B14466, at 09:14"],
            "failed",
        )
        + arrow.format(a=204, b=246)
        + card(252, "you re-test it", ["a new verdict, passed,", "stamped 14:02"], "passed")
        + arrow.format(a=456, b=498)
        + card(
            504,
            "the old guesses drop",
            ["stamped 09:14, older than", "the verdict they name,", "so they are re-derived"],
            "rejected",
        )
        + "</svg>"
    )


def legend() -> str:
    chips = []
    for key in ("passed", "failed", "rejected", "scrapped", "needs", "none", "ongoing"):
        label, ink, fill, edge = STATUS[key]
        chips.append(
            f'<span class="chip" style="background:{fill};border-color:{edge};'
            f'color:{ink}">{label}</span>'
        )
    return '<div class="legend">' + "".join(chips) + "</div>"


# --- the scenarios, every one of them straight out of the rules -----------

S1A = ladder(
    [
        {"marking": "B14426", "status": "none"},
        {"marking": "B14425", "status": "passed", "set": True},
        {"marking": "B14424", "status": "rejected"},
        {"marking": "B14423", "status": "rejected"},
        {"marking": "B14422", "status": "rejected", "note": "and so on backwards"},
    ],
    [
        {
            "from": 2,
            "to": 4,
            "label": "back until it meets|an electrode that|already has a status",
            "color": "#166534",
        }
    ],
)

S1B = ladder(
    [
        {"marking": "B14425", "status": "passed", "set": True},
        {"marking": "B14424", "status": "needs"},
        {"marking": "B14423", "status": "needs"},
        {"marking": "B14422", "status": "needs", "flag": True},
        {"marking": "B14421", "status": "scrapped"},
        {"marking": "B14420", "status": "scrapped"},
        {"marking": "B14419", "status": "failed"},
    ],
    [
        {
            "from": 1,
            "to": 3,
            "label": "it recovered by B14425,|but nobody knows where,|so each one is a real question",
            "color": "#92400E",
        }
    ],
)

S1C = ladder(
    [
        {"marking": "B14428", "status": "none", "note": "was needs testing"},
        {"marking": "B14427", "status": "none", "note": "was needs testing"},
        {"marking": "B14426", "status": "none", "note": "was needs testing"},
        {"marking": "B14425", "status": "passed", "set": True},
        {"marking": "B14424", "status": "rejected"},
    ],
    [
        {
            "from": 0,
            "to": 2,
            "label": "doubt cleared,|not vouched for",
            "color": "#52606D",
        }
    ],
)

S1D = ladder(
    [
        {"marking": "B14436", "status": "needs", "flag": True},
        {"marking": "B14435", "status": "scrapped"},
        {"marking": "B14434", "status": "scrapped"},
        {"marking": "B14433", "status": "failed", "note": "a fault ahead"},
        {"marking": "B14432", "status": "scrapped"},
        {"marking": "B14431", "status": "scrapped"},
        {"marking": "B14430", "status": "needs", "flag": True},
        {"marking": "B14429", "status": "needs"},
        {"marking": "B14428", "status": "needs"},
        {"marking": "B14427", "status": "needs"},
        {"marking": "B14426", "status": "needs"},
        {"marking": "B14425", "status": "passed", "set": True},
        {"marking": "B14424", "status": "rejected"},
    ],
    [
        {
            "from": 6,
            "to": 10,
            "label": "nothing here is cleared:|the later fault has a|real claim on them",
            "color": "#92400E",
        }
    ],
)

S2 = ladder(
    [
        {"marking": "B14430", "status": "needs"},
        {"marking": "B14429", "status": "needs"},
        {"marking": "B14428", "status": "needs", "flag": True, "note": "third above"},
        {"marking": "B14427", "status": "scrapped"},
        {"marking": "B14426", "status": "scrapped"},
        {"marking": "B14425", "status": "failed", "set": True},
        {"marking": "B14424", "status": "scrapped"},
        {"marking": "B14423", "status": "scrapped"},
        {"marking": "B14422", "status": "needs", "flag": True, "note": "third below"},
        {"marking": "B14421", "status": "needs"},
        {"marking": "B14420", "status": "needs"},
    ],
    [
        {"from": 0, "to": 1, "label": "outward until a pass", "color": "#92400E"},
        {"from": 3, "to": 7, "label": "the two either side|are scrapped with it", "color": "#9A3412"},
        {"from": 9, "to": 10, "label": "outward until a pass", "color": "#92400E"},
    ],
)

S2A = ladder(
    [
        {"marking": "B14429", "status": "none", "note": "the doubt never gets here"},
        {"marking": "B14428", "status": "passed", "note": "a wall"},
        {"marking": "B14427", "status": "scrapped"},
        {"marking": "B14426", "status": "scrapped"},
        {"marking": "B14425", "status": "failed", "set": True},
    ]
)

S2B = ladder(
    [
        {"marking": "B14470", "vat": "vat 28", "status": "scrapped"},
        {"marking": "B14469", "vat": "vat 30", "status": "none"},
        {"marking": "B14468", "vat": "vat 28", "status": "scrapped"},
        {"marking": "B14467", "vat": "vat 30", "status": "none"},
        {"marking": "B14466", "vat": "vat 28", "status": "failed", "set": True},
        {"marking": "B14465", "vat": "vat 30", "status": "none"},
        {"marking": "B14464", "vat": "vat 28", "status": "scrapped"},
        {"marking": "B14463", "vat": "vat 30", "status": "none"},
        {"marking": "B14462", "vat": "vat 28", "status": "scrapped"},
    ]
)

S3 = ladder(
    [
        {"marking": "B14422", "status": "needs", "flag": True},
        {"marking": "B14421", "status": "ongoing", "note": "was Rejected"},
        {"marking": "B14420", "status": "ongoing", "note": "was Scrapped"},
        {"marking": "B14419", "status": "needs"},
    ],
    [{"from": 1, "to": 2, "label": "both cards are on|the board right now"}],
)

S3A = ladder(
    [
        {"marking": "B14421", "status": "needs", "note": "reopened by the fault above"},
        {"marking": "B14420", "status": "scrapped", "note": "back to its own verdict"},
    ]
)

OVERWRITE_ROWS = [
    ("Not Tested", "yes", "nothing is known, so anything is an improvement"),
    ("Needs testing", "yes", "it is a request, not an answer"),
    ("Rejected", "yes", "nobody measured it"),
    ("Scrapped", "no, it keeps it, but the sweep carries on past", "a recorded verdict"),
    ("Tested, failed", "no, and the sweep carries on past", "a measurement"),
    ("Tested, passed", "no, and it stops the sweep dead", "the only wall"),
]


def build_html() -> str:
    overwrite = "".join(
        f"<tr><td><b>{_esc(a)}</b></td><td>{_esc(b)}</td><td>{_esc(c)}</td></tr>"
        for a, b, c in OVERWRITE_ROWS
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>How an electrode's status is decided</title>
<style>
  @page {{ size: A4; margin: 15mm 14mm 13mm 14mm; }}
  * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ font-family: "Segoe UI", Arial, sans-serif; color: {INK};
         font-size: 10.2pt; line-height: 1.5; margin: 0; }}
  h1 {{ font-size: 21pt; margin: 0 0 4px; letter-spacing: -0.3px; }}
  h2 {{ font-size: 13.5pt; margin: 24px 0 8px; padding-bottom: 5px;
        border-bottom: 2px solid {TEAL}; break-after: avoid; }}
  h3 {{ font-size: 11pt; margin: 18px 0 4px; color: #0F766E; break-after: avoid; }}
  p {{ margin: 6px 0; }}
  .sub {{ color: {MUTED}; font-size: 9.6pt; margin-bottom: 14px; }}
  .fig {{ width: 100%; height: auto; display: block; margin: 10px 0 14px;
          break-inside: avoid; }}
  svg text {{ font-family: "Segoe UI", Arial, sans-serif; }}
  .mk {{ font-size: 12px; font-weight: 600; fill: {INK}; }}
  .mkc {{ font-size: 12px; font-weight: 600; fill: {INK}; text-anchor: middle; }}
  .vat {{ font-size: 10px; fill: {MUTED}; }}
  .st {{ font-size: 12.5px; font-weight: 600; }}
  .fl {{ font-size: 9px; font-weight: 700; text-anchor: middle;
         text-transform: uppercase; letter-spacing: 0.4px; }}
  .nt {{ font-size: 11px; fill: {MUTED}; }}
  .nt.strong {{ fill: {INK}; font-weight: 700; }}
  .bd {{ font-size: 10.5px; }}
  .ax {{ font-size: 10.5px; fill: {MUTED}; font-weight: 700;
         text-transform: uppercase; letter-spacing: 0.6px; }}
  .cap {{ font-size: 11px; fill: {MUTED}; }}
  .hd {{ font-size: 12.5px; font-weight: 700; }}
  .end {{ text-anchor: end; }}
  .card {{ background: #F7FAFA; border: 1px solid #D6E4E3;
           border-left: 4px solid {TEAL}; border-radius: 8px; padding: 8px 14px;
           margin: 12px 0; break-inside: avoid; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 14px; }}
  .chip {{ border: 1.4px solid; border-radius: 20px; padding: 3px 12px;
           font-size: 9.4pt; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0 12px;
           font-size: 9.8pt; break-inside: avoid; }}
  th, td {{ text-align: left; padding: 6px 9px; border-bottom: 1px solid #E4E7EB;
            vertical-align: top; }}
  th {{ background: #F0FAF9; color: #0F766E; font-size: 8.8pt;
        text-transform: uppercase; letter-spacing: 0.5px; }}
  .page {{ break-before: page; }}
  .note {{ font-size: 9.2pt; color: {MUTED}; border-top: 1px solid #E4E7EB;
           padding-top: 8px; margin-top: 22px; }}
</style></head><body>

<h1>How an electrode's status is decided</h1>
<div class="sub">Testing one electrode says something about its neighbours.
QC Data Manager, the complete set of propagation rules.</div>

{legend()}

<div class="card">
<p><b>1. Everything travels along the vat, not along the line.</b> Electrodes are
plated in vats (28, 29, 30) and the line alternates between them, so B14466 and
B14464 are neighbours <i>in vat 28</i> while B14465 sits between them on the line
in a different vat. A fault is the vat's doing, so it spreads to the electrodes
that vat made, and skips over everything else.</p>
<p><b>2. A measurement always beats a guess.</b> Only two statuses are
measurements: Tested passed and Tested failed. Somebody put those electrodes on a
rig. Everything else is inferred from them, and an inference can always be
replaced by a better one.</p>
</div>

{vat_diagram()}

<h2>The six statuses</h2>
<table>
<tr><th>Status</th><th>Meaning</th><th>Measured?</th></tr>
<tr><td><b>Tested, passed</b></td><td>put on a rig, it was good</td><td>yes</td></tr>
<tr><td><b>Tested, failed</b></td><td>put on a rig, it was bad</td><td>yes</td></tr>
<tr><td><b>Rejected</b></td><td>presumed good, because of a pass nearby</td>
<td>no, a guess</td></tr>
<tr><td><b>Scrapped</b></td><td>presumed bad, because of a fault nearby</td>
<td>no, a guess</td></tr>
<tr><td><b>Needs testing</b></td><td>in doubt, somebody should test it</td>
<td>no, a request</td></tr>
<tr><td><b>Not Tested</b></td><td>nothing is known about it</td><td>no</td></tr>
</table>
<p>Plus one that is never stored: <b>Test ongoing</b>, which means the electrode
is on the QC board right now. It is worked out fresh every time the screen is
drawn.</p>

<div class="page"></div>
<h2>Scenario 1: you set an electrode to Tested, passed</h2>
<p>A pass does two different things in the two directions, and they are not
symmetric. <b>Behind it, it rejects. Ahead of it, it only clears doubt.</b></p>

{direction_diagram()}

<h3>1a. Behind it, nothing in the way</h3>
<p>Everything untested behind it in the same vat becomes Rejected, back until it
meets an electrode that already has a status.</p>
{S1A}

<h3>1b. Behind it, with a fault in the way</h3>
<p><b>Nothing is rejected.</b> The stretch between the fault and the test stays in
doubt and is flagged for testing instead. We now know production had recovered
<i>by</i> B14425, but not <i>where</i> it recovered, so every electrode in between
could be on either side of that line.</p>
{S1B}

<div class="page"></div>
<h3>1c. Ahead of it, no fault ahead</h3>
<p>Electrodes ahead that were flagged Needs testing go back to Not Tested. They
were doubted because of a fault the pass now stands between, so that doubt can no
longer reach them. They do <b>not</b> become Rejected: they were plated after the
test was done, so nothing here vouches for them.</p>
{S1C}

<h3>1d. Ahead of it, with a fault ahead</h3>
<p>Nothing ahead is cleared. Those electrodes are in doubt because of the
<i>later</i> fault, and that doubt is real.</p>
{S1D}

<div class="page"></div>
<h2>Scenario 2: you set an electrode to Tested, failed</h2>
<p>A fault spreads <b>both ways equally</b>, along its own vat. The two either
side are scrapped with it. The third each way is flagged Needs testing and marked
<b>test first</b>, because those are where the fault most likely started and
stopped, so they are the ones worth testing next. Beyond that it keeps marking
Needs testing outward until it reaches an electrode that was tested and passed.</p>
{S2}

<h3>2a. A pass stops it</h3>
<p>The doubt cannot travel through an electrode somebody tested and passed.</p>
{S2A}

<div class="page"></div>
<h3>2b. Other vats are untouched</h3>
<p>The line alternates between vats. Only vat 28 is affected here. The same is
true of a pass: it rejects along its own vat and leaves the others alone.</p>
{S2B}

<h2>Scenario 3: you put an electrode on the QC board</h2>
<p>It reads <b>Test ongoing</b>, whatever it read before. A recorded Scrapped, a
Rejected, a Needs testing, all of them give way: putting it on the board is the
statement that its status is being revisited. It also loses any <b>test first</b>
flag, because somebody is already testing it.</p>
{S3}

<div class="page"></div>
<h3>3a. Taking it back off the board</h3>
<p>Test ongoing is never saved anywhere. Remove the card and the electrode reads
whatever it <i>would</i> have read if it had never been on the board. Note this is
not always the status it showed before. B14420 had a verdict of its own, so it
comes back. B14421 only had a guess, and by the time the card came off, a nearby
fault had a better claim on it.</p>
{S3A}

<h2>Scenario 4: you re-test something</h2>
<p>Setting a new verdict on an electrode that already had one throws away every
guess the old verdict produced, and re-derives them from the new one. So if
B14466 failed, condemning its neighbours, and you later re-test it and it passes,
those neighbours are not left scrapped. They are worked out again.</p>
<p>This is why the app records, for every guessed status, <b>which</b> verdict it
came from and <b>when</b>. A guess older than the verdict it names was drawn from
a version of that verdict that no longer exists, so it is discarded.</p>
{retest_diagram()}

<div class="page"></div>
<h2>The rules underneath</h2>
<h3>What a sweep may overwrite</h3>
<table><tr><th>Status</th><th>Can a sweep change it?</th><th>Because</th></tr>
{overwrite}</table>
<p>A Rejected electrode being overwritten is the surprising one. It matters
because a Rejected electrode never appears in anybody's work queue. If a fault
turned up beside it and it kept that status, it would ship without ever being
looked at.</p>

<h3>Only a real pass is a wall</h3>
<p>A guess does not bound another guess. Rejected does not stop doubt, only
Tested passed does. A scrapped electrode does not stop a sweep either, so two
nearby faults produce one continuous zone rather than two separate ones.</p>

<h3>Distances</h3>
<table>
<tr><th>Rule</th><th>Reach</th></tr>
<tr><td>scrapped either side of a fault</td><td><b>2</b> electrodes</td></tr>
<tr><td>test first flag</td><td>the <b>3rd</b> each way</td></tr>
<tr><td>how far Needs testing runs</td><td>until a Tested passed</td></tr>
</table>
<p>If the process log does not say which vat an electrode came from, the fault
falls back to counting along the production line instead, and stops after the
third. A guess about the line is worth two electrodes, not a whole stretch.</p>

<h2>Why a row is locked</h2>
<p>A row is locked when a card <b>currently on the QC board</b> is what decides
its status. Dragging that card somewhere else would change the row, so it cannot
be pinned by hand at the same time.</p>
<p>Two things that look similar and are not. <b>Because of</b> names <b>one</b>
decision, the one recorded against the status. <b>Locked by</b> covers
<b>every</b> decision that contributed. A status can genuinely have more than one
cause: a stretch cleared between two passes needs both of them to stay cleared,
so both lock it. A finalised verdict never locks anything, it cannot be dragged
anywhere.</p>

<div class="note">Generated from docs/status-propagation.md by
tools/generate_status_propagation_pdf.py. Written with the planned wording
Rejected in place of the current Released.</div>

</body></html>"""


def find_browser() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    found = shutil.which("chrome") or shutil.which("msedge")
    if not found:
        raise SystemExit("No Chrome or Edge found to print the PDF.")
    return found


def main() -> None:
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(build_html(), encoding="utf-8")
    subprocess.run(
        [
            find_browser(),
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={OUT_PDF}",
            OUT_HTML.as_uri(),
        ],
        check=True,
        timeout=180,
    )
    print(f"wrote {OUT_PDF} ({OUT_PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
