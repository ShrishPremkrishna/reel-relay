"""Reel Relay's standalone, offline media engine.

No parent workspace, effect pack, or paid provider is needed by this module.
Transcription requires a separately installed local faster-whisper runtime/model.
"""

from .backend import BuiltinStudio, MediaBackend

__all__ = ["BuiltinStudio", "MediaBackend"]
