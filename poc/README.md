# POC harness

Run the deterministic offline proof with:

```powershell
powershell -NoProfile -File scripts\run-poc.ps1 -Provider fake
```

The fake provider is the default. It uses synthetic, noncopyrighted sample evidence and writes a canonical report plus checksum under `data/projects/poc`.

Real probes are manual only. VieNeu requires `-AllowLocalModel`; Qwen requires `-AllowPaidProvider`, a held budget authorization ID, a granted cloud consent ID, and an accepted prompt confirmation before one HTTP batch can run.
