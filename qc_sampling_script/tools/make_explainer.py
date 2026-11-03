"""Build the explainer PDF: from bad runs to the gain number.

For people who do not use the tool and will not read its README. Every figure in
it is computed by the same functions the window uses, so the document cannot
drift from the app.

    venv\\Scripts\\python.exe qc_sampling_script\\tools\\make_explainer.py

The run lengths below were read from dataloggingDev on 2026-09-04, vat 28 over
every electrode in the archive, which reaches back to March 2025 and is mostly
Gen3.1. They are frozen here on purpose: a document that quietly changes every
time it is rebuilt is not a document.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

from PyQt6.QtCore import QMarginsF, QSizeF
from PyQt6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PyQt6.QtWidgets import QApplication

from qc_sampling_script.data.run_lengths import (
    acceptable_band, best_option, detection, interval_curve, interval_for,
    stopped_and_lost,
)

# --- the data the document is about ----------------------------------------
RUNS = [1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 5, 5, 5, 5,
        5, 6, 8, 8, 9, 9, 10, 11, 11, 12, 13, 14, 14, 15, 16, 18, 19, 21, 22,
        38, 48, 78, 89]
BAD_SHARE = 0.141
VAT = "28"

# Two illustrative vats, not measured ones, held against each other in section
# 4. The whole point of the pair is that the same catch target buys wildly
# different things depending on the vat it is asked of.
DIRTY_SHARE = 0.15
CLEAN_SHARE = 0.03

# The clean vat's streaks, which is the half of the comparison that is
# easy to get wrong. A vat that makes less bad production also tends to
# recover from an excursion faster, and a SHORT streak is the hard one to
# hit. Diluting the dirty vat's long streaks instead would have left the
# interval unchanged, because the catch rate depends on streak length
# alone, and the comparison would have said nothing.
CLEAN_RUNS = [1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 5,
              5, 6, 7, 9]

# The formulas section 5 sets, in mathtext. Kept up here with the other
# wording rather than buried in the drawing code. The catch rate is the one
# that needs symbols: spelling out "streak length" three times inside a
# fraction is unreadable, so L and N are named in the line under it.
# Written as functions of the sampling interval, because that is the only
# thing anybody chooses: the fault rate and the streak lengths are both
# measured off the archive and fixed before the question is even asked.
_N = r"\mathrm{sampling\ interval}"
# Bare on the left, like the other two. The catch rate is the only one written
# as a function, because it is the only place the interval has to be named
# twice for the formula to make sense.
GOOD_FORMULA = (r"$\mathrm{good\ destroyed}\ =\ "
                r"\frac{100 \times (1 - \mathrm{fault\ rate})}{" + _N + r"}$")
# No argument anywhere on this one: the sampling interval does not appear in
# it directly, and the line under it is where the catch rate is opened up.
BAD_FORMULA = (r"$\mathrm{bad\ stopped}\ =\ 100 \times "
               r"\mathrm{fault\ rate} \times \mathrm{catch\ rate}$")
CATCH_FORMULA = (r"$\mathrm{catch\ rate}(" + _N + r")\ =\ "
                 r"\frac{\sum L_i \times \min(1,\ L_i / " + _N
                 + r")}{\sum L_i}$")
GAIN_FORMULA = (r"$\mathrm{gain}\ =\ \mathrm{bad\ stopped}"
                r"\ -\ \mathrm{good\ destroyed}$")
LEGEND_FORMULA = (r"$L_i\ =\ \mathrm{length\ of\ bad\ streak\ }i$"
                  "\n"
                  r"$\sum L_i\ =\ \mathrm{number\ of\ bad\ electrodes\ produced}$")

# The catch targets both tables are built from.
TARGETS = (0.70, 0.80, 0.90, 0.95)

# --- the look ---------------------------------------------------------------
TEAL = "#0D9488"
RED = "#E45A52"
INK = "#12232B"
MUTED = "#66787F"
PALE = "#E3EAEC"
BAND = "#D6EDE9"
# The band behind a highlighted rule, and how wide a drawn formula sits on it.
# Matches the .rule background and the text width, so a drawn formula and a
# written one are the same object on the page.
RULE_BG = "#E6F5F2"
FORMULA_WIDTH = 688

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "docs")
PDF = os.path.join(OUT, "how_sampling_is_decided.pdf")


def _save(fig, name: str) -> str:
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=170, bbox_inches="tight", pad_inches=0.02,
                facecolor="white")
    plt.close(fig)
    return path


def _bare(axes) -> None:
    for spine in axes.spines.values():
        spine.set_visible(False)
    axes.set_xticks([])
    axes.set_yticks([])


# --- the pictures -----------------------------------------------------------
def picture_streaks() -> str:
    """A stretch of production: mostly good, with two bad streaks in it."""
    fig, axes = plt.subplots(figsize=(9, 1.5))
    bad = set(range(14, 24)) | set(range(41, 44))
    for index in range(60):
        axes.add_patch(patches.Rectangle(
            (index, 0), 0.86, 1, facecolor=RED if index in bad else TEAL,
            alpha=1.0 if index in bad else 0.45, linewidth=0))
    axes.annotate("a streak of 10 bad", xy=(19, 1.1), xytext=(19, 1.9),
                  ha="center", color=RED, fontsize=11, fontweight="bold",
                  arrowprops=dict(arrowstyle="-", color=RED, lw=1))
    axes.annotate("a streak of 3", xy=(42.5, 1.1), xytext=(46, 1.9),
                  ha="center", color=RED, fontsize=11, fontweight="bold",
                  arrowprops=dict(arrowstyle="-", color=RED, lw=1))
    axes.text(0, -0.7, "production order, one box is one electrode",
              color=MUTED, fontsize=10)
    axes.set_xlim(-0.5, 60)
    axes.set_ylim(-1.2, 2.6)
    _bare(axes)
    return _save(fig, "p1_streaks.png")


def picture_lengths() -> str:
    """Every streak vat 28 has had, shortest to longest."""
    fig, axes = plt.subplots(figsize=(9, 2.6))
    axes.bar(range(len(RUNS)), sorted(RUNS), color=RED, width=0.75)
    axes.set_ylabel("electrodes in the streak", color=MUTED, fontsize=10)
    axes.set_xlabel(f"{len(RUNS)} streaks, shortest to longest",
                    color=MUTED, fontsize=10)
    axes.tick_params(colors=MUTED, labelsize=9)
    axes.set_xticks([])
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axes.spines[spine].set_color(PALE)
    axes.annotate("most are short", xy=(18, 4), xytext=(6, 46),
                  ha="left", color=INK, fontsize=11,
                  arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    axes.annotate("a few are long, and they\nhold most of the bad ones",
                  xy=(44.5, 86), xytext=(24, 66), ha="left", color=INK,
                  fontsize=11,
                  arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    return _save(fig, "p2_lengths.png")


def picture_comb() -> str:
    """Testing every 10th electrode: the long streak is hit, the short one is not."""
    fig, axes = plt.subplots(figsize=(9, 2.1))
    bad = set(range(14, 24)) | set(range(41, 44))
    tested = set(range(8, 60, 10))
    for index in range(60):
        axes.add_patch(patches.Rectangle(
            (index, 0), 0.86, 1, facecolor=RED if index in bad else TEAL,
            alpha=1.0 if index in bad else 0.45, linewidth=0))
    for index in sorted(tested):
        axes.add_patch(patches.Rectangle(
            (index, 0), 0.86, 1, facecolor="none", edgecolor=INK, lw=1.6))
        axes.plot([index + 0.43], [1.35], marker="v", color=INK, markersize=7)
    axes.text(30, 2.0, "test every 10th electrode", ha="center", color=INK,
              fontsize=11, fontweight="bold")
    axes.annotate("caught", xy=(18.4, -0.15), xytext=(13, -1.0), ha="center",
                  color=TEAL, fontsize=11, fontweight="bold",
                  arrowprops=dict(arrowstyle="->", color=TEAL, lw=1.2))
    axes.annotate("missed", xy=(42.4, -0.15), xytext=(47, -1.0), ha="center",
                  color=RED, fontsize=11, fontweight="bold",
                  arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
    axes.set_xlim(-0.5, 60)
    axes.set_ylim(-1.6, 2.5)
    _bare(axes)
    return _save(fig, "p3_comb.png")


def picture_formula(name: str, latex: str, height: float = 0.5,
                    size: float = 16) -> str:
    """A formula set as real maths, on the same band the rule boxes use.

    Drawn rather than written because Qt's rich text has no fractions, and a
    division written inline reads as a different statement from one with a
    numerator over a denominator.
    """
    # Sized in inches at the document's own 96 dpi, so the type comes out the
    # size it says rather than whatever the scaling to 688px happens to make.
    fig, axes = plt.subplots(figsize=(FORMULA_WIDTH / 96.0, height))
    drawn = axes.text(0.5, 0.5, latex, ha="center", va="center", color=TEAL,
                      fontsize=size, linespacing=1.6)
    # Shrink to fit rather than hand-tuning each formula: spelling an argument
    # out in words makes a line long, and one that overruns the band is
    # silently cut off at both ends.
    renderer = fig.canvas.get_renderer()
    limit = fig.get_size_inches()[0] * fig.dpi * 0.94
    while drawn.get_window_extent(renderer).width > limit and size > 8:
        size -= 0.5
        drawn.set_fontsize(size)
    axes.set_axis_off()
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=170, facecolor=RULE_BG)
    plt.close(fig)
    return path


def picture_destroyed() -> str:
    """The good destroyed formula, drawn as its two factors in turn.

    An illustration, not this vat: 100 electrodes with 15 bad in two streaks,
    tested every 10th, so the arithmetic can be followed by eye. The top strip
    is the division, the pulled-out row underneath is the multiplication.
    """
    total, interval, offset = 100, 10, 4
    bad = set(range(20, 30)) | set(range(55, 60))
    tested = list(range(offset, total, interval))
    hit_good = [index for index in tested if index not in bad]

    fig, axes = plt.subplots(figsize=(9, 3.6))
    for index in range(total):
        axes.add_patch(patches.Rectangle(
            (index, 2.0), 0.86, 1.0, facecolor=RED if index in bad else TEAL,
            alpha=1.0 if index in bad else 0.45, linewidth=0))
    for index in tested:
        axes.add_patch(patches.Rectangle(
            (index, 2.0), 0.86, 1.0, facecolor="none", edgecolor=INK, lw=1.5))
        axes.plot([index + 0.43], [3.35], marker="v", color=INK, markersize=6)
    axes.text(0, 4.05, f"100 electrodes produced, we test every {interval}th",
              color=INK, fontsize=11, fontweight="bold")

    # The ten we destroyed, pulled out and enlarged: only the good ones cost
    # anything, which is what the (1 - fault rate) factor is doing.
    box_w, gap, left, row = 3.4, 0.6, 30.0, -0.9
    for slot, index in enumerate(tested):
        x = left + slot * (box_w + gap)
        spoiled = index in bad
        axes.add_patch(patches.Rectangle(
            (x, row), box_w, 1.0, facecolor=RED if spoiled else TEAL,
            alpha=1.0 if spoiled else 0.45, edgecolor=INK, lw=1.3))
        axes.plot([index + 0.43, x + box_w / 2], [1.92, row + 1.06],
                  color="#C7D0D4", lw=0.7, zorder=0)
    axes.text(left, 0.45, f"the {len(tested)} electrodes the testing destroyed",
              color=INK, fontsize=11, fontweight="bold")
    axes.annotate(f"{len(hit_good)} were good:\nthis is the real cost",
                  xy=(left + box_w / 2, row - 0.1), xytext=(2, -2.6),
                  ha="left", color=TEAL, fontsize=10.5, fontweight="bold",
                  arrowprops=dict(arrowstyle="->", color=TEAL, lw=1.2))
    axes.annotate(f"{len(tested) - len(hit_good)} was bad anyway:\n"
                  f"no loss at all",
                  xy=(left + 2 * (box_w + gap) + box_w / 2, row - 0.1),
                  xytext=(74, -2.6), ha="left",
                  color=RED, fontsize=10.5, fontweight="bold",
                  arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
    axes.set_xlim(-1, total + 1)
    axes.set_ylim(-3.6, 4.4)
    _bare(axes)
    return _save(fig, "p5_destroyed.png")


def picture_catch() -> str:
    """The catch rate formula, drawn as one bar per streak.

    An illustration, not this vat: four streaks and a round interval, so the
    arithmetic can be checked by eye. Solid is what we expect to catch, pale is
    what escapes, and the catch rate is how much of the total is solid.
    """
    streaks, interval = [25, 12, 8, 3], 10
    chances = [min(1.0, length / interval) for length in streaks]
    caught = [length * chance for length, chance in zip(streaks, chances)]
    total, expected = sum(streaks), sum(caught)

    unit, left, row_h = 1.5, 26.0, 0.62
    fig, axes = plt.subplots(figsize=(9, 3.9))
    for slot, (length, chance, found) in enumerate(
            zip(streaks, chances, caught)):
        y = 3.4 - slot * 0.78
        axes.text(left - 2, y + row_h / 2, f"a streak of {length}",
                  ha="right", va="center", color=INK, fontsize=10.5,
                  fontweight="bold")
        # The whole streak pale, the part we expect to catch solid on top.
        axes.add_patch(patches.Rectangle(
            (left, y), length * unit, row_h, facecolor=RED, alpha=0.22,
            linewidth=0))
        axes.add_patch(patches.Rectangle(
            (left, y), found * unit, row_h, facecolor=RED, alpha=1.0,
            linewidth=0))
        note = ("always caught" if chance >= 1
                else f"caught {chance * 10:.0f} times in 10")
        axes.text(left + length * unit + 2.5, y + row_h / 2, note, ha="left",
                  va="center", color=TEAL if chance >= 1 else MUTED,
                  fontsize=10, fontweight="bold" if chance >= 1 else "normal")

    axes.plot([left, left + total * unit], [0.28, 0.28], color=PALE, lw=1.2)
    y = -0.75
    axes.text(left - 2, y + row_h / 2, "all together", ha="right", va="center",
              color=INK, fontsize=10.5, fontweight="bold")
    axes.add_patch(patches.Rectangle(
        (left, y), total * unit, row_h, facecolor=RED, alpha=0.22, linewidth=0))
    axes.add_patch(patches.Rectangle(
        (left, y), expected * unit, row_h, facecolor=RED, alpha=1.0,
        linewidth=0))
    axes.text(left + total * unit + 2.5, y + row_h / 2,
              f"{expected:.1f} of {total} bad electrodes", ha="left",
              va="center", color=INK, fontsize=10, fontweight="bold")
    axes.text(left, -1.55, f"catch rate = {expected:.1f} / {total} = "
                           f"{expected / total * 100:.0f} %",
              ha="left", va="center", color=TEAL, fontsize=13,
              fontweight="bold")
    axes.text(0, 4.55, f"four streaks, testing every {interval}th electrode",
              color=INK, fontsize=11, fontweight="bold")
    axes.text(0, 4.15, "solid is what we expect to catch, pale is what escapes",
              color=MUTED, fontsize=9.5)
    axes.set_xlim(0, 100)
    axes.set_ylim(-2.1, 4.9)
    _bare(axes)
    return _save(fig, "p5_catch.png")


def picture_gain(interval: int) -> str:
    """The gain formula: two bars from one line, the gap between them the gain.

    Bars rather than counted boxes, because this one runs on the vat's real
    figures and those are not whole electrodes.
    """
    catch = detection(RUNS, interval)
    stopped, lost = stopped_and_lost(BAD_SHARE, interval, catch, 0.0)
    gain = stopped - lost

    fig, axes = plt.subplots(figsize=(9, 2.9))
    left, span = 22.0, 62.0
    unit = span / max(stopped, lost)

    axes.text(0, 3.5, f"out of every 100 electrodes produced, "
                      f"testing every {interval}th",
              color=INK, fontsize=11, fontweight="bold")
    for y, value, colour, label in (
            (2.0, stopped, TEAL, "bad ones stopped"),
            (0.7, lost, RED, "good ones destroyed")):
        axes.add_patch(patches.Rectangle((left, y), value * unit, 0.85,
                                         facecolor=colour, alpha=0.85,
                                         linewidth=0))
        axes.text(left - 2, y + 0.42, label, ha="right", va="center",
                  color=colour, fontsize=10.5, fontweight="bold")
        axes.text(left + value * unit + 1.5, y + 0.42, f"{value:.1f}",
                  ha="left", va="center", color=colour, fontsize=11,
                  fontweight="bold")

    # The gap between the two bar ends is the gain, on the same scale.
    short, long = left + lost * unit, left + stopped * unit
    axes.plot([short, short], [0.55, 2.95], color=MUTED, lw=1, ls=":")
    axes.plot([long, long], [1.95, 2.95], color=MUTED, lw=1, ls=":")
    axes.plot([short, short, long, long], [-0.15, -0.5, -0.5, -0.15],
              color=TEAL, lw=1.5)
    axes.text((short + long) / 2, -0.95,
              f"gain = {gain:.1f} electrodes per 100 = {gain:.1f} %",
              ha="center", va="top", color=TEAL, fontsize=12.5,
              fontweight="bold")
    axes.set_xlim(0, 100)
    axes.set_ylim(-2.1, 3.9)
    _bare(axes)
    return _save(fig, "p6_gain.png")


def picture_scales(interval: int) -> str:
    """The two numbers, side by side, at one testing interval."""
    catch = detection(RUNS, interval)
    saved, lost = stopped_and_lost(BAD_SHARE, interval, catch, 0.0)
    fig, axes = plt.subplots(figsize=(9, 2.2))
    axes.barh([1], [saved], color=TEAL, height=0.5)
    axes.barh([0], [lost], color=RED, height=0.5)
    axes.text(saved + 0.15, 1, f"  {saved:.1f} %  bad electrodes stopped",
              va="center", color=INK, fontsize=12, fontweight="bold")
    axes.text(lost + 0.15, 0, f"  {lost:.1f} %  good electrodes destroyed"
                              f" by the testing",
              va="center", color=INK, fontsize=12, fontweight="bold")
    axes.set_xlim(0, max(saved, lost) * 2.6)
    axes.set_ylim(-0.6, 1.8)
    axes.text(0, 1.55, f"testing every {interval}th electrode, "
                       f"per 100 electrodes made",
              color=MUTED, fontsize=11)
    _bare(axes)
    return _save(fig, "p4_scales.png")


def picture_curve() -> str:
    """Gain against the testing interval, with the peak and the flat band."""
    curve = interval_curve(RUNS, BAD_SHARE, 0.0)
    best = best_option(curve)
    peak = best.net_percent
    low, high = acceptable_band(curve, best)
    limit = best.sampling_interval * 4
    points = [option for option in curve if option.sampling_interval <= limit]
    xs = [option.sampling_interval for option in points]
    ys = [option.net_percent for option in points]

    fig, axes = plt.subplots(figsize=(9, 3.6))
    # Cropped below, not scaled to the data. Testing every electrode scores
    # about minus fifty, and an axis stretched to reach it leaves the part
    # being decided a few pixels tall.
    floor, ceiling = -peak * 1.7, peak * 1.45
    axes.axvspan(low, high, color=BAND, zorder=0)
    axes.axhline(0, color=MUTED, lw=1, ls=":")
    axes.plot(xs, ys, color=TEAL, lw=2.6, zorder=3)
    axes.plot([best.sampling_interval], [peak], "o", color=TEAL, markersize=11,
              markeredgecolor="white", markeredgewidth=2, zorder=4)

    # Under the peak rather than over it. The curve is at its highest here by
    # definition, so above it is where the top captions already are.
    axes.text(best.sampling_interval + 3, peak - ceiling * 0.12,
              f"best: one in every {best.sampling_interval}",
              color=TEAL, fontsize=12, fontweight="bold", va="top")

    near = min(range(len(xs)), key=lambda i: abs(ys[i] - floor * 0.45))
    axes.annotate("test too often and the testing\nitself costs more than it saves",
                  xy=(xs[near], ys[near]), xycoords="data",
                  xytext=(0.13, 0.04), textcoords="axes fraction",
                  ha="left", va="bottom", color=MUTED, fontsize=10,
                  arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    axes.annotate("test too rarely and\nwhole streaks slip past",
                  xy=(limit * 0.93, ys[-int(len(ys) * 0.07)]), xycoords="data",
                  xytext=(0.60, 0.62), textcoords="axes fraction",
                  ha="left", va="bottom", color=MUTED, fontsize=10,
                  arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))

    axes.set_xlabel("test one electrode in every ...", color=MUTED, fontsize=10)
    axes.set_ylabel("gain, % of production", color=MUTED, fontsize=10)
    axes.tick_params(colors=MUTED, labelsize=9)
    axes.set_xlim(1, limit)
    axes.set_ylim(floor, ceiling)
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        axes.spines[spine].set_color(PALE)
    return _save(fig, "p5_curve.png")


def target_rows(runs: list[int], bad_share: float) -> list[dict]:
    """What each catch target takes, and what it is worth, on one vat.

    Both arguments matter and they are not interchangeable. The interval comes
    from the STREAK LENGTHS alone: how much bad production a vat makes does not
    change how hard one streak is to hit. The bad share then decides what
    hitting them is worth.
    """
    rows = []
    for share in TARGETS:
        every = interval_for(runs, share)
        reached = detection(runs, every)
        stopped, spoiled = stopped_and_lost(bad_share, every, reached, 0.0)
        rows.append({"share": share * 100, "every": every, "spoiled": spoiled,
                     "stopped": stopped, "net": stopped - spoiled})
    return rows


def table_of(rows: list[dict], caption: str) -> str:
    """One catch-target table: what the target asks for, and what it costs."""
    body = "".join(
        f"<tr><td><b>{row['share']:.0f} %</b></td>"
        f"<td><b>{row['every']}</b></td>"
        f"<td>{row['spoiled']:.1f} %</td></tr>"
        for row in rows)
    return (f'<p class="cap">{caption}</p>'
            '<table class="tbl" width="100%" cellspacing="0" cellpadding="5">'
            '<tr class="hd"><td>Finding this share of bad electrodes</td>'
            "<td>Test one in every</td>"
            "<td>Good electrodes destroyed</td></tr>" + body + "</table>")


# --- the document -----------------------------------------------------------
def html(images: dict, facts: dict) -> str:
    """The whole document. Kept in one place so the wording can be read."""
    table = table_of(
        facts["targets"],
        f"{facts['dirty_share']:.0f} % production fault rate, with bad streaks "
        f"averaging {facts['dirty_mean']:.0f} electrodes")
    clean_table = table_of(
        facts["clean_targets"],
        f"{facts['clean_share']:.0f} % production fault rate, with bad streaks "
        f"averaging {facts['clean_mean']:.0f} electrodes")
    return f"""
