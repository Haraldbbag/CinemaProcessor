"""Simulated Dolby cinema processor: ASCII control port + web update feed.

    python3 tests/fake_dolby.py [cp750|cp850] [ascii_port] [http_port]

ASCII behaviour follows the CP750 manual (ch. 6.2): "<param> <value>" sets,
"<param> ?" queries, the answer is "<param> <value>", unknown parameters get
an empty line. CP850/CP950 use the same parameters without "cp750.".
The web feed mimics /prototype/TroutMockData?get_updates as captured on a
CP850 (HTTP Basic auth, JSON list of events).

Importing this module also registers a package stub so the integration's
protocol code can be imported without Home Assistant, plus a minimal
`aiohttp` stand-in if the real one is not installed.
"""

from __future__ import annotations

import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import pathlib
import re
import sys
import threading
import types

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "cinema_processor"
if "cinema_processor" not in sys.modules:
    pkg = types.ModuleType("cinema_processor")
    pkg.__path__ = [str(_PKG)]
    sys.modules["cinema_processor"] = pkg

try:  # pragma: no cover - depends on the environment
    import aiohttp  # noqa: F401
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "stubs"))

INPUTS = ["analog", "dig_1", "dig_2", "dig_3", "dig_4", "mic", "non_sync"]
MACROS = {1: "5.1 + Dolby Atmos", 2: "7.1 + Dolby Atmos", 6: "Pausenmusik"}


class State:
    def __init__(self, model: str = "cp850") -> None:
        self.model = model
        self.prefix = "cp750." if model == "cp750" else ""
        self.fader = 70
        self.mute = 0
        self.input_mode = "dig_1"
        self.macro = 6
        self.log: list[str] = []
        self.connections = 0
        self.events: list[dict] = []  # pending web events
        self.atmos_playing = False  # Atmos stream arrives from the server
        self.force_pcm = False  # "ignore Dolby Atmos" pressed

    @property
    def atmos_rendered(self) -> bool:
        return self.atmos_playing and not self.force_pcm

    def params(self) -> dict[str, str]:
        p = self.prefix
        out = {
            f"{p}sys.fader": str(self.fader),
            f"{p}sys.mute": str(self.mute),
            f"{p}sysinfo.version": "1.0.0.150183" if self.prefix else "4.1.2.3",
        }
        if self.model == "cp750":
            out["cp750.sys.input_mode"] = self.input_mode
        else:
            out["sys.macro_preset"] = str(self.macro)
            out["sys.macro_name"] = f'"{MACROS[self.macro]}"'
        return out


def handle(st: State, line: str) -> list[str]:
    st.log.append(line)
    text = line.strip()
    if not text:
        return []
    if text.lower() == "help":
        return list(st.params())
    if text.lower() == "status":
        return [f"{k} {v}" for k, v in st.params().items()]
    parts = text.split(None, 1)
    key = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""
    p = st.prefix
    if key == f"{p}ctrl.fader_delta":
        st.fader = max(0, min(100, st.fader + int(value)))
        return [f"{key} {value}"]
    params = st.params()
    if key not in params:
        return [""]
    if value == "?":
        return [f"{key} {params[key]}"]
    if key == f"{p}sys.fader":
        st.fader = max(0, min(100, int(value)))
    elif key == f"{p}sys.mute":
        st.mute = 1 if value in ("1", "on") else 0
    elif key == "cp750.sys.input_mode" and value in INPUTS:
        st.input_mode = value
    elif key == "sys.macro_preset" and value.isdigit() and int(value) in MACROS:
        if int(value) != st.macro and st.force_pcm:
            st.force_pcm = False  # the CP850 reverts "ignore Dolby Atmos" on macro change
            st.events.append({"id": "ForcePcmControlEvent_1066", "forcePCM": False})
        st.macro = int(value)
    return [f"{key} {st.params()[key]}"]


async def start_ascii(st: State, host: str, port: int) -> asyncio.base_events.Server:
    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        st.connections += 1
        writer.write(b"\xff\xfb\x01")  # a Telnet WILL ECHO, must be ignored
        try:
            while line := await reader.readline():
                text = line.decode("latin-1").strip("\r\n")
                if text.strip().lower() == "exit":
                    st.log.append(text)
                    break
                for reply in handle(st, text):
                    writer.write(reply.encode("latin-1") + b"\r\n")
                await writer.drain()
        finally:
            writer.close()

    return await asyncio.start_server(client, host, port)


def atmos_event(status: int, frame: int = 1) -> dict:
    playing = status == 1
    return {
        "id": "AtmosContentEvent_1015",
        "encrypted": False,
        "status": status,
        "sampleRate": 2,
        "frameRate": 24 if playing else 0,
        "uuid": "fed6e6df-f4cf-4e8f-aa2c-d5763222c78a" if playing else "n/a",
        "atmosVersion": 1,
        "reversionReason": 0,
        "frameNumber": frame,
        "watermarked": False,
    }


def start_http(st: State, host: str, port: int, user: str = "admin", password: str = "admin"):
    """Login as the CP850 web UI does it: multipart POST ?login -> sessionId=...;"""
    frame = [0]
    sessions: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            pass

        def _send(self, status: int, data: bytes, ctype: str = "text/html") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            if self.path.endswith("?send_message"):
                if self.headers.get("SessionId") not in sessions:
                    return self._send(401, b"")
                st.log.append(f"web send {body}")
                for event in json.loads(body):
                    if str(event.get("id", "")).startswith("ForcePcmControlEvent"):
                        was = st.atmos_rendered
                        st.force_pcm = bool(event["forcePCM"])
                        st.events.append({"id": "ForcePcmControlEvent_1066", "forcePCM": st.force_pcm})
                        if was and not st.atmos_rendered:
                            st.events.append(atmos_event(2))
                return self._send(200, b"")
            if not self.path.endswith("?login"):
                return self._send(404, b"")
            fields = dict(re.findall(r'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', body))
            st.log.append(f"web login {fields.get('userName')}")
            if fields.get("userName") == user and fields.get("password") == password and fields.get("clientId"):
                sid = base64.b16encode(fields["clientId"].encode()).decode()[:16]
                sessions.add(sid)
                return self._send(200, f"sessionId={sid};loggedInUserLevel=3;".encode(), "text/plain")
            return self._send(200, b"error=invalid;", "text/plain")

        def do_GET(self) -> None:  # noqa: N802
            if self.path.endswith("?product_status"):
                return self._send(200, b"shutdown_required=false;\nproduct=CP850;\n", "text/plain")
            if self.headers.get("SessionId") not in sessions:
                return self._send(401, b"")
            if self.path.endswith("?get_updates"):
                events = [{"id": "SpeakerMeterStateEvent_1005", "speakerIndex": 0,
                           "peakLevel": 20, "rmsLevel": 14}]
                if st.atmos_rendered:
                    frame[0] += 1
                    events.append(atmos_event(1, frame[0]))
                events += st.events
                st.events = []
                body = events
            else:
                return self._send(404, b"")
            self._send(200, json.dumps(body).encode())

    def expire_sessions() -> None:
        sessions.clear()

    server = ThreadingHTTPServer((host, port), Handler)
    server.expire_sessions = expire_sessions  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def _main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "cp850"
    ascii_port = int(sys.argv[2]) if len(sys.argv) > 2 else 61408
    http_port = int(sys.argv[3]) if len(sys.argv) > 3 else 8080
    st = State(model)
    server = await start_ascii(st, "0.0.0.0", ascii_port)
    start_http(st, "0.0.0.0", http_port)
    print(f"Fake Dolby {model}: ASCII {ascii_port}, web {http_port} (admin/admin)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
