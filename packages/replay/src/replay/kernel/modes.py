"""The three modes the gate can be in.

Everything in the system is a consequence of these. Live executes and records.
Replay serves the log and executes nothing. Fork serves the log up to a chosen
step, substitutes one recorded result there, and goes live from that point on.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel

from replay_events import Result


class LiveMode(BaseModel):
    kind: Literal["live"] = "live"


class ReplayMode(BaseModel):
    kind: Literal["replay"] = "replay"
    # Serve effects 0..up_to from the log. Execute nothing.
    up_to: int


class ForkMode(BaseModel):
    kind: Literal["fork"] = "fork"
    # The effect sequence whose recorded result is replaced.
    at: int
    mutation: Result


Mode = Union[LiveMode, ReplayMode, ForkMode]
