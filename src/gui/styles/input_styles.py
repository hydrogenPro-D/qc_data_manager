"""Input field styling for PyQt elements - Modern design."""

from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget
from PyQt6.QtGui import QColor

INPUT_FIELD_STYLE = """
    QLineEdit, QPlainTextEdit {
        background-color: #FFFFFF;
        border: 1px solid #D4D4D4;
        border-radius: 6px;
        padding: 8px 10px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        color: #1F1F1F;
        selection-background-color: #0078D4;
    }
    QLineEdit:focus, QPlainTextEdit:focus {
        border: 2px solid #0078D4;
        background-color: #FFFFFF;
    }
    QLineEdit:disabled, QPlainTextEdit:disabled {
        background-color: #F5F5F5;
        color: #999999;
        border: 1px solid #E4E4E4;
    }
"""

LABEL_STYLE = """
    QLabel {
        color: #1F1F1F;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        font-weight: 500;
    }
"""

TITLE_LABEL_STYLE = """
    QLabel {
        color: #1F1F1F;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 24px;
        font-weight: 600;
    }
"""


def style_group_widget(widget: QWidget, bg_color: str) -> None:
    """Style a form group as a rounded card: subtle white → bg_color gradient,
    soft teal shadow.

    Args:
        widget: The QWidget to style
        bg_color: Background color in hex (e.g., "#E3F2FD")
    """
    widget.setStyleSheet(f"""
        QWidget {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                        stop:0 #FFFFFF,
                                        stop:0.3 #FFFFFF,
                                        stop:1 {bg_color});
            border: 1px solid #CCCCCC;
            border-radius: 12px;
        }}
    """)

    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(8)
    shadow.setXOffset(0)
    shadow.setYOffset(2)
    shadow.setColor(QColor("#14B8A6"))
    widget.setGraphicsEffect(shadow)


COMBO_BOX_STYLE = """
    QComboBox {
        background-color: #FFFFFF;
        border: 1px solid #D4D4D4;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 13px;
        color: #1F1F1F;
    }
    QComboBox:focus { border: 1.5px solid #55BDBD; }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 26px;
        border-left: 1px solid #E1E1E1;
        background-color: #F5F6F7;
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
    }
    QComboBox::down-arrow {
        width: 0px; height: 0px;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid #6B7280;
    }
    QComboBox QAbstractItemView {
        border: 1px solid #E1E1E1;
        background-color: #FFFFFF;
        selection-background-color: #E6F6F6;
        selection-color: #1F1F1F;
        outline: none;
    }
"""
