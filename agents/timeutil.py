"""
Operator-local time for MINAS.

Operators state windows in their own wall-clock time ("18:00 to 22:00"), and
the LLM has no notion of "now" unless told. Everything that interprets a
timestamp goes through here so that:
  - the orchestrator's prompt carries the current date/time and offset,
  - a timestamp without an offset is read as MINAS_TZ (not UTC — Postgres'
    session zone, which silently turned 22:00 in Brazil into 19:00),
  - what reaches the database is always timezone-aware.

Env: MINAS_TZ (IANA name, default America/Sao_Paulo).
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_NAME = os.getenv("MINAS_TZ", "America/Sao_Paulo")
TZ = ZoneInfo(TZ_NAME)


def now() -> datetime:
    return datetime.now(TZ)


def parse_iso(value: str) -> datetime:
    """ISO-8601 -> aware datetime; a naive value is taken as MINAS_TZ local time.
    Raises ValueError on malformed input."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=TZ)


def prompt_header() -> str:
    """The block the orchestrator prepends to every intent."""
    # no concrete clock times in the example: an earlier "from 18h to 22h" /
    # "<today>T18:00" example was copied into intents that state no window
    n = now().replace(microsecond=0)
    return (
        "## Current date and time\n"
        f"{n.isoformat()} ({TZ_NAME}, {n.strftime('%A')})\n"
        "If the intent states a time window, resolve its relative dates/times "
        "(\"tonight\", \"tomorrow\") against this moment, in this time zone, and "
        "write window_start/window_end as ISO-8601 WITH the UTC offset "
        f"(YYYY-MM-DDTHH:MM:SS{n.strftime('%z')[:3]}:{n.strftime('%z')[3:]}). "
        "If the intent states no time window, omit window_start and window_end."
    )
