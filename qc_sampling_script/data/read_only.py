"""A backend that can only be read from.

This tool points at the real database, so "we just do not write anything" needs
to be something the code enforces rather than something the author remembered.
The wrapper exposes the four reads the window makes and nothing else: every
save, every stored procedure and every board mutation on ``DbManager`` is simply
not reachable through it, and reaching for one raises instead of running.

It is not a security boundary. Python has no such thing, and somebody
determined can still dig the wrapped object out. It is there so that an
accidental write is impossible and a deliberate one has to be written on
purpose, in daylight, by somebody who meant it.
"""

from __future__ import annotations


class ReadOnlyBackend:
    """Wraps a ``DbManager`` and permits only the calls this tool needs."""

    # Everything the sampling window asks of the backend. Adding to this list
    # is the moment to check that what you are adding only reads.
    ALLOWED = frozenset({
        "get_metadata",          # the electrode list, already processed
        "load_vats",             # the vat of each electrode, from the process log
        "vats_pending",          # whether that join has answered yet
        "database_name",         # for the banner
        "plant_database_name",   # for the banner
    })

    def __init__(self, backend) -> None:
        # Name-mangled so an absent-minded ``backend._backend.save_x()`` is at
        # least an awkward thing to type.
        self.__backend = backend

    def __getattr__(self, name: str):
        """Hand over an allowed read, refuse everything else.

        Only called for attributes not found on the wrapper itself, so the
        wrapped object stays out of reach of ordinary attribute access.
        """
        if name in self.ALLOWED:
            return getattr(self.__backend, name)
        raise AttributeError(
            f"{name!r} is not available: this tool opens the database read "
            f"only. Allowed: {', '.join(sorted(self.ALLOWED))}.")

    def __setattr__(self, name: str, value) -> None:
        """Block anything being pushed back onto the backend."""
        if name.endswith("__backend"):
            super().__setattr__(name, value)
            return
        raise AttributeError(f"{name!r} cannot be set: the backend is read only.")

    def databases(self) -> tuple[str, str]:
        """``(where the QC data comes from, where the electrode list does)``.

        Two names because they need not be the same database, and which is
        which is exactly what somebody looking at these numbers has to know.
        """
        qc = str(self.database_name() or "")
        plant = str(self.plant_database_name() or "") or qc
        return qc, plant
