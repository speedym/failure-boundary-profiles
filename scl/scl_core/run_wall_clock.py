"""A run's wall-clock time, read at the scoring stage from the log the raw bundle holds for it (F94,
2026-10-17: the paper's "roughly 230 hours on a single GPU" becomes a pipeline number).

Two readings, one per backend, the difference forced by what the simulators' logs carry
(adapters/PARITY.md G37):
  between_stamps  the seconds between the first and the last timestamp in a log. CARLA: the
                  leaderboard's run log beside each result record stamps every line it writes, from the
                  evaluator's first line to the watchdog's last; a retried run's attempts all lie between
                  them, so the value is what the run cost, not what its last attempt took.
  elapsed_mark    the seconds a counter line states. Isaac Sim: the Kit log of a cell prefixes every line
                  with the seconds since the app started and writes "Simulation App Shutting Down" last;
                  its ISO timestamps sit on warning lines only, so a first-to-last pair would end at the
                  last warning, long before the shutdown.
Each returns the measured seconds, or null with the case that produced it (principle 14: a log without
its stamps is a named case, never a default; the scorer counts such rows in its stamp beside the
method). The line patterns are the adapters'; this module holds the parsing and the null cases once
(principle 11). Stdlib only: the scorers run under the backends' own environments.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

MEASURED = "measured"
NO_LOG = "no_log"                              # no log beside the run's record
NO_STAMPS = "no_stamps"                        # a log without a timestamp
ONE_STAMP = "one_stamp"                        # a single timestamp: no interval to read
STAMPS_OUT_OF_ORDER = "stamps_out_of_order"    # the last timestamp precedes the first
NO_MARK = "no_mark"                            # the counter line is absent (a cell that never reached it)
CASES = (MEASURED, NO_LOG, NO_STAMPS, ONE_STAMP, STAMPS_OUT_OF_ORDER, NO_MARK)
FIELDS = ("wall_clock_s", "wall_clock_case")   # the two annex fields every scorer writes


def result(seconds, case: str) -> dict:
    """The two fields: the seconds (a number exactly when the case is measured, else null) and the case."""
    if case not in CASES:
        raise ValueError(f"wall-clock case must be one of {CASES}, got {case!r}")
    if (case == MEASURED) != (seconds is not None):
        raise ValueError(f"wall_clock_s is a number exactly when the case is {MEASURED!r}, got {seconds!r} with {case!r}")
    return {"wall_clock_s": None if seconds is None else round(float(seconds), 3), "wall_clock_case": case}


def between_stamps(text: str, stamp_re, parse=datetime.fromisoformat) -> dict:
    """The seconds between the first and the last match of `stamp_re` in `text`, each parsed by `parse`."""
    stamps = stamp_re.findall(text)
    if not stamps:
        return result(None, NO_STAMPS)
    if len(stamps) < 2:
        return result(None, ONE_STAMP)
    seconds = (parse(stamps[-1]) - parse(stamps[0])).total_seconds()
    if seconds < 0:
        return result(None, STAMPS_OUT_OF_ORDER)
    return result(seconds, MEASURED)


def elapsed_mark(text: str, mark_re) -> dict:
    """The seconds the last match of `mark_re` states (its one group): the counter the log keeps itself."""
    marks = mark_re.findall(text)
    if not marks:
        return result(None, NO_MARK)
    return result(float(marks[-1]), MEASURED)


def read_log(path, reader) -> dict:
    """`reader` (text -> result) over the log at `path`; the no-log case when there is none."""
    path = Path(path)
    if not path.is_file():
        return result(None, NO_LOG)
    return reader(path.read_text(errors="replace"))