<html><body>
<h1>How we decide how often to test</h1>
<h2>1. Faults arrive in streaks</h2>
<p>A bad electrode is almost never on its own. Something in the process drifts,
the line makes bad electrodes for a while, somebody fixes it, and the line goes
back to normal. So production looks like this:</p>
<p class="pic"><img src="{images['streaks']}" width="640"></p>
<p>That single fact is what makes sampling work at all. If bad electrodes were
scattered at random, testing one in twenty would find one in twenty of them and
nothing more. Because they come in streaks, one test can expose a whole stretch
of bad production.</p>

<h2>2. Streak length statistics</h2>
<p>Vat {facts['vat']}, every electrode in the archive: <b>{facts['count']} streaks</b>, made
of {facts['bad']} bad electrodes.</p>
<p class="pic"><img src="{images['lengths']}" width="640"></p>

<h2>3. Chance of catching a streak</h2>
<p class="pic"><img src="{images['comb']}" width="640"></p>
<p>A streak is caught only if a test happens to land inside it, so the longer it
runs, the harder it is to miss. A shorter
one is caught only in proportion to its length, so half as long as the interval
means caught about half the time. Short streaks are the ones that slip
through.</p>

<h2 class="break">4. Target catch rate</h2>
{table}
{clean_table}
<p>Setting a target catch rate for bad electrodes is not the right way of
thinking. Catching 90% of bad electrodes is completely different when the share
of bad electrodes is low or high. When the fault rate is low, catching most
electrodes becomes really expensive. Therefore, when setting a sampling rate we
should choose a number that does not destroy too many good electrodes.</p>
<p>We should come up with a formula that takes into account the cost of
testing.</p>

