"""Table styling for PyQt elements - Modern design."""

# Pixel sizes for scrollbars. Deliberately thick so they're easy to drag on a
# tablet. Other modules import these to size sibling widgets, so the layout
# stays aligned if the values change.
SCROLLBAR_VERTICAL_WIDTH = 35
SCROLLBAR_HORIZONTAL_HEIGHT = 35

# Shared scrollbar styling for all widgets. Arrow buttons are zeroed out so the
# thumb owns the whole track.
SCROLLBAR_STYLE = f"""
    QScrollBar:vertical {{
        border: none;
        background-color: #F0F0F0;
        width: {SCROLLBAR_VERTICAL_WIDTH}px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background-color: #C0C0C0;
        border-radius: 12px;
        min-height: 20px;
        margin: 2px 2px 2px 2px;
        border: none;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: #A0A0A0;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        border: none;
        background: none;
        height: 0px;
    }}
    QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {{
        border: none;
        background: none;
        height: 0px;
        width: 0px;
    }}
    QScrollBar:horizontal {{
        border: none;
        background-color: #F0F0F0;
        height: {SCROLLBAR_HORIZONTAL_HEIGHT}px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background-color: #C0C0C0;
        border-radius: 12px;
        min-width: 20px;
        margin: 2px 2px 2px 2px;
        border: none;
    }}
    QScrollBar::handle:horizontal:hover {{
        background-color: #A0A0A0;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        border: none;
        background: none;
        width: 0px;
    }}
    QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal {{
        border: none;
        background: none;
        height: 0px;
        width: 0px;
    }}
"""

# A slim variant for narrow places where the 35px bar would eat the content,
# such as a kanban column or a filter strip.
SLIM_SCROLLBAR_STYLE = """
    QScrollBar:vertical {
        border: none; background-color: transparent; width: 8px; margin: 0px;
    }
    QScrollBar:horizontal {
        border: none; background-color: transparent; height: 8px; margin: 0px;
    }
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background-color: #D1D5DB; border-radius: 4px;
    }
    QScrollBar::handle:vertical { min-height: 24px; }
    QScrollBar::handle:horizontal { min-width: 24px; }
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
        background-color: #9CA3AF;
    }
    QScrollBar::add-line, QScrollBar::sub-line {
        border: none; background: none; height: 0px; width: 0px;
    }
    QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""

TABLE_STYLE = """
    QTableWidget {
        background-color: #FFFFFF;
        gridline-color: #E4E4E4;
        border: 1px solid #E1E1E1;
        border-radius: 6px;
        margin: 10px 0px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11px;
    }
    QTableWidget::item {
        padding: 8px 6px;
        border: none;
        color: #1F1F1F;
    }
    QTableWidget::item:selected {
        background-color: #E8F0FA;
        color: #1F1F1F;
    }
    QHeaderView::section {
        background-color: #55BDBD;
        color: white;
        padding: 10px 6px;
        border: none;
        font-weight: 600;
        font-size: 12px;
        font-family: 'Segoe UI', Arial, sans-serif;
        border-right: 1px solid #3fa3a3;
    }
    QHeaderView::section:last {
        border-right: none;
    }
    QTableWidget::item:hover {
        background-color: #F0F4F8;
    }
""" + SCROLLBAR_STYLE

# For a list that is scanned rather than read, in a panel that is short of
# height. Same look, a header that takes what it needs and no more: the pull
# list beside the electrode table is pinned above the report button, so every
# pixel its header keeps is a pixel of the chart above it.
PULL_LIST_TABLE_STYLE = TABLE_STYLE + """
    QHeaderView::section { padding: 4px 6px; }
"""

# For tables with many narrow numeric columns. Same look, tighter horizontal
# padding and a smaller header, so twenty columns still fit on one screen.
COMPACT_TABLE_STYLE = TABLE_STYLE + """
    QTableWidget::item { padding: 6px 2px; }
    QHeaderView::section { padding: 8px 3px; font-size: 11px; }
"""
