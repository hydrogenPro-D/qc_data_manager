"""External-change polling — the watermark contract every entity follows.

Multiple operators can edit the same data at once. Each entity exposes a cheap
watermark — ``(max_row_version, row_count)`` — that the GUI polls on a timer.
When it moves, that tab reloads. One tiny query beats re-fetching every tab.

``make_update_callback`` builds the zero-arg closure a tab expects:
``{"has_updates": bool, <result_key>: <payload|None>}``.

Our own writes are not hidden from it. Advancing the baseline after a write
would spare one reload, and reading the watermarks to do so costs more than the
reload does, on the thread that can least afford it.
"""

from __future__ import annotations

from typing import Callable

# Sentinel for an un-seeded baseline (distinct from a real None watermark).
UNSEEDED = object()


class WatermarkPoller:
    """Tracks per-entity watermark baselines and builds tab poll callbacks."""

    def __init__(self, read_watermarks: Callable[[], dict]) -> None:
        # ``read_watermarks`` returns {entity: watermark} for every entity.
        self._read_watermarks = read_watermarks
        self._baselines: dict = {}
        self._warned: set = set()

    def make_update_callback(self, entity: str, getter: Callable, result_key: str = "data"):
        """Build the poll closure for one tab.

        Seeds the baseline now (at widget-construction time, right after the
        tab's initial load) so a change between load and first poll isn't missed.
        """
        self._seed(entity)

        def _check() -> dict:
            try:
                current = self._read_watermarks().get(entity)
            except Exception as e:
                if entity not in self._warned:
                    print(f"Watermark poll failed for {entity!r}: {e}")
                    self._warned.add(entity)
                return {"has_updates": False, result_key: None}

            baseline = self._baselines.get(entity, UNSEEDED)
            if baseline is UNSEEDED or current == baseline:
                self._baselines[entity] = current
                return {"has_updates": False, result_key: None}

            # Watermark moved → reload this tab. Adopt the new baseline ONLY after
            # a successful reload, so a failed reload is retried on the next poll
            # instead of being silently marked "handled" (would strand stale data).
            try:
                payload = getter()
            except Exception as e:
                print(f"Reload after watermark change failed for {entity!r}: {e}")
                return {"has_updates": False, result_key: None, "error": str(e)}
            self._baselines[entity] = current
            return {"has_updates": True, result_key: payload}

        return _check

    def _seed(self, entity: str) -> None:
        try:
            self._baselines[entity] = self._read_watermarks().get(entity)
        except Exception:
            pass  # left un-seeded; the first poll seeds instead
