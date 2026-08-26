"""Backend factory — builds the data backend the app reads through.

    MainWindow ──► build_backend() ──► DbManager / DbWriter
                                       reads:  dbo.MetaData, qc.vw_* views
                                       writes: qc.usp_* stored procedures

Called once, at window construction: a bad config or unreachable database fails
at launch rather than mid-use.
"""

from __future__ import annotations


def build_backend(db_install_confirm=None, ask_image_root=None):
    """Construct the SQL backend.

    Args:
        db_install_confirm: Optional ``(driver_name, installer_path) -> bool``
            callback the GUI uses to ask before the ODBC-driver installer runs.
            Keeps the data layer Qt-free.
        ask_image_root: Optional ``() -> str`` folder picker, asked only when
            the SEM images library cannot be found by searching. Same reason it
            is a callable rather than a dialog down here.
    """
    from src.data_access.db_manager import DbManager

    return DbManager(install_confirm=db_install_confirm,
                     ask_image_root=ask_image_root)
