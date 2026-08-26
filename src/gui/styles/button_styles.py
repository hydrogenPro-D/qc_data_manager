"""Button styling for PyQt elements - Modern design."""

from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from PyQt6.QtGui import QColor

# The app accent. Every primary action, selection highlight and chart accent
# derives from this family.
ACCENT = "#14B8A6"
ACCENT_DARK = "#0F9E8D"
ACCENT_LIGHT = "#55BDBD"

PRIMARY_BUTTON_STYLE = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #14B8A6, stop:1 #0F9E8D);
    color: white;

    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;

    padding: 8px 16px;

    font-family: 'Segoe UI';
    font-size: 14px;
    font-weight: 600;

    letter-spacing: 0.4px;

    min-height: 20px;
}

QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #19C6B3, stop:1 #14B8A6);
    border: 1px solid rgba(255,255,255,0.12);
}

QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0F9E8D, stop:1 #0C8A7C);
    padding-top: 9px;
    padding-bottom: 7px;
}

QPushButton:disabled {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E5E7EB, stop:1 #D1D5DB);
    color: #9CA3AF;
    border: 1px solid #D1D5DB;
}
"""

# The same button, sized for a toolbar rather than a dialog. A dialog button is
# the thing you came to press, so it stays a big tablet target; a toolbar sits
# beside a title all day and reads as chunky at that size.
TOOLBAR_BUTTON_STYLE = PRIMARY_BUTTON_STYLE.replace(
    "padding: 8px 16px;", "padding: 4px 12px;").replace(
    "min-height: 20px;", "min-height: 16px;").replace(
    "border-radius: 12px;", "border-radius: 9px;").replace(
    "font-size: 14px;", "font-size: 13px;").replace(
    "padding-top: 9px;", "padding-top: 5px;").replace(
    "padding-bottom: 7px;", "padding-bottom: 3px;")

SECONDARY_BUTTON_STYLE = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F3F3F3, stop:1 #EFEFEF);
        color: #1F1F1F;
        border: 1px solid #D4D4D4;
        border-radius: 6px;
        padding: 8px 16px;
        font-weight: 500;
        font-size: 12px;
        font-family: 'Segoe UI', Arial, sans-serif;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #EFEFEF, stop:1 #E1E1E1);
        border: 1px solid #7A7A7A;
    }
    QPushButton:pressed {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E1E1E1, stop:1 #D4D4D4);
    }
    QPushButton:disabled {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F5F5F5, stop:1 #E4E4E4);
        color: #D4D4D4;
        border: 1px solid #E4E4E4;
    }
"""

# For the one action in a form that destroys something. Outlined rather than
# filled: it should be findable without competing with the button you actually
# came to press, and only commit to red once the pointer is on it.
DANGER_BUTTON_STYLE = """
    QPushButton {
        background: #FFFFFF;
        color: #DC2626;
        border: 1px solid #FCA5A5;
        border-radius: 6px;
        padding: 8px 16px;
        font-weight: 600;
        font-size: 12px;
        font-family: 'Segoe UI', Arial, sans-serif;
    }
    QPushButton:hover {
        background: #DC2626;
        color: #FFFFFF;
        border: 1px solid #DC2626;
    }
    QPushButton:pressed {
        background: #B91C1C;
        color: #FFFFFF;
        border: 1px solid #B91C1C;
    }
    QPushButton:disabled {
        background: #F5F5F5;
        color: #D4D4D4;
        border: 1px solid #E4E4E4;
    }
"""

ACCENT_BUTTON_STYLE = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2BA44F, stop:1 #1E8A3F);
        color: white;
        border: none;
        border-radius: 6px;
        padding: 10px 20px;
        font-weight: 600;
        font-size: 13px;
        font-family: 'Segoe UI', Arial, sans-serif;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #32B859, stop:1 #2BA44F);
    }
    QPushButton:pressed {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1A8040, stop:1 #127030);
    }
    QPushButton:disabled {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E4E4E4, stop:1 #D9D9D9);
        color: #999999;
    }
"""

# The "you are here" state of a toolbar action (Add / Edit) while its form is open.
HIGHLIGHTED_BUTTON_STYLE = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0F9E8D, stop:1 #0C7A6F);
    color: white;

    border: 1px solid rgba(255, 255, 255, 0.10);

    border-radius: 12px;

    padding: 12px 22px;

    font-family: 'Segoe UI';
    font-size: 14px;
    font-weight: 600;

    letter-spacing: 0.4px;

    min-height: 20px;
}

QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #14B8A6, stop:1 #0F9E8D);
    border: 1px solid rgba(255, 255, 255, 0.18);
}

QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0C8A7C, stop:1 #08635F);
    padding-top: 13px;
    padding-bottom: 11px;
}

QPushButton:disabled {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E5E7EB, stop:1 #D1D5DB);
    color: #9CA3AF;
    border: 1px solid #D1D5DB;
}
"""


def apply_modern_shadow(widget,
                        blur=18,
                        x_offset=0,
                        y_offset=4,
                        color=QColor(20, 184, 166, 70)):
    """Soft teal drop-shadow used on buttons and cards."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setXOffset(x_offset)
    shadow.setYOffset(y_offset)
    shadow.setColor(color)

    widget.setGraphicsEffect(shadow)
