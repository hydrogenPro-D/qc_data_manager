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
# Production could never answer it: the electrode left their list before
# anybody got to it. Set by the database, never by either application.
EXPIRED = "expired"

# Why a request exists. Raised automatically by the sampling rule, raised
# because a failure nearby needs the neighbours tested, or asked for by hand.
KIND_INTERVAL = "interval"
KIND_FAULT = "fault"
KIND_MANUAL = "manual"

QC_STATUS_COLUMN = "QC request"
# When it was asked for. Deliberately NOT in the Electrodes tab's display map:
# the frame carries it for the pull list, which is where it is read.
QC_REQUESTED_COLUMN = "QC requested"
# Which coupons the request wants. Also off the display map: it is drawn beside
# the state rather than given a column of its own.
QC_COUPONS_COLUMN = "QC coupons"

# An electrode with no request has no QC status, which is most of them, and a
# blank column reads as "nothing to do here" far faster than a word would.
NO_REQUEST = ""

STATE_LABELS: dict[str, str] = {
    AWAITING_PRODUCTION: "Requested",
    AWAITING_QC: "Offered to QC",
    # Animated in the Electrodes tab, the way Test ongoing is: QC has
    # agreed and the electrode is now somebody else's move.
    TO_BE_SENT: "Pending",
    SENT: "Sent",
    # Said in full: on its own the word reads as the electrode having been
    # rejected, which is a different thing and the one that matters more.
    DECLINED: "Request declined",
    # Nobody said no. The question simply ran out of anybody to ask.
    EXPIRED: "Request expired",
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
