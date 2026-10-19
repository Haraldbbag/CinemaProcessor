"""Shared data structures and errors (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProcessorError(Exception):
    """Connection or protocol problem."""


class ProcessorCommandError(ProcessorError):
    """The processor did not accept a command or parameter."""


class ProcessorTimeout(ProcessorError):
    """The processor did not answer (but the connection is up)."""


class ProcessorAuthError(ProcessorError):
    """Login to the web interface was rejected."""


@dataclass(slots=True)
class ProcessorInfo:
    manufacturer: str
    model: str
    version: str | None = None
    serial: str | None = None


@dataclass(slots=True)
class ProcessorData:
    fader: float | None = None  # 0.0 .. 10.0
    mute: bool | None = None
    source: str | None = None  # input (CP750) or macro name (CP850/CP950)
    sources: list[str] = field(default_factory=list)
    atmos: bool | None = None  # Dolby Atmos is really being rendered
    atmos_attrs: dict[str, Any] = field(default_factory=dict)
    force_pcm: bool | None = None  # "ignore Dolby Atmos" active (CP850/CP950A)
    web_error: str | None = None
