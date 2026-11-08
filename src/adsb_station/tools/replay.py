"""Serve a recording over TCP exactly like readsb serves its SBS output on port 30003.

The ingest service cannot tell the difference between this and a real receiver, so the whole
pipeline can be run and tested without hardware, at real speed or faster.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

from ..recording import read_recording

log = logging.getLogger("adsb.replay")

# A client that falls this far behind is dropped instead of letting memory grow forever.
MAX_CLIENT_BUFFER = 1_000_000


class ReplayServer:
    def __init__(self, path: str | Path, speed: float = 1.0) -> None:
        if speed < 0:
            raise ValueError("speed must be >= 0 (0 means as fast as possible)")
        self.path = Path(path)
        self.speed = speed
        self.lines_sent = 0
        self._clients: set[asyncio.StreamWriter] = set()
        self._client_connected = asyncio.Event()
        self._server: asyncio.Server | None = None

    async def start(self, host: str = "127.0.0.1", port: int = 30003) -> int:
        """Start listening and return the bound port (useful with port=0)."""
        self._server = await asyncio.start_server(self._on_client, host, port)
        bound = self._server.sockets[0].getsockname()[1]
        log.info("serving %s on %s:%d", self.path, host, bound)
        return bound

    async def wait_for_client(self) -> None:
        await self._client_connected.wait()

    async def play(self) -> None:
        """Send the recording once, keeping the original spacing divided by `speed`."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        first_ts: float | None = None
        for count, record in enumerate(read_recording(self.path)):
            if first_ts is None:
                first_ts = record.ts
            if self.speed > 0:
                delay = started + (record.ts - first_ts) / self.speed - loop.time()
                if delay > 0.002:
                    await asyncio.sleep(delay)
            elif count % 500 == 0:
                await asyncio.sleep(0)  # let the clients' transports breathe
            self._broadcast((record.line + "\r\n").encode("ascii", errors="replace"))
        await self._drain_all()

    async def close(self) -> None:
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _broadcast(self, data: bytes) -> None:
        for writer in list(self._clients):
            if writer.is_closing():
                self._clients.discard(writer)
                continue
            if writer.transport.get_write_buffer_size() > MAX_CLIENT_BUFFER:
                log.warning("dropping slow client %s", writer.get_extra_info("peername"))
                self._clients.discard(writer)
                writer.close()
                continue
            writer.write(data)
        self.lines_sent += 1

    async def _drain_all(self) -> None:
        for writer in list(self._clients):
            try:
                await writer.drain()
            except ConnectionError:
                self._clients.discard(writer)

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log.info("client connected: %s", peer)
        self._clients.add(writer)
        self._client_connected.set()
        try:
            await reader.read()  # returns when the client disconnects
        except ConnectionError:
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            log.info("client disconnected: %s", peer)


async def _run(args: argparse.Namespace) -> None:
    server = ReplayServer(args.recording, speed=args.speed)
    await server.start(args.host, args.port)
    try:
        if not args.no_wait:
            log.info("waiting for a client before starting playback...")
            await server.wait_for_client()
        while True:
            await server.play()
            log.info("playback finished (%d lines sent)", server.lines_sent)
            if not args.loop:
                break
    finally:
        await server.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30003)
    parser.add_argument("--speed", type=float, default=1.0,
                        help="playback speed multiplier; 0 = as fast as possible")
    parser.add_argument("--loop", action="store_true", help="start again when finished")
    parser.add_argument("--no-wait", action="store_true",
                        help="start playing immediately instead of waiting for a client")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
