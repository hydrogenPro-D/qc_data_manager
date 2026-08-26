"""Tab and container styling utilities for PyQt elements - Modern design."""

from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget, QVBoxLayout
from PyQt6.QtGui import QColor

# Top-level app tab bar: flat strip, teal underline + white pane on the active tab.
MAIN_TAB_STYLE = """
    QTabWidget::pane {
        border: 1px solid #E1E1E1;
    }
    QTabBar::tab {
        background-color: #F5F5F5;
        color: #1F1F1F;
        padding: 10px 24px;
        margin-right: 2px;
        font-weight: 500;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 13px;
        border: none;
    }
    QTabBar::tab:hover {
        background-color: #ECECEC;
    }
    QTabBar::tab:selected {
        background-color: #FFFFFF;
        color: #55BDBD;
        border-bottom: 3px solid #55BDBD;
        padding: 10px 24px;
    }
"""


def create_styled_container_layout(outer_margins: tuple = (10, 10, 10, 10),
                                   inner_margins: tuple = (16, 16, 16, 16)):
    """Create the card container every tab sits in, and return its layouts.

    Common pattern across all tabs: an outer layout with margins, an inner
    white rounded container with a teal shadow, and the content layout inside it.

    Args:
        outer_margins: Tuple (left, top, right, bottom) for outer layout margins
        inner_margins: Tuple (left, top, right, bottom) for inner container margins

    Returns:
        Tuple of (main_layout, content_container, content_layout)
    """
    main_layout = QVBoxLayout()
    main_layout.setContentsMargins(*outer_margins)
    main_layout.setSpacing(0)

    content_container = QWidget()
    content_container.setStyleSheet("""
        QWidget {
            background-color: #FFFFFF;
            border-radius: 8px;
        }
    """)

    shadow = QGraphicsDropShadowEffect(content_container)
    shadow.setBlurRadius(8)
    shadow.setXOffset(0)
    shadow.setYOffset(2)
    shadow.setColor(QColor("#14B8A6"))
    content_container.setGraphicsEffect(shadow)

    content_layout = QVBoxLayout()
    content_layout.setContentsMargins(*inner_margins)
    content_layout.setSpacing(0)

    return main_layout, content_container, content_layout


# Styling for the inner plot tab widget (one tab per trend chart).
PLOT_TABS_STYLE = """
    QTabWidget::pane {
        border: 1px solid #E1E1E1;
        border-radius: 6px;
        background-color: #FFFFFF;
        top: -1px;
    }
    QTabBar::tab {
        background-color: #F5F5F5;
        color: #1F1F1F;
        padding: 8px 20px;
        margin-right: 2px;
        font-weight: 500;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        border: 1px solid #E1E1E1;
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }
    QTabBar::tab:hover {
        background-color: #ECECEC;
    }
    QTabBar::tab:selected {
        background-color: #FFFFFF;
        color: #55BDBD;
        border-bottom: 3px solid #55BDBD;
    }
"""
