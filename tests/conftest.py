"""Shared test setup: importable project root + headless Qt."""

import os
import sys
from pathlib import Path

# GUI tests run without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
