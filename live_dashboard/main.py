"""Entry point for the office wall screen.

    venv\\Scripts\\python.exe live_dashboard\\main.py

Runs from the repository root and reuses QC Data Manager's data layer wholesale:
the same backend, the same electrode processor, the same status rules and the
same donut. Nothing about QC is re-implemented here, only re-presented, so a
change to how a status is worked out reaches the wall as well as the desk.
"""

from __future__ import annotations

import os
import sys

# The repo root, so ``src`` and ``live_dashboard`` both import whatever the
# working directory happens to be. The TV machine runs this from a shortcut.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from live_dashboard.gui.dashboard_window import run_dashboard  # noqa: E402

if __name__ == "__main__":
    run_dashboard()
