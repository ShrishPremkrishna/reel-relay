"""Standalone subprocess entry, staged inside the artifact sandbox before execution.

This file intentionally imports no Reel Relay code, account configuration or network client.
"""

import argparse
import json
import os
import wave
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="")
    args = parser.parse_args()
    import numpy as np
    from faster_whisper import WhisperModel

    # FFmpeg already decoded the untrusted container inside the media sandbox.
    # Feed its canonical PCM to ASR directly: a second container decoder is not
    # necessary, and this keeps runtime behavior independent of PyAV ABI changes.
    with wave.open(args.audio, "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (
            1,
            2,
            16000,
            "NONE",
        ):
            raise ValueError("ASR accepts only the engine's 16 kHz mono PCM audio")
        if stream.getnframes() > 16000 * 7200:
            raise ValueError("ASR audio exceeds the two-hour media limit")
        audio = (
            np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        )
    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
        cpu_threads=min(4, os.cpu_count() or 1),
        local_files_only=True,
    )
    segments, info = model.transcribe(
        audio,
        language=args.language or None,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    words, rows = [], []
    for segment in segments:
        rows.append({"start": segment.start, "end": segment.end, "text": segment.text.strip()})
        words.extend(
            {"start": word.start, "end": word.end, "text": word.word.strip(), "probability": word.probability}
            for word in segment.words or []
            if word.word.strip()
        )
    result = {"backend": "faster-whisper", "language": info.language, "words": words, "segments": rows}
    output = Path(args.output)
    temporary = output.with_suffix(".partial.json")
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    temporary.replace(output)


if __name__ == "__main__":
    main()