<h2>5. Cost and benefit of testing</h2>
<p>Every time we test an electrode, we destroy it. That is a loss for our company.
But is it really? It is only a real loss if the electrode is good. If it is bad,
destroying it has cost us nothing. Everything below is counted per 100 electrodes
produced, so that the cost and the benefit can be compared directly. Out of every
100 electrodes we make, this is how many good ones the testing destroys:</p>
<p class="pic"><img src="{images['f_good']}" width="688"></p>
<p class="pic"><img src="{images['destroyed']}" width="640"></p>
<p>On the other hand, what we gain by testing is not only the bad electrodes
that have been tested, but the entire streaks of bad electrodes that we do not sell.
Out of the same 100 electrodes, this is how many bad ones the testing keeps from
being sold:</p>
<p class="pic"><img src="{images['f_bad']}" width="688"></p>
<p>, where</p>
<p class="pic"><img src="{images['f_catch']}" width="688"></p>
<p>, where</p>
<p class="pic"><img src="{images['f_legend']}" width="688"></p>
<p>A streak is caught in proportion to how long it is, and once it is longer
than the sampling interval it is always caught. Each streak then counts for as
many electrodes as it holds, so a long one weighs more than a short one.</p>
<p class="pic"><img src="{images['catch']}" width="640"></p>

