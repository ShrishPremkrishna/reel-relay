# Standalone media engine

A fresh installation uses the builtin FFmpeg engine. It does not require a parent checkout, another editing application or a preexisting media library. An optional legacy adapter exists for compatible external projects; it is not the default installation path.

## Tools and local transcription

Install FFmpeg/FFprobe with libass, libx264, drawtext and loudnorm support. Media execution requires `sandbox-exec` on macOS or bubblewrap with working unprivileged user namespaces on Linux. Missing isolation fails closed. macOS media execution has been exercised; end-to-end Linux media verification remains outstanding.

Install local transcription explicitly from **Setup & accounts → Workspace services**. The advanced CLI equivalents are:

```sh
./relay setup-media
./relay setup-media --download-model small
```

The first command checks readiness. The download command creates a separate environment under the private data directory, installs the shipped hash-locked dependencies, downloads the selected model and saves its paths. Runtime transcription uses local files with networking denied. Model weights and their licenses are separate from the application.

For manually provisioned transcription, paths must resolve within the data directory. A trusted transcript can be imported without installing a transcriber:

```json
{
  "words": [
    {"start": 0.25, "end": 0.65, "text": "Example"},
    {"start": 0.66, "end": 1.1, "text": "line"}
  ]
}
```

Words use chronological source-time seconds bounded by the actual media. An optional `source_sha256` must match. Keep provenance; imported or generated text is not a human listening review.

## Capture length and finished length

Capture pre/post seconds preserve context around the trigger. Editing minimum/maximum seconds constrain the finished cut. These are separate settings: making a longer source window does not force a longer final video.

Saved editing settings cover caption enablement/group size, layout, sound/reaction toggles, maximum effect counts, sound gain and style notes. Settings freeze when a job is selected and are recorded in each export's `editing.json` with a hash. Later UI changes do not alter an already-selected job or its prior exports.

## Framing, captions and effects

The vertical renderer supports containing the full image, a focused crop and a split composition when evidence identifies an appropriate region. Structured plans can also describe bounded inspected panels. It validates source-time cuts and normalized source regions before rendering.

Captions use timed words and preserve an SRT sidecar. The words-per-caption setting is a maximum. Disabling captions turns off burned speech subtitles while retaining timed metadata and SRT for audio verification; the hook title is separate. Optional corrections are bounded by source-time intervals and retain the original evidence and reason. Literal subtitle text must not inject style commands. A disabled caption preference is different from a missing expected caption artifact.

Effects use output-time positions. Sound effects have bounded gain and fades; reaction overlays have explicit size, location and source offset. Only catalogued files can be selected. Overlay audio is not automatically mixed into the source. An empty catalog is valid.

## Asset catalog

Store asset files and their JSON catalog within the data directory. The default catalog location is `engine/assets/catalog.json`; explicit `media.asset_catalog` and `media.asset_root` settings are available for managed installations.

A record carries a path, kind, title, tags, duration, provenance, rights/use notes, attribution and content hash. For example:

```json
[
  {
    "path": "accents/soft-pop.wav",
    "kind": "sfx",
    "title": "Soft punctuation pop",
    "tags": ["small surprise"],
    "duration": 0.45,
    "rights": "Original recording owned by the contributor",
    "source_url": "",
    "attribution": "",
    "usage_notes": "Use quietly after speech",
    "sha256": "REPLACE_WITH_ACTUAL_SHA256"
  }
]
```

This illustrates the format; no sound file is bundled. Verify file type, hash and reuse terms before adding an asset. Keep credits with exports. Models and third-party media are not covered by the application's MIT license.

## Evidence and adapters

The `MediaBackend` protocol separates staging, catalog access, preparation, rendering and transcript import. Selection evidence binds source identity, duration, dimensions, audio, frames, transcript and available assets. Review evidence binds the rendered export and its technical checks.

The builtin engine retains source media and prior exports. Technical QA includes decoding and audio measurements. Local rendered-speech checks compare recovered speech with expected captions; optional media-perception observations are separate. An uncertain model interpretation is not a verified listening result.

The legacy `reel-studio` adapter requires an explicitly configured external installation and may contain less portable historical paths. Configurable caption burning/grouping is a builtin-engine feature; the legacy adapter rejects unsupported non-default caption controls. Its presence is compatibility support, not a requirement or cross-platform guarantee.
