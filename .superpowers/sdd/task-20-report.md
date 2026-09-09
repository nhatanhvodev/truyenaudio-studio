# Task 20 / A01 report

Status: DONE (code path verified via contract/fake; live smoke NOT_RUN)

## Delivered / verified

- VieNeu local TTS bridge is already implemented and pinned:
  - `backend/app/providers/vieneu.py`: `VieNeuTtsAdapter` with `default_sample_rate = 44_100`, `provider_version = "local-onnx-int8"`, output WAV validated for sample rate / channels / sample width / duration + content sha256, redacted subprocess errors. Sample rate is locked (no silent resample assumption); C08's 44.1 kHz master contract matches this pin.
  - `backend/app/modules/voices/catalog.py`: manifest schema `truyenaudio-studio.voice-presets.v1` requiring id/name/provider/model/locale/license/cost_tier/sample_rate/model_path/license_snapshot_path/model_sha256/license_snapshot_sha256/model_verified/license_verified/poc_passed/settings/pronunciation; path confinement rejects absolute/`..`; the catalog refuses activation without verified model+license snapshots (`ModelLicenseUnverified`), marks local provider unavailable when the model is missing, and never falls back to cloud.
  - `voice-presets.manifest.json` fixture carries `vieneu-vi-int8` preset wiring.
- Acceptance mapping: manifest/model/voice/license pin ✓; sample-rate/capability probe ✓; catalog unavailable without model/license ✓; no auto model download; no fake=live claim (fake tests labeled).

## Residual / NOT_RUN

- Live invocation/probe/playback and real listening checks are `NOT_RUN`: no local engine/model/license is installed on this machine (environment input, per plan §8). Nothing in this task ran a paid cloud call or downloaded a model.
- The adapter drives the local engine through a validated subprocess/CLI wrapper rather than the Python API; contract/fake boundary is what the fixtures prove — live smoke must confirm the wrapper still matches the pinned engine before A02 "available" claims.

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/providers/test_local_tts_contract.py backend/tests/speech -q` — 26 passed (manifest/license activation, catalog prefer-vieneu-after-verified / piper-fallback / unavailable-without-model, common preview estimate, preview cache key).
- Backend full suite (repo root): 617 passed (recorded after J01 round 3; no backend code changed by this close).
- No provider/cloud/live call added.
