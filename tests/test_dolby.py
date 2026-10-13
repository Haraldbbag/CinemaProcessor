"""Protocol and driver tests against the simulator (no Home Assistant needed).

    python3 tests/test_dolby.py
"""

import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fake_dolby  # noqa: E402  (registers the package stub)
from cinema_processor import ascii_client  # noqa: E402
from cinema_processor.ascii_client import AsciiClient, parse_line  # noqa: E402
from cinema_processor.common import (  # noqa: E402
    ProcessorAuthError,
    ProcessorCommandError,
    ProcessorData,
)
from cinema_processor.dolby import (  # noqa: E402
    DolbyDriver,
    parse_fader,
    parse_macro_list,
)
from cinema_processor.web_client import AtmosTracker, WebClient  # noqa: E402

# Faster tests: no 3 s spacing between connections to the same host.
ascii_client.CONNECT_SPACING = 0.0


def test_parsing() -> None:
    assert parse_line("cp750.sys.fader 70") == ("cp750.sys.fader", "70")
    assert parse_line("> sys.macro_name \"5.1 + Dolby Atmos\"") == (
        "sys.macro_name",
        '"5.1 + Dolby Atmos"',
    )
    assert parse_line("") is None
    assert parse_fader("70") == 7.0 and parse_fader("4.0") == 4.0 and parse_fader("5") == 0.5
    assert parse_macro_list("1=5.1 + Dolby Atmos\n2: 7.1\n\n6 = Pausenmusik") == {
        1: "5.1 + Dolby Atmos",
        2: "7.1",
        6: "Pausenmusik",
    }
    assert parse_macro_list("A\nB") == {1: "A", 2: "B"}


def test_tracker() -> None:
    t = AtmosTracker()
    assert not t.active(0)
    t.feed([{"id": "SpeakerMeterStateEvent_1005"}], 0)
    assert not t.active(0)
    t.feed([fake_dolby.atmos_event(1)], 1)
    assert t.active(2)
    assert t.attributes()["frame_rate"] == 24 and t.attributes()["uuid"]
    assert not t.active(1 + 60)  # stale: events stopped without a status 2
    t.feed([fake_dolby.atmos_event(2)], 3)
    assert not t.active(3)
    assert t.attributes()["uuid"] is None and t.attributes()["status_code"] == 2


async def _cp750() -> None:
    st = fake_dolby.State("cp750")
    server = await fake_dolby.start_ascii(st, "127.0.0.1", 16408)
    d = DolbyDriver("127.0.0.1", 16408, "cp750")
    try:
        info = await d.device_info()
        assert info.model == "CP750" and info.version == "1.0.0.150183", info
        data = ProcessorData()
        await d.update(data)
        assert (data.fader, data.mute, data.source) == (7.0, False, "Digital 1"), data
        assert "NonSync" in data.sources and data.atmos is None
        await d.set_fader(4.3)
        await d.set_mute(True)
        await d.select_source("Digital 2")
        await d.step_fader(-1)
        await d.update(data)
        assert (data.fader, data.mute, data.source) == (4.2, True, "Digital 2"), data
        assert "cp750.sys.input_mode dig_2" in st.log
        try:
            await d.ascii.query("cp750.sys.nonsense")
        except ProcessorCommandError:
            pass
        else:
            raise AssertionError("expected unsupported parameter")
        # still usable after the unknown parameter
        assert parse_fader(await d.ascii.query("cp750.sys.fader")) == 4.2
        replies = await d.send_raw("help")
        assert "cp750.sys.fader" in replies, replies
        assert st.connections == 1, st.connections  # one persistent connection
    finally:
        await d.close()
        server.close()
        await server.wait_closed()
    assert st.log[-1] == "exit"


async def _cp850() -> None:
    st = fake_dolby.State("cp850")
    server = await fake_dolby.start_ascii(st, "127.0.0.1", 16409)
    http = fake_dolby.start_http(st, "127.0.0.1", 18080)
    d = DolbyDriver("127.0.0.1", 16409, "cp850", "admin", "secret", "1=5.1 + Dolby Atmos")
    d.web = WebClient("127.0.0.1", "admin", "secret", port=18080)
    try:
        info = await d.device_info()
        assert info.model == "CP850", info
        await d.check_web()
        data = ProcessorData()
        await d.update(data)
        assert data.fader == 7.0 and data.source == "Pausenmusik", data
        assert data.sources == ["5.1 + Dolby Atmos", "Pausenmusik"], data.sources
        assert d.learned_changed and d.learned_macros == {6: "Pausenmusik"}
        assert data.atmos is False and data.web_error is None

        # Atmos starts: events keep coming while it renders
        await d.select_source("5.1 + Dolby Atmos")
        assert "sys.macro_preset 1" in st.log
        st.atmos_playing = True
        await d.update(data)
        assert data.source == "5.1 + Dolby Atmos" and data.atmos is True, data
        assert data.atmos_attrs["frame_rate"] == 24
        await d.update(data)
        assert data.atmos is True

        # Atmos stops (e.g. AES problem): one status 2 event, then silence
        st.atmos_playing = False
        st.events.append(fake_dolby.atmos_event(2))
        await d.update(data)
        assert data.atmos is False and data.atmos_attrs["status_code"] == 2
        await d.update(data)
        assert data.atmos is False

        # macro names learned from the device; unknown name refused
        assert d.learned_macros[1] == "5.1 + Dolby Atmos"
        try:
            await d.select_source("Gibt es nicht")
        except ProcessorCommandError:
            pass
        else:
            raise AssertionError("expected unknown macro")

        await d.set_mute(True)
        await d.step_fader(5)
        await d.update(data)
        assert data.mute is True and data.fader == 7.5, data
        assert "sys.mute 1" in st.log and "ctrl.fader_delta 5" in st.log

        # wrong web password: Atmos unknown, rest keeps working
        bad = WebClient("127.0.0.1", "admin", "wrong", port=18080)
        try:
            await bad.poll()
        except ProcessorAuthError:
            pass
        else:
            raise AssertionError("expected auth error")
        d.web = bad
        await d.update(data)
        assert data.atmos is None and data.web_error and data.fader == 7.5
    finally:
        await d.close()
        server.close()
        await server.wait_closed()
        http.shutdown()


async def _reconnect() -> None:
    st = fake_dolby.State("cp850")
    server = await fake_dolby.start_ascii(st, "127.0.0.1", 16410)
    c = AsciiClient("127.0.0.1", 16410)
    old = ascii_client.RECONNECT_BACKOFF
    ascii_client.RECONNECT_BACKOFF = 0.5
    try:
        assert await c.query("sys.fader") == "70"
        assert c._writer is not None
        c._writer.close()  # connection drops
        await asyncio.sleep(0.1)
        server.close()
        await server.wait_closed()
        server = await fake_dolby.start_ascii(st, "127.0.0.1", 16410)
        await asyncio.sleep(0.6)
        assert await c.query("sys.fader") == "70"
        assert st.connections == 2
        # pacing: three commands take at least 2 x COMMAND_SPACING
        start = time.monotonic()
        for _ in range(3):
            await c.query("sys.mute")
        assert time.monotonic() - start >= 2 * ascii_client.COMMAND_SPACING - 0.01
    finally:
        ascii_client.RECONNECT_BACKOFF = old
        await c.close()
        server.close()
        await server.wait_closed()


def main() -> None:
    test_parsing()
    test_tracker()
    asyncio.run(_cp750())
    asyncio.run(_cp850())
    asyncio.run(_reconnect())
    print("all tests passed")


if __name__ == "__main__":
    main()
