"""QC status report — an A4 preview with a PDF export.

Portrait, because the range list runs long and would spill over several pages
either way. The report is one HTML document: Qt paginates it for the preview
and for the PDF from the same source, so what you see is what gets saved.

Domain-neutral inputs: the owning tab hands over the ranges and the summary.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys

from PyQt6.QtCore import QSizeF, QMarginsF, QRectF, QUrl, Qt
from PyQt6.QtGui import (QTextDocument, QPdfWriter, QPageSize, QPageLayout,
                         QImage, QPainter, QPixmap, QColor)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFileDialog, QFrame, QWidget, QApplication,
)

from ..styles.button_styles import (
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    apply_modern_shadow,
)
from ..styles.table_styles import SCROLLBAR_STYLE, SCROLLBAR_VERTICAL_WIDTH

# A4 portrait in points, and the margin the document is laid out inside.
A4_POINTS = QSizeF(595.0, 842.0)
PAGE_MARGIN_MM = 14
# The grey border around the sheets in the preview.
DESK_MARGIN = 18
# The buttons beside the preview.
SIDE_PANEL_WIDTH = 220
_POINTS_PER_MM = 72.0 / 25.4

# The name the chart is registered under. Qt rich text resolves an <img src>
# against the document's own resources, so this is a key rather than a path.
CHART_RESOURCE = "qc_status_chart"
# Points, matching the column it sits in. The image itself is rendered far
# larger and scaled down here, which is what keeps it sharp in the PDF.
CHART_WIDTH = 185

# The summary tables' two number columns. Wide enough for five digits with a
# separator: a cell narrower than its number wraps it onto a second line, and
# the separators are non-breaking spaces throughout for the same reason.
COUNT_WIDTH = 50
# The chart legend's two number columns. Both sized for their widest possible
# contents, five digits and "100%": a cell narrower than what it holds gets
# broken across two lines, and "100" over "%" is the giveaway.
LEGEND_VALUE_WIDTH = 44
LEGEND_SHARE_WIDTH = 38
SHARE_WIDTH = 40

# How much bigger than life the preview draws its pages. A page is 516 points
# across, which on screen would be 516 pixels and too small to read, so it is
# drawn larger. Only the preview scales; the PDF is unaffected.
PREVIEW_SCALE = 1.25

# Qt rich text cannot pin a block to the foot of a page, so the glossary is
# pushed down by a spacer, and the size of that spacer is searched for rather
# than calculated. This is how much is then given back, so the fit is never
# exact to the last point.
GLOSSARY_PAD = 4
# Smaller than the tables, but not smaller than this. The preview draws the
# page at roughly one point per pixel, so anything under about 7pt is there on
# paper and unreadable on screen.
GLOSSARY_SIZE = 6.5


def _grouped(value) -> str:
    """A number with non-breaking thousands separators.

    Non-breaking because a table cell narrower than the number will otherwise
    break it across two lines. Applied to the NUMBER, never to the whole row:
    doing that to the row was quietly eating the comma out of labels like
    "Tested, passed".
    """
    return f"{value:,}".replace(",", "&nbsp;")


def _open_saved_file(path: str) -> None:
    """Show the finished PDF in whatever the machine reads PDFs with.

    Failing to open it is not worth an error dialog: the file is written and
    the operator knows where it went, they asked for the name.
    """
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 (the path is one we just wrote)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except OSError as e:
        print(f"Saved {path} but could not open it: {e}")


def _glossary_drop(assemble, chart_png: bytes | None) -> float:
    """The largest drop that still leaves the glossary on page one.

    Found by trying, not by arithmetic. The obvious sum, page height minus
    content height, is wrong because the spacer is a paragraph and a paragraph
    is as tall as its LINE, about 1.2x its font size. Rather than carry that
    factor around, this lays the page out and asks Qt how many pages it came
    to, then binary searches for the biggest drop that still answers one.

    Measured against the PRINTED page, which is smaller than the preview's:
    the PDF takes a margin off every side, so fitting to the preview would
    overflow on paper. Fitting to the smaller one is safe both ways, and the
    glossary simply sits a little higher on screen.

    The chart is registered first. Without it Qt lays out a missing image
    instead of a 185pt one and every answer comes out too generous.
    """
    margin = PAGE_MARGIN_MM * _POINTS_PER_MM
    printable = QSizeF(A4_POINTS.width() - 2 * margin,
                       A4_POINTS.height() - 2 * margin)

    document = QTextDocument()
    if chart_png:
        image = QImage()
        image.loadFromData(chart_png, "PNG")
        if not image.isNull():
            document.addResource(QTextDocument.ResourceType.ImageResource,
                                 QUrl(CHART_RESOURCE), image)
    document.setPageSize(printable)

    def fits(drop: float) -> bool:
        document.setHtml(assemble(drop))
        return document.pageCount() <= 1

    if not fits(0):
        return 0.0                      # already full, do not push it further

    low, high = 0.0, printable.height()
    for _ in range(12):                 # to within a fraction of a point
        middle = (low + high) / 2
        if fits(middle):
            low = middle
        else:
            high = middle
    return max(0.0, low - GLOSSARY_PAD)


def build_report_html(ranges, production: list, qc: list, tints: dict[str, str],
                      date_from: str, date_to: str,
                      total_shown: int, total_all: int,
                      filters: dict | None = None, qc_total: int = 0,
                      chart_counts: dict[str, int] | None = None,
                      chart_colors: dict[str, str] | None = None,
                      definitions: list | None = None,
                      chart_png: bytes | None = None) -> str:
    """The whole report as one HTML document.

    ``production`` and ``qc`` are the two summary tables, each a list of
    ``(label, count, depth)``. They are kept apart because they answer
    different questions: what the line made, and what QC has done about it.
    ``tints`` are the pale row fills, so a status looks the same on paper as it
    does on screen.

    EACH TABLE IS MEASURED AGAINST ITS OWN FOOTING. Production is a share of
    everything produced; QC is a share of the electrodes actually tested, so a
    pass rate reads as a pass rate rather than as a sliver of the whole line.

    ``chart_counts`` puts the status ring beside them, on its own page. The
    image itself is registered on the document by :class:`ReportDialog`, since
    Qt rich text loads images as document resources rather than from a URL.
    """
    generated = dt.datetime.now().strftime("%d.%m.%Y")

    range_rows = "".join(
        f"<tr>"
        f"<td>{item.label}</td>"
        f"<td align='right'>{_grouped(item.count)}</td>"
        f"<td bgcolor='{tints.get(item.status, '#FFFFFF')}'>{item.status}</td>"
        f"</tr>"
        for item in ranges
    )

    def summary_table(title: str, rows: list, base: int) -> str:
        """One summary table, its rows indented by depth.

        Depth 2 is a breakdown of the row above it, so it is set smaller and
        greyer and its label is led in, rather than reading as another total.
        """
        body = ""
        for label, count, depth in rows:
            share = count / (base or 1) * 100
            lead = "&nbsp;&nbsp;&nbsp;&nbsp;" if depth == 2 else ""
            weight = "normal" if depth == 2 else "bold"
            colour = "#6B6B6B" if depth == 2 else "#1F1F1F"
            fill = "#FFFFFF" if depth == 2 else tints.get(label, "#FFFFFF")
            body += (
                f"<tr bgcolor='{fill}'>"
                f"<td><span style='font-weight:{weight}; color:{colour};'>"
                f"{lead}{label}</span></td>"
                f"<td align='right'><span style='font-weight:{weight}; "
                f"color:{colour};'>{_grouped(count)}</span></td>"
                f"<td><span style='color:{colour};'>{share:.0f}%</span></td>"
                f"</tr>")

        heading = (f'<th align="left"><font color="#FFFFFF">{title}</font></th>'
                   if title else
                   '<th align="left"><font color="#FFFFFF">&nbsp;</font></th>')
        return f"""
      <table width="100%" cellpadding="3" cellspacing="0" border="1"
             bordercolor="#E1E1E1" style="font-size:7.5pt; margin:0;">
        <tr bgcolor="#55BDBD">
          {heading}
          <th align="right" width="{COUNT_WIDTH}">
            <font color="#FFFFFF">Count</font></th>
          <th align="left" width="{SHARE_WIDTH}">
            <font color="#FFFFFF">Share</font></th>
        </tr>
        {body}
      </table>"""

    def legend_table() -> str:
        """The ring's key, as text rather than baked into the picture.

        Set as HTML so it stays sharp in the PDF and can be copied out of it,
        and so the swatch colours match the table tints exactly.
        """
        total = sum(value for value in (chart_counts or {}).values() if value > 0)
        rows = ""
        for name, value in (chart_counts or {}).items():
            share = (value / total * 100) if total else 0.0
            swatch = (chart_colors or {}).get(name, "#9CA3AF")
            rows += (
                f'<tr>'
                f'<td width="14" bgcolor="{swatch}">'
                f'<font color="{swatch}" size="1">&nbsp;</font></td>'
                f'<td>&nbsp;{name}</td>'
                f'<td align="right" width="{LEGEND_VALUE_WIDTH}"><b>{_grouped(value)}</b></td>'
                f'<td align="right" width="{LEGEND_SHARE_WIDTH}">'
                f'<font color="#6B6B6B">{share:.0f}%</font></td>'
                f'</tr>')
        return f"""
      <table width="100%" cellpadding="2" cellspacing="0" border="0"
             style="font-size:7.5pt; margin:0;">{rows}</table>"""

    # The ring and the tables read the same numbers, so they sit side by side,
    # straight after the ranges rather than on a page of their own.
    chart_block = "" if not chart_counts else f"""
      <div>
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td width="60%" valign="top">
              {summary_table("Production", production, total_shown)}
              <p style="font-size:7pt; margin:10px 0 0 0;">&nbsp;</p>
              {summary_table("QC", qc, qc_total)}
            </td>
            <td width="4%">&nbsp;</td>
            <td width="36%" valign="top">
              <p style="font-size:9pt; margin:0 0 4px 0;"><b>Status breakdown</b></p>
              <img src="{CHART_RESOURCE}" width="{CHART_WIDTH}"
                   height="{CHART_WIDTH}" />
              {legend_table()}
            </td>
          </tr>
        </table>
      </div>"""

    # Without a chart the two tables still have to appear, one under the other.
    plain_block = "" if chart_counts else f"""
      <div>
        {summary_table("Production", production, total_shown)}
        <p style="font-size:7pt; margin:10px 0 0 0;">&nbsp;</p>
        {summary_table("QC", qc, qc_total)}
      </div>"""

    # What each word in the tables above means. Set small and grey: it is
    # there to be consulted, not read. The terms come from the caller so they
    # always match the labels actually printed.
    definition_rows = "".join(
        f'<tr>'
        f'<td width="110" valign="top">{term}</td>'
        f'<td valign="top"><font color="#6B6B6B">{meaning}</font></td>'
        f'</tr>'
        for term, meaning in (definitions or []))
    # Dropped to the foot of the page by a fixed gap. The block above it is a
    # constant height (two tables of known rows beside a fixed-size chart), so
    # the gap does not have to be worked out, and it is kept short of the full
    # drop to leave the page a margin for error.
    def glossary(drop: float) -> str:
        """The glossary, pushed down the page by ``drop`` points."""
        if not definition_rows:
            return ""
        return f"""
      <p style="font-size:{drop:.0f}pt; margin:0;">&nbsp;</p>
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="font-size:{GLOSSARY_SIZE}pt; margin:0;">{definition_rows}</table>"""

    ranges_block = f"""
      <div style="page-break-before:always;">
        <table width="100%" cellpadding="3" cellspacing="0" border="1"
               bordercolor="#E1E1E1" style="font-size:7.5pt; margin:0;">
          <tr bgcolor="#55BDBD">
            <th align="left"><font color="#FFFFFF">Electrode Range</font></th>
            <th align="right" width="{COUNT_WIDTH}">
              <font color="#FFFFFF">Count</font></th>
            <th align="left" width="150"><font color="#FFFFFF">Status</font></th>
          </tr>
          {range_rows}
        </table>
      </div>"""

    scope = (f"{total_shown:,} of {total_all:,} electrodes".replace(",", "&nbsp;")
             if total_shown != total_all else "")

    # Only the filters that were actually narrowing anything. A reader has to be
    # able to tell a report of the whole line from a report of two vats, and the
    # totals look identical either way.
    chosen = "".join(
        f"<b>{name}</b> {', '.join(values)}&nbsp;&nbsp;&nbsp;"
        for name, values in (filters or {}).items())
    filter_line = (f'<p style="font-size:7.5pt; margin:0 0 8px 0; padding:0;">'
                   f'<b>Filtered by</b><br/>'
                   f'<span style="color:#6B6B6B;">{chosen}</span></p>'
                   if chosen else "")

    scope_line = (f'<p style="font-size:7pt; color:#6B6B6B; margin:0 0 6px 0;">{scope}</p>'
                  if scope else "")

    # Every block gets margin-left:0 and the tables cellspacing="0": Qt's rich
    # text otherwise indents tables and headings against the h1, so the report
    # ends up with two different left edges.
    def assemble(drop: float, with_ranges: bool = True) -> str:
        return f"""
    <html><body style="font-family:'Segoe UI',Arial,sans-serif; color:#1F1F1F;
                       margin:0; padding:0;">
      <h1 style="font-size:19pt; margin:0 0 6px 0; padding:0;">
        HydrogenPro QC Status Report &ndash; {generated}</h1>
      {scope_line}

      <p style="font-size:7.5pt; margin:0 0 8px 0; padding:0;"><b>Date range</b><br/>
        <span style="color:#6B6B6B;">{date_from} &ndash; {date_to}</span></p>
      {filter_line}

      {chart_block}{plain_block}
      {glossary(drop)}
      {ranges_block if with_ranges else ""}
    </body></html>
    """

    return assemble(_glossary_drop(
        lambda drop: assemble(drop, with_ranges=False), chart_png))


class ReportDialog(QDialog):
    """Full-height preview of the report, with a PDF export beside it."""

    def __init__(self, parent=None, html: str = "",
                 suggested_name: str = "qc_report.pdf",
                 chart_png: bytes | None = None) -> None:
        super().__init__(parent)
        self._html = html
        self._suggested_name = suggested_name
        # Kept as an image rather than as bytes: the preview and the PDF are
        # two separate documents, and each has to be handed the same one.
        self._chart = QImage()
        if chart_png:
            self._chart.loadFromData(chart_png, "PNG")

        self.setWindowTitle("QC status report")
        self.setStyleSheet("QDialog { background-color: #F5F5F5; }")

        preview = self._build_preview()

        download = QPushButton("Download report")
        download.setMinimumHeight(42)
        download.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(download)
        download.clicked.connect(self._download)

        close = QPushButton("Close")
        close.setMinimumHeight(38)
        close.setStyleSheet(SECONDARY_BUTTON_STYLE)
        close.clicked.connect(self.accept)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(10)
        side.addWidget(download, 0)
        side.addStretch(1)
        side.addWidget(close, 0)

        side_panel = QFrame()
        side_panel.setFixedWidth(SIDE_PANEL_WIDTH)
        side_panel.setLayout(side)

        layout = QHBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        layout.addWidget(preview, 0)
        layout.addWidget(side_panel, 1)
        self.setLayout(layout)
        self._fill_screen(self._preview_width() + SIDE_PANEL_WIDTH + 16 + 32)

    def _printable(self) -> QSizeF:
        """The page the PDF actually prints on, margins already taken off."""
        margin = PAGE_MARGIN_MM * _POINTS_PER_MM
        return QSizeF(A4_POINTS.width() - 2 * margin,
                      A4_POINTS.height() - 2 * margin)

    def _build_preview(self) -> QScrollArea:
        """One image per printed page, stacked on a grey desk.

        Drawn rather than reflowed. A QTextBrowser lays the document out at
        whatever width the window happens to be, which is why the preview used
        to be a wide page that matched nothing: the PDF is a fixed A4 column.
        Painting each page instead means what is on screen is the page, at the
        same proportions and with the same line and page breaks.
        """
        page = self._printable()
        document = QTextDocument()
        self._add_chart(document)
        document.setPageSize(page)
        document.setHtml(self._html)

        sheets = QVBoxLayout()
        sheets.setContentsMargins(DESK_MARGIN, DESK_MARGIN, DESK_MARGIN, DESK_MARGIN)
        sheets.setSpacing(18)
        for index in range(document.pageCount()):
            sheets.addWidget(self._page_label(document, page, index), 0,
                             Qt.AlignmentFlag.AlignHCenter)
        sheets.addStretch(1)

        desk = QWidget()
        desk.setLayout(sheets)
        desk.setStyleSheet("background-color: #E9EAEC;")

        area = QScrollArea()
        area.setWidget(desk)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea { border: none; }" + SCROLLBAR_STYLE)
        area.setFixedWidth(self._preview_width())
        return area

    @staticmethod
    def _preview_width() -> int:
        """Exactly one page across, plus its desk and the scrollbar."""
        return (round(A4_POINTS.width() * PREVIEW_SCALE)
                + 2 * DESK_MARGIN + SCROLLBAR_VERTICAL_WIDTH + 4)

    @staticmethod
    def _page_label(document: QTextDocument, page: QSizeF, index: int) -> QLabel:
        """Page ``index`` painted onto a white sheet.

        The SHEET is a whole A4 and the content is inset by the printer's
        margin, which is what the PDF looks like. Drawing the printable area
        alone made a sheet with the text running to its edge, so the preview
        and the file it produces did not resemble each other.
        """
        scale = PREVIEW_SCALE
        margin = PAGE_MARGIN_MM * _POINTS_PER_MM
        sheet = QPixmap(round(A4_POINTS.width() * scale),
                        round(A4_POINTS.height() * scale))
        sheet.fill(QColor("#FFFFFF"))

        painter = QPainter(sheet)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.scale(scale, scale)
        # Into the margin, then slide the page being drawn up to the origin.
        # The rect clips it, so a block straddling the boundary is cut exactly
        # where the printer cuts it.
        painter.translate(margin, margin - page.height() * index)
        document.drawContents(painter, QRectF(0, page.height() * index,
                                              page.width(), page.height()))
        painter.end()

        label = QLabel()
        label.setPixmap(sheet)
        label.setFixedSize(sheet.size())
        label.setStyleSheet("border: 1px solid #C9CBCF;")
        return label

    def _add_chart(self, document: QTextDocument) -> None:
        """Register the ring on one document, if there is one to register."""
        if not self._chart.isNull():
            document.addResource(QTextDocument.ResourceType.ImageResource,
                                 QUrl(CHART_RESOURCE), self._chart)

    def _fill_screen(self, wanted_width: int) -> None:
        """As wide as one page needs, and as tall as the screen allows.

        Width comes from the page, not the screen: a window wider than the
        sheet is empty desk, and the point of the preview is the sheet.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(wanted_width, 900)
            return
        available = screen.availableGeometry()
        self.resize(min(wanted_width, available.width()),
                    int(available.height() * 0.92))
        self.move(available.center() - self.rect().center())

    def _download(self) -> None:
        """Ask where to save, then write the same HTML out as a PDF."""
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save report", self._suggested_name, "PDF document (*.pdf)")
        if not path:
            return

        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Portrait)
        writer.setPageMargins(
            QMarginsF(PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM),
            QPageLayout.Unit.Millimeter)
        writer.setResolution(300)

        document = QTextDocument()
        self._add_chart(document)
        document.setHtml(self._html)
        document.setPageSize(QSizeF(writer.pageLayout().paintRectPoints().size()))
        document.print(writer)
        # The writer only flushes on destruction, so drop it before handing the
        # file to a reader that is about to open it.
        del writer
        _open_saved_file(path)
