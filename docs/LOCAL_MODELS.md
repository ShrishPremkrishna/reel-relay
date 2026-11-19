# Models and evidence

Reel Relay separates editorial decisions, local transcription and optional media perception. Their outputs are different evidence types. A provider being configured does not prove its model is suitable, available or accurate.

## Editorial providers

Configure the provider and exact model in **Setup & accounts → Your editing engine**.

| Provider | Requirements | Evidence destination |
| --- | --- | --- |
| External/manual | A separate editor supplies structured decisions and reviews | The editor selected by the administrator |
| Ollama | An installed local vision-capable model | Configured loopback Ollama service |
| Installed Codex | An installed, signed-in CLI and its selected model | The CLI's configured provider |
| Gemini | A chosen model, API key and explicit cloud-evidence permission | Google's configured API |
| Custom command | A trusted executable argument array | Administrator-selected program |

The application does not install an editorial model silently and does not require a particular paid provider or cloud model. Local providers still need enough memory and throughput for transcripts and sampled frames. Begin with one worker and measure representative jobs, including holds and retries.

For a custom adapter, use the structured contract in the implementation and [API documentation](API.md); do not treat model text as a shell command. Media and transcript instructions are untrusted content, not runtime instructions.

## Local transcription

Choose and install a supported Whisper model from **Workspace services**. Installation uses a separate environment under the data directory and an explicit download step. Ordinary transcription loads local files with network access denied.

Supported installation choices are `tiny`, `base`, `small`, `medium` and `large-v3-turbo`. Larger model files and longer audio require more resources; model size alone does not predict speed or accuracy on a particular machine. Choose based on measured caption accuracy and queue latency. Check the model's separate license before redistribution.

A trusted timed-word transcript can also be imported through the advanced CLI. Transcription supplies source-time word boundaries. It is not a listening review, and agreement between models does not prove a disputed word.

## Routine export checks

**Steady volume** uses local rendered speech recovery, caption comparison, audio measurements and decoding. It does not require a generative native-audio model.

When the frozen edit legitimately contains no speech captions, the application can use the technical-audio path. Missing or corrupted expected captions must not silently become a no-speech exception. These checks do not claim a human listened or that a joke will perform well.

## Additional media perception

**More selective** can use a separate perception provider. Ollama perception requires a local model advertising native audio input; image support is insufficient. The adapter checks model identity and binds observations to the supplied media. Local endpoints must stay on loopback, and cloud-backed local-model tags are not a substitute for local processing.

The local audio adapter uses bounded application-defined windows. It records source/window identity, model identity, observations and uncertainty. Model prose is not used as exact caption timing or evidence of unseen visual actions. Stale, mismatched or malformed evidence cannot clear a review.

Gemini perception requires a key and explicit cloud-upload permission. Supplying a key alone is insufficient. Editorial cloud permission and additional-media permission are separate settings. The encrypted key can be shared by the configured Gemini adapters; it is never returned to the settings page.

## Evaluate before increasing volume

Measure full jobs, including model loading, frame/transcript processing, rendering and final checks. Track peak memory, latency, incorrect captions, unsupported claims, unnecessary holds and revisions. Do not extrapolate from one diagnostic inference.

Native-audio models can hallucinate speech or overconfidently infer context. Frame reviewers can miss intermediate motion. Keep uncertainty visible and compare source playback with the finished export when tuning policy.

No private hardware benchmark, transcript or development receipt is part of the public release. Platform-independent throughput and model-quality claims have not been established.
