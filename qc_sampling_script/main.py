"""Entry point for the sampling analysis tool.

    venv\\Scripts\\python.exe qc_sampling_script\\main.py

Measures how long the good and bad runs actually are, per vat and pooled, so
the testing rate can be argued from this plant's own history rather than from
an assumed defect rate.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from qc_sampling_script.gui.sampling_window import run_app  # noqa: E402

if __name__ == "__main__":
    run_app()
