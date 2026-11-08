"""Record a live SBS feed (e.g. readsb on port 30003) to a recording file for later replay."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

from ..recording import format_line

log = logging.getLogger("adsb.record")


async def record(host: str, port: int, out: Path, duration_s: float | None) -> int:
    reader, writer = await asyncio.open_connection(host, port)
    log.info("recording %s:%d -> %s", host, port, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if duration_s is None else time.monotonic() + duration_s
    count = 0
    with open(out, "w", encoding="ascii", newline="\n") as handle:
        handle.write(f"# source: live {host}:{port}, started {datetime.now().isoformat()}\n")
        try:
            while deadline is None or time.monotonic() < deadline:
                timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout)
                except TimeoutError:
                    break
                if not raw:
                    log.warning("feed closed the connection")
                    break
                handle.write(format_line(time.time(), raw.decode("ascii", errors="replace")))
                count += 1
                if count % 1000 == 0:
                    handle.flush()
                    log.info("%d lines recorded", count)
        finally:
            writer.close()
    return count


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30003)
    parser.add_argument("--minutes", type=float, default=None, help="stop after N minutes")
    parser.add_argument("--out", type=Path,
                        default=Path(f"data/recordings/{datetime.now():%Y%m%d-%H%M%S}.rec"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    duration = None if args.minutes is None else args.minutes * 60
    try:
        count = asyncio.run(record(args.host, args.port, args.out, duration))
    except KeyboardInterrupt:
        print("stopped")
        return
    print(f"recorded {count} lines to {args.out}")


if __name__ == "__main__":
    main()
