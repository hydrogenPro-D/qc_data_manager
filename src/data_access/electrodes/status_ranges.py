"""Collapsing a per-electrode status list into contiguous ranges.

The QC overview has always been read as ranges — "B11727 - B11848: GOOD" — not
as thousands of individual rows. This turns the app's per-electrode statuses
back into that shape for reporting.

A range ends when the status changes, not when the numbering skips: a missing
electrode number doesn't split an otherwise uniform stretch, which is how the
workbook's ranges read too.

Pure functions — no Qt, no DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data_access.electrodes.status_propagation import electrode_number


@dataclass
class StatusRange:
    """A run of consecutive electrodes sharing one status."""

    start: str
    end: str
    status: str
    count: int
    # The day the status settled, blank where nothing has settled it yet. Part
    # of what makes a run: a stretch marked as done on two different days is
    # two entries, because that is what the report is reporting.
    finalised: str = ""

    @property
    def label(self) -> str:
        """'B11727 - B11848', or just the marking for a single electrode."""
        return self.start if self.start == self.end else f"{self.start} - {self.end}"


def collapse_to_ranges(markings: list[str], statuses: list[str],
                       finalised: list[str] | None = None) -> list[StatusRange]:
    """Pair up markings with statuses and collapse them into ranges.

    Args:
        markings: Electrode markings, any order.
        statuses: The status of each, positionally aligned with ``markings``.
        finalised: When each settled, aligned the same way. Optional: without
            it every electrode reads blank and only the status splits a run.

    Returns:
        Ranges ordered by electrode number, oldest first.
    """
    dates = list(finalised or [])
    entries = []
    for index, (marking, status) in enumerate(zip(markings, statuses)):
        number = electrode_number(marking)
        if number is not None:
            settled = str(dates[index]).strip() if index < len(dates) else ""
            entries.append((number, str(marking).strip(), str(status).strip(),
                            settled))
    entries.sort(key=lambda entry: entry[0])

    ranges: list[StatusRange] = []
    for _number, marking, status, settled in entries:
        last = ranges[-1] if ranges else None
        if last is not None and last.status == status and last.finalised == settled:
            last.end = marking
            last.count += 1
        else:
            ranges.append(StatusRange(marking, marking, status, 1, settled))
    return ranges