<h2>6. Gain</h2>
<p class="pic"><img src="{images['f_gain']}" width="688"></p>
<p class="pic"><img src="{images['gain']}" width="640"></p>
<p>Gain means the net benefit of testing, measured in percentage. 
If we stop more bad electrodes from reaching customers 
than we destroy in the process, we have a positive gain.
The algorithm simply chooses the sampling rate that maximizes this gain.
The plot of the gain as a function of the sampling interval is shown below. </p>
<p class="pic"><img src="{images['curve']}" width="640"></p>
<p>The <b>gain</b> tries to balance between too frequent and too infrequent testing.
Too infrequent testing allows bad electrodes to reach customers, 
while too frequent testing destroys good electrodes unnecessarily.</p>

<h2>7. What the model assumes</h2>
<p>The gain counts every bad electrode we stop as worth a whole electrode, which
is the same as assuming that a bad electrode reaching a customer is worth nothing
at all. That is the one place in the whole calculation that rests on judgement
rather than on something we measured.</p>
<p>Putting a more accurate number on it is challenging. We can measure how a bad
electrode performs and say that it runs at, for example, 70 % of the efficiency
of a good one. That does not make it worth 70 % of a good one. The customer paid
for a full electrode and expects a full electrode, so the loss is not simply the
missing 30 %. It is that shortfall plus whatever it costs us in claims,
replacements and standing with that customer, and none of that can be priced with
any confidence. Treating a shipped bad electrode as worth nothing keeps the
assumption in one visible place rather than hiding a guess inside the arithmetic.
If a defensible figure for what a shipped fault really costs ever appears, it
goes into the same formula and the recommended interval moves with it.</p>

