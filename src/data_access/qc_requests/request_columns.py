"""The states a QC request moves through, and what the tab calls them.

The stored values are the contract with Production Data Manager and are shared
with it through ``qc_requests.qc_request_state``. The labels are this
application's alone: relabelling a column here is a GUI change, renaming a
state is a coordinated migration in both repositories.

The two awaiting states read differently on this side on purpose. One is a
question this application asked and is waiting on; the other is a question it
has been asked and has to answer.
"""

from __future__ import annotations

# Stored, and shared with the other application. Never translate these.
AWAITING_PRODUCTION = "awaiting_production"
AWAITING_QC = "awaiting_qc"
TO_BE_SENT = "to_be_sent"
SENT = "sent"
DECLINED = "declined"

# Why a request exists. Raised automatically by the sampling rule, raised
# because a failure nearby needs the neighbours tested, or asked for by hand.
KIND_INTERVAL = "interval"
KIND_FAULT = "fault"
KIND_MANUAL = "manual"

QC_STATUS_COLUMN = "QC request"

# An electrode with no request has no QC status, which is most of them, and a
# blank column reads as "nothing to do here" far faster than a word would.
NO_REQUEST = ""

STATE_LABELS: dict[str, str] = {
    AWAITING_PRODUCTION: "Requested",
    AWAITING_QC: "Offered to QC",
    TO_BE_SENT: "Approved, waiting",
    SENT: "Sent",
    DECLINED: "Declined",
}

# The ones still moving. A row in either of these is waiting on somebody, and
# is what a count on the tab would be counting.
OPEN_STATES = frozenset({AWAITING_PRODUCTION, AWAITING_QC, TO_BE_SENT})


def state_label(state: str) -> str:
    """What the column shows for one stored state.

    An unrecognised state is shown as it is stored rather than blanked: the
    other application owns half of this workflow and may well add one before
    this application knows about it, and a row that silently reads as having
    no request would be a lie.
    """
    text = str(state or "").strip()
    return STATE_LABELS.get(text, text)
