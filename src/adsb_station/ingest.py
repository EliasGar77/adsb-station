"""Ingest service: SBS feed (TCP) -> parser -> tracker -> SQLite.

    readsb / replay  --TCP 30003-->  [read lines] -> parse_line -> Tracker.update
                                                                      |
                                       Store.write  <-- batch every second (worker thread)

Design notes:
- Reconnects with exponential backoff, so the receiver can restart without killing ingest.
- Writes are batched (one transaction per second instead of one per message) and run in a
  worker thread so slow disk I/O never stalls reading the socket.
- Every message is stamped with the receive time from `clock`, which tests can replace.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .sbs import SbsParseError, parse_line
from .store import AircraftRow, Store
from .tracker import Aircraft, PositionFix, Tracker

log = logging.getLogger("adsb.ingest")

MAX_PENDING_FIXES = 5_000  # flush early if a burst arrives between periodic flushes
MAX_BACKOFF_S = 30.0


@dataclass
class IngestStats:
    lines: int = 0
    messages: int = 0
    positions: int = 0
    parse_errors: int = 0
    connections: int = 0


class Ingestor:
    def __init__(
        self,
        store: Store,
        tracker: Tracker | None = None,
        *,
        flush_interval_s: float = 1.0,
        stats_interval_s: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.tracker = tracker or Tracker()
        self.flush_interval_s = flush_interval_s
        self.stats_interval_s = stats_interval_s
        self.clock = clock
        self.stats = IngestStats()
        self._pending: list[PositionFix] = []
        self._dirty: dict[str, Aircraft] = {}

    # --- message handling (pure, synchronous: easy to test) -------------------------------

    def handle_line(self, line: str) -> None:
        self.stats.lines += 1
        try:
            message = parse_line(line)
        except SbsParseError as exc:
            self.stats.parse_errors += 1
            if self.stats.parse_errors <= 10:
                log.debug("skipping malformed line: %s", exc)
            return
        if message is None:
            return
        self.stats.messages += 1
        aircraft, fix = self.tracker.update(message, self.clock())
        self._dirty[aircraft.icao] = aircraft
        if fix is not None:
            self.stats.positions += 1
            self._pending.append(fix)

    def _take_batch(self) -> tuple[list[AircraftRow], list[PositionFix]]:
        # Snapshot in the event-loop thread, so the writer thread never sees live objects.
        rows = [(a.icao, a.first_seen, a.last_seen, a.callsign) for a in self._dirty.values()]
        fixes = self._pending
        self._dirty = {}
        self._pending = []
        return rows, fixes

    async def flush(self) -> None:
        rows, fixes = self._take_batch()
        if rows or fixes:
            await asyncio.to_thread(self.store.write, rows, fixes)

    def flush_sync(self) -> None:
        rows, fixes = self._take_batch()
        if rows or fixes:
            self.store.write(rows, fixes)

    # --- networking -----------------------------------------------------------------------

    async def run(self, host: str, port: int) -> None:
        """Run until cancelled. Pending data is flushed on the way out."""
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._connection_loop(host, port))
                group.create_task(self._housekeeping())
        finally:
            self.flush_sync()

    async def _connection_loop(self, host: str, port: int) -> None:
        backoff = 1.0
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except OSError as exc:
                log.warning("cannot connect to %s:%d (%s); retrying in %.0fs",
                            host, port, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue
            backoff = 1.0
            self.stats.connections += 1
            log.info("connected to %s:%d", host, port)
            try:
                await self._consume(reader)
                log.warning("feed closed the connection")
            except ConnectionError as exc:
                log.warning("connection lost: %s", exc)
            finally:
                writer.close()
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()
            await asyncio.sleep(backoff)

    async def _consume(self, reader: asyncio.StreamReader) -> None:
        while raw := await reader.readline():
            self.handle_line(raw.decode("ascii", errors="replace"))
            if len(self._pending) >= MAX_PENDING_FIXES:
                await self.flush()

    async def _housekeeping(self) -> None:
        last_stats = self.clock()
        last_lines = 0
        while True:
            await asyncio.sleep(self.flush_interval_s)
            await self.flush()
            now = self.clock()
            expired = self.tracker.expire(now)
            if expired:
                log.debug("expired %d aircraft", len(expired))
            if now - last_stats >= self.stats_interval_s:
                rate = (self.stats.lines - last_lines) / (now - last_stats)
                log.info("%.1f msg/s | tracking %d aircraft | %d positions stored | "
                         "%d parse errors", rate, len(self.tracker), self.stats.positions,
                         self.stats.parse_errors)
                last_stats, last_lines = now, self.stats.lines


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest an SBS feed into SQLite.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30003)
    parser.add_argument("--db", type=Path, default=Path("data/adsb.sqlite3"))
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="seconds without messages before an aircraft is forgotten")
    parser.add_argument("--stats-interval", type=float, default=30.0)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    store = Store(args.db)
    ingestor = Ingestor(store, Tracker(args.timeout), stats_interval_s=args.stats_interval)
    try:
        asyncio.run(ingestor.run(args.host, args.port))
    except KeyboardInterrupt:
        log.info("stopped: %d lines, %d positions stored", ingestor.stats.lines,
                 ingestor.stats.positions)
    finally:
        store.close()


if __name__ == "__main__":
    main()