</body></html>
"""


CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; color: #12232B;
       font-size: 10.5pt; }
h1 { font-size: 21pt; color: #0D9488; margin-bottom: 6px; }
h2 { font-size: 13pt; color: #12232B; margin-top: 11px; margin-bottom: 1px; }
p { line-height: 106%; margin-top: 3px; margin-bottom: 3px; }
.rule { background: #E6F5F2; padding: 6px 10px; font-weight: bold;
        color: #0D9488; margin: 10px 0 10px 0; text-align: center; }
.pic { line-height: 100%; margin: 2px 0 0px 0; }
.tbl { margin: 2px 0 8px 0; }
.break { page-break-before: always; }
.cap { color: #66787F; font-size: 9.5pt; font-weight: bold;
       margin: 8px 0 0 0; }
.tbl td { border-bottom: 1px solid #E3EAEC; }
.hd td { background: #E6F5F2; color: #0D9488; font-weight: bold;
         font-size: 9.5pt; }
.foot { color: #66787F; font-size: 9pt; margin-top: 12px; }
"""


def facts_from(interval: int) -> dict:
    total = sum(RUNS)
    catch = detection(RUNS, interval)
    saved, lost = stopped_and_lost(BAD_SHARE, interval, catch, 0.0)
    curve = interval_curve(RUNS, BAD_SHARE, 0.0)
    best = best_option(curve)
    low, high = acceptable_band(curve, best)
    # Where half the bad electrodes sit: walk the streaks longest first until
    # half the bad production is accounted for.
    running, half_at = 0, RUNS[-1]
    for length in sorted(RUNS, reverse=True):
        running += length
        if running >= total / 2:
            half_at = length
            break
    targets = target_rows(RUNS, DIRTY_SHARE)
    tightest = targets[-1]
    return {
        "targets": targets,
        "clean_targets": target_rows(CLEAN_RUNS, CLEAN_SHARE),
        "dirty_mean": sum(RUNS) / len(RUNS),
        "clean_mean": sum(CLEAN_RUNS) / len(CLEAN_RUNS),
        "clean_share": CLEAN_SHARE * 100,
        "dirty_share": DIRTY_SHARE * 100,
        "tight_share": tightest["share"], "tight_every": tightest["every"],
        "tight_gain": tightest["net"],
        "vat": VAT, "count": len(RUNS), "bad": total,
        "shortest": min(RUNS), "longest": max(RUNS), "half_at": half_at,
        "interval": interval, "catch": catch * 100,
        "bad_share": BAD_SHARE * 100, "saved": saved, "lost": lost,
        "destroyed": 100.0 / interval, "gain": saved - lost,
        "best": best.sampling_interval, "low": low, "high": high,
    }


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)

    facts = facts_from(20)
    images = {
        "streaks": picture_streaks(),
        "lengths": picture_lengths(),
        "comb": picture_comb(),
        "destroyed": picture_destroyed(),
        "f_good": picture_formula("p5a_good.png", GOOD_FORMULA, 0.62),
        "f_bad": picture_formula("p5b_bad.png", BAD_FORMULA, 0.44),
        "catch": picture_catch(),
        "f_catch": picture_formula("p5c_catch.png", CATCH_FORMULA, 0.74),
        "f_legend": picture_formula("p5d_legend.png", LEGEND_FORMULA, 0.72, 13),
        "gain": picture_gain(facts["interval"]),
        "f_gain": picture_formula("p6a_gain.png", GAIN_FORMULA, 0.44),
        "scales": picture_scales(facts["interval"]),
        "curve": picture_curve(),
    }

    writer = QPdfWriter(PDF)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(16, 15, 16, 15),
                          QPageLayout.Unit.Millimeter)
    writer.setResolution(96)
    writer.setTitle("How we decide how often to test")

    document = QTextDocument()
    document.setDefaultFont(QFont("Segoe UI", 10))
    document.setDefaultStyleSheet(CSS)
    document.setPageSize(QSizeF(writer.width(), writer.height()))
    document.setHtml(html(images, facts))
    printer = getattr(document, "print_", None) or document.print
    printer(writer)

    for path in images.values():
        os.remove(path)
    print(f"wrote {PDF} ({os.path.getsize(PDF) // 1024} kB), "
          f"{document.pageCount()} pages")
    print(f"  best {facts['best']}, band {facts['low']}..{facts['high']}, "
          f"gain at {facts['interval']} = {facts['gain']:+.2f}%")


if __name__ == "__main__":
    main()
