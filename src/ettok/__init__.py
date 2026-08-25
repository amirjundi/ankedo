"""Client for the Ettok platform.

The whole integration surface is `Ettok.net/docs/AGENT_CONTRACT.md`, mirrored into
`specs/` when it changes. HTTPS only, bearer key with the `hate_speech_scan` scope.
"""

from src.ettok.client import (
    AgentKeyRejected,
    EttokClient,
    EttokError,
    EttokUnavailable,
)

__all__ = ["EttokClient", "EttokError", "AgentKeyRejected", "EttokUnavailable"]
