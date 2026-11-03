"""Sampling: how long the good and bad stretches run, and what that means
for how often QC should test.

Pure ``DataFrame`` in, numbers out. No Qt and no database, so every figure
the QC Sampling tab prints can be checked directly.

A copy of the same maths as the standalone ``qc_sampling_script``, which
stays pointed at the production database. The two are deliberately
separate: this one reads whatever the app is connected to.
"""
