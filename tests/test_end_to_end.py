"""Whole pipeline: simulated recording -> replay server (TCP) -> ingest -> SQLite."""

import asyncio
import contextlib

from adsb_station.ingest import Ingestor
from adsb_station.recording import format_line, read_recording
from adsb_station.sbs import parse_line
from adsb_station.store import Store
from adsb_station.tools.replay import ReplayServer
from adsb_station.tools.simulate import simulate


def test_replayed_recording_ends_up_in_the_database(tmp_path):
    recording = tmp_path / "sim.rec"
    events = simulate(minutes=2, aircraft=10, seed=3)
    recording.write_text("# source: simulated\n" + "".join(format_line(*e) for e in events))

    messages = [parse_line(r.line) for r in read_recording(recording)]
    expected_lines = len(messages)
    expected_positions = sum(1 for m in messages if m.has_position)
    expected_aircraft = {m.icao for m in messages}

    store = Store(tmp_path / "adsb.sqlite3")
    ingestor = Ingestor(store, flush_interval_s=0.05)

    async def scenario():
        server = ReplayServer(recording, speed=0)  # as fast as possible
        port = await server.start("127.0.0.1", 0)
        task = asyncio.create_task(ingestor.run("127.0.0.1", port))
        await asyncio.wait_for(server.wait_for_client(), timeout=5)
        await server.play()
        async with asyncio.timeout(10):
            while ingestor.stats.lines < expected_lines:
                await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await server.close()

    asyncio.run(scenario())

    assert ingestor.stats.parse_errors == 0
    assert store.query("SELECT count(*) FROM positions") == [(expected_positions,)]
    stored_aircraft = {icao for (icao,) in store.query("SELECT icao FROM aircraft")}
    assert stored_aircraft == expected_aircraft
    callsigns = {c for (c,) in store.query("SELECT callsign FROM aircraft") if c}
    assert callsigns and all(c.startswith("SIM") for c in callsigns)
