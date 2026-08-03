# Phase 1.7.1 — Integration Center / Gemini

## Source of truth

`IntegrationManager` is the single operational source of truth for both the Socket.IO UI and Veronica's Gemini Live tools. Integration status is not stored in personal memory or conversation state.

Gemini is the only registered integration in this phase. Future providers can be added through `IntegrationManager.register()` and will automatically appear in the dynamic UI list.

## Status criteria

- `not_configured`: `GEMINI_API_KEY` is absent.
- `checking`: a real, lightweight `models.get` health check is running.
- `active`: the health check succeeded or Gemini Live connected successfully.
- `error`: a configured health check or Gemini Live connection failed.
- `inactive`: configured but not yet checked or connected in this process.

The health check creates a separate short-lived SDK client and retrieves model metadata. It does not disconnect, replace or send content through the active Gemini Live session.

## Models

- Main/current Veronica model: `gemini-2.5-flash-native-audio-preview-12-2025`
- Gemini Live model: `models/gemini-2.5-flash-native-audio-preview-12-2025`

Both values come from the current `backend/ada.py` configuration.

## API key security

The key is accepted only by the backend Socket.IO handler, written atomically to the project `.env`, and retained in backend process memory. Public registry, detail, test and event payloads expose only `api_key_configured`. Errors redact the active key. The key is never written to memory data, telemetry, prompts or logs and is never echoed to React. An already running Live client needs a voice restart to use a newly saved key.

## Telemetry

Operational records live in `data/telemetry/gemini_usage.json`, separate from `data/memory`. Writes use temp-file replacement and the store retains at most 5,000 records.

The Google GenAI SDK 2.16.0 exposes Gemini Live usage through `LiveServerMessage.usage_metadata`. The integration records the last metadata provided for a completed Live turn. Missing metadata produces `null` token fields; token counts are never inferred. Health checks record requests, latency and success but leave token fields unavailable.

CAD and Web Agent clients were audited but intentionally not modified because this phase explicitly excludes changes to CAD and Web Agent. Their token usage therefore remains unavailable in this delivery.

No model pricing table exists in the project, so estimated cost is reported as unavailable rather than guessed.
