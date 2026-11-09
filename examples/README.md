# Compact real-result example

`fd4_review.zip` is a read-only display subset of FD4 MA run
`run_7c72e4af2bfe__started_2026-10-20T15_49_08`.
It contains the twenty recorded department snapshots, action/receipt files,
end-of-day observer states, metadata and cumulative decision quality.
It excludes model conversations, executable checkpoints, host paths, full
projections and full transaction exports. It is not a resumable experiment or a
replacement for the full research archive. Missing artifact families remain
unavailable in this example. Raw business messages retain their archived language.

Archive SHA-256: `d59661fcc4571c7a0c1b70040a6db8eb8f897c27985e98f94518cb8d00b20b59`. Compressed size: 698,398 bytes.
The internal publication manifest records each published member's hash.

Import into the default visitor publication root:

```bash
.venv/bin/python scripts/import_result_example.py
```

Then start the viewer using `bash scripts/start_integrated_app.sh`. The example
does not require model credentials. Its headline numerical checks are net profit
margin 79.91%, gross profit margin 95.88%, four completed recruitment batches,
six onboarded people, order completion 48/50, quantity fill 3160/4720 and backlog
1560 product units at R19. The sample's source metadata hash is retained in the
publication manifest; derived display files do not rewrite the original archive.
