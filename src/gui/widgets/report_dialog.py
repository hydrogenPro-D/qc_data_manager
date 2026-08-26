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

from PyQt6.QtCore import QSizeF, QMarginsF
from PyQt6.QtGui import QTextDocument, QPdfWriter, QPageSize, QPageLayout
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
    QFileDialog, QFrame, QApplication,
)

from ..styles.button_styles import (
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    apply_modern_shadow,
)
from ..styles.table_styles import SCROLLBAR_STYLE

# A4 portrait in points, and the margin the document is laid out inside.
A4_POINTS = QSizeF(595.0, 842.0)
PAGE_MARGIN_MM = 14


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


def build_report_html(ranges, totals: dict[str, int], tints: dict[str, str],
                      date_from: str, date_to: str,
                      total_shown: int, total_all: int) -> str:
    """The whole report as one HTML document.

    ``totals`` is the summary footing the range table, ``tints`` the pale row
    fills, so a status looks the same on paper as on screen.

    No chart. The report is read as numbers, and a ring saying 91% next to a
    table saying 91% was one more thing to keep in step for no extra meaning.
    """
    generated = dt.datetime.now().strftime("%d.%m.%Y")

    range_rows = "".join(
        f"<tr>"
        f"<td>{item.label}</td>"
        f"<td align='right'>{item.count:,}</td>"
        f"<td bgcolor='{tints.get(item.status, '#FFFFFF')}'>{item.status}</td>"
        f"</tr>".replace(",", " ")
        for item in ranges
    )

    # Footer: the summary the old workbook reported. Not a sum of the ranges
    # above it, since a tested electrode is counted here but never listed
    # there, so the rows are not labelled as one.
    summary_rows = "".join(
        f"<tr bgcolor='{tints.get(status, '#FFFFFF')}'>"
        f"<td><b>{status}</b></td>"
        f"<td align='right'><b>{count:,}</b></td>"
        f"<td>{count / (total_shown or 1) * 100:.0f}%</td>"
        f"</tr>".replace(",", " ")
        for status, count in totals.items()
    )

    scope = (f"{total_shown:,} of {total_all:,} electrodes".replace(",", " ")
             if total_shown != total_all else "")

    scope_line = (f'<p style="font-size:7pt; color:#6B6B6B; margin:0 0 6px 0;">{scope}</p>'
                  if scope else "")

    # Every block gets margin-left:0 and the tables cellspacing="0": Qt's rich
    # text otherwise indents tables and headings against the h1, so the report
    # ends up with two different left edges.
    return f"""
    <html><body style="font-family:'Segoe UI',Arial,sans-serif; color:#1F1F1F;
                       margin:0; padding:0;">
      <h1 style="font-size:19pt; margin:0 0 6px 0; padding:0;">
        HydrogenPro QC Status Report &ndash; {generated}</h1>
      {scope_line}

      <p style="font-size:7.5pt; margin:0 0 8px 0; padding:0;"><b>Date range</b><br/>
        <span style="color:#6B6B6B;">{date_from} &ndash; {date_to}</span></p>

      <table width="100%" cellpadding="3" cellspacing="0" border="1"
             bordercolor="#E1E1E1" style="font-size:7.5pt; margin:0;">
        <tr bgcolor="#55BDBD">
          <th align="left"><font color="#FFFFFF">Electrode Range</font></th>
          <th align="right" width="70"><font color="#FFFFFF">Count</font></th>
          <th align="left" width="150"><font color="#FFFFFF">Status</font></th>
        </tr>
        {range_rows}
        {summary_rows}
      </table>
    </body></html>
    """


class ReportDialog(QDialog):
    """Full-height preview of the report, with a PDF export beside it."""

    def __init__(self, parent=None, html: str = "",
                 suggested_name: str = "qc_report.pdf") -> None:
        super().__init__(parent)
        self._html = html
        self._suggested_name = suggested_name

        self.setWindowTitle("QC status report")
        self.setStyleSheet("QDialog { background-color: #F5F5F5; }")
        self._fill_screen()

        preview = QTextBrowser()
        preview.setStyleSheet(
            "QTextBrowser { background-color: #FFFFFF; border: 1px solid #E1E1E1; }"
            + SCROLLBAR_STYLE)
        preview.setHtml(html)
        # Paginate the preview exactly as the PDF will be.
        preview.document().setPageSize(A4_POINTS)
        preview.setMinimumWidth(620)

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
        side_panel.setFixedWidth(240)
        side_panel.setLayout(side)

        layout = QHBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        layout.addWidget(preview, 1)
        layout.addWidget(side_panel, 0)
        self.setLayout(layout)

    def _fill_screen(self) -> None:
        """Almost full screen, but still a window."""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1100, 800)
            return
        available = screen.availableGeometry()
        self.resize(int(available.width() * 0.9), int(available.height() * 0.9))
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
        document.setHtml(self._html)
        document.setPageSize(QSizeF(writer.pageLayout().paintRectPoints().size()))
        document.print(writer)
        # The writer only flushes on destruction, so drop it before handing the
        # file to a reader that is about to open it.
        del writer
        _open_saved_file(path)
