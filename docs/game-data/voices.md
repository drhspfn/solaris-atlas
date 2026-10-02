# Voice import sample

The worker can pin and download all four Windows voice languages (en, ja, ko, zh), including character packages. `plan-voices` reads the exported Kuro public config and a local configuration crypto file containing base64 `key` and `iv` values. Keep this file outside version control. The resulting plan contains public archive paths, sizes and hashes only.

`download-voices PLAN --workspace DIRECTORY` downloads from official game CDNs. Optional `--installed-game DIRECTORY` reuses installed files only after size and SHA-1 validation; production does not require an installed game. Completed files are reused when restarting the same plan.

Base archives are downloaded from the package version route (for example 3.7.0), whereas patches use their resource version. Locally, base archives remain in the `Base` directory used by the game.

For a bounded sample, extract an exact PlotAudio filename prefix with FModelCLI from the completed voice mirror, using the existing client key file. Then publish extracted files:

```powershell
uv run --project packages/worker --env-file packages/worker/.env python -m wuwa_story_worker.voice_import EXTRACTED_DIRECTORY --decoder VGMSTREAM_EXECUTABLE --asset-version 3.7.0
```

The importer requires all four languages for each extracted filename, matches existing VoiceReference records exactly, decodes WAV, and registers original WEM and playable WAV files through the existing content-addressed storage service. FileReference metadata stores language, duration and asset version. Repeated publication updates the same voice/language/build reference under a database advisory lock.

The initial local sample contains ten voice references (40 tracks); eight of these appear at the beginning of quest 139000025. Audio comes from the 3.7.0 client, while the selected text snapshot can be 1.0.0. Historical recording identity is not verified. Unmatched extracted filenames are not attached to dialogue.

This is a CLI sample flow. Automatic extraction, queue orchestration, full-catalog coverage, compressed browser variants and partial-download resume still need production work. Do not treat the sample as a finished automated production pipeline.
