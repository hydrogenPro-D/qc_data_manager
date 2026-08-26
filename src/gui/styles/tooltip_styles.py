"""Tooltip styling, applied once to the whole application.

Qt draws a tooltip with the platform palette unless a stylesheet says
otherwise, and under the styling this app sets that came out as black on black,
unreadable. Six tooltips across the tabs explain things that are not otherwise
on screen, a locked status, what undo would reverse, why a View button is red,
so they have to be legible.

Styled like the rest of the app rather than like a system tooltip: white, with
the same text colour and a soft grey border, as the cards and dialogs use.

Applied to the QApplication rather than per widget: a tooltip is a top level
window of its own, so styling the widget that owns it does not reach it.

NO border-radius HERE, however much the rest of the app is rounded. Giving
QToolTip a radius makes Qt mark the tip window translucent so it can round the
corners, and on Windows everything it then does not paint comes out black: the
tooltip reads as white text on a black slab. It looks like the stylesheet was
ignored, and it is the opposite, the stylesheet was applied too well. Square
corners, and it renders.
"""

TOOLTIP_STYLE = """
    QToolTip {
        background-color: #FFFFFF;
        color: #1F1F1F;
        border: 1px solid #D4D4D4;
        padding: 6px 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11px;
    }
"""
