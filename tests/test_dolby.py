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
    from cinema_processor.web_client import button_macros
    assert button_macros([{"id": "AtmosContentEvent_1015"}]) is None
    assert button_macros(fake_dolby.macro_config_events()) == fake_dolby.MACROS


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
        # own input names: only the named inputs, switching by name
        named = DolbyDriver("127.0.0.1", 16408, "cp750", inputs_text="dig_1=Kinoserver\nNonSync=Pausenmusik\nfoo=bar")
        named.ascii = d.ascii
        assert named.inputs == {"dig_1": "Kinoserver", "non_sync": "Pausenmusik"}
        await named.select_source("Pausenmusik")
        await named.update(data)
        assert data.source == "Pausenmusik" and data.sources == ["Kinoserver", "Pausenmusik"], data
        await named.select_source("Digital 2")  # default name of a hidden input still works
        await named.update(data)
        assert data.source == "Digital 2", data  # current input stays visible
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
    d = DolbyDriver("127.0.0.1", 16409, "cp850", "admin", "admin", "1=5.1 + Dolby Atmos")
    d.web = WebClient("127.0.0.1", "admin", "admin", port=18080)
    try:
        info = await d.device_info()
        assert info.model == "CP850", info
        await d.check_web()
        data = ProcessorData()
        await d.update(data)
        assert data.fader == 7.0 and data.source == "Pausenmusik", data
        # with web login: the button macros come from the processor (no ProTools,
        # no undefined slots); the option text still overrides a name
        assert data.sources == ["5.1 + Dolby Atmos", "7.1 + Dolby Atmos", "Pausenmusik"], data.sources
        assert "web required macros" in st.log
        await d.update(data)
        assert st.log.count("web required macros") == 1  # not on every poll
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
        # "ignore Dolby Atmos" for the QC: Atmos playing -> PCM forced -> back
        st.atmos_playing = True
        await d.update(data)
        assert data.atmos is True and data.force_pcm is False, data
        await d.set_force_pcm(True)
        assert 'web send [{"id":"ForcePcmControlEvent_1066", "forcePCM":true}]' in st.log, st.log
        await d.update(data)
        assert data.atmos is False and data.force_pcm is True, data
        assert data.atmos_attrs["pcm_forced"] is True and data.atmos_attrs["last_change"]
        await d.set_force_pcm(False)
        await d.update(data)
        assert data.atmos is True and data.force_pcm is False, data
        # macro change reverts it on the processor; the switch follows
        await d.set_force_pcm(True)
        await d.update(data)
        assert data.force_pcm is True
        await d.select_source("Pausenmusik")
        await d.update(data)
        assert data.force_pcm is False and data.source == "Pausenmusik", data
        await d.select_source("5.1 + Dolby Atmos")
        st.atmos_playing = False
        st.events.append(fake_dolby.atmos_event(2))
        await d.update(data)
        assert data.atmos is False

        # session expires (e.g. processor rebooted): logs in again by itself
        http.expire_sessions()
        await d.update(data)
        assert data.atmos is False and data.web_error is None
        assert sum(1 for x in st.log if x.startswith("web login")) == 2, st.log

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


async def _silent_fader_step() -> None:
    """CP950: fader_delta is executed but not echoed. Steps must be quick and
    must not count as timeouts (which would drop the connection)."""
    st = fake_dolby.State("cp950")
    st.echo_fader_delta = False
    server = await fake_dolby.start_ascii(st, "127.0.0.1", 16411)
    d = DolbyDriver("127.0.0.1", 16411, "cp950")
    try:
        data = ProcessorData()
        await d.update(data)
        assert data.fader == 7.0
        start = time.monotonic()
        for _ in range(5):
            await d.step_fader(1)
        elapsed = time.monotonic() - start
        assert elapsed < 5 * 0.6, elapsed  # was 5 x 2 s before the fix
        await d.update(data)
        assert data.fader == 7.5, data
        for _ in range(2):
            await d.step_fader(-1)
        await d.update(data)
        assert data.fader == 7.3, data
        assert st.connections == 1, st.connections  # never dropped
    finally:
        await d.close()
        server.close()
        await server.wait_closed()


def main() -> None:
    test_parsing()
    test_tracker()
    asyncio.run(_cp750())
    asyncio.run(_cp850())
    asyncio.run(_reconnect())
    asyncio.run(_silent_fader_step())
    print("all tests passed")


if __name__ == "__main__":
    main()
