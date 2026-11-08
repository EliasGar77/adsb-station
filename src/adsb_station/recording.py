"""Recording file format: a raw SBS feed plus the time each line was received.

One line per received SBS line:

    <unix_seconds>\t<raw SBS line>

Lines starting with "#" are comments (used for metadata such as "# source: simulated").
Keeping receive times lets a recording be replayed later at its original pace, which makes
development and tests independent of live traffic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RecordedLine:
    ts: float
    line: str


def format_line(ts: float, line: str) -> str:
    return f"{ts:.3f}\t{line.rstrip()}\n"


def read_recording(path: str | Path) -> Iterator[RecordedLine]:
    with open(path, encoding="ascii", errors="replace") as handle:
        for number, raw in enumerate(handle, start=1):
            raw = raw.rstrip("\r\n")
            if not raw or raw.startswith("#"):
                continue
            ts_text, sep, line = raw.partition("\t")
            if not sep:
                raise ValueError(f"{path}:{number}: missing tab separator")
            try:
                ts = float(ts_text)
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: bad timestamp {ts_text!r}") from exc
            yield RecordedLine(ts, line)
