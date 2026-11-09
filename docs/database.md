# Archives, runtime databases, and optional business storage

[Architecture](architecture.md) · [Artifact guide](interface_and_artifacts.md#where-the-records-live) · [Deployment](public_deployment.md)

## Storage roles

| Store | Contents and purpose | Default/local location |
| --- | --- | --- |
| Private filesystem run archive | Configuration, observations, actions/results, exchange records, projections, and private checkpoints | `agent/workspace_jobs/` |
| Public result copy | Reviewed inspection files with a publication manifest and hashes | `agent/public_results/runs/` |
| Operations SQLite | Jobs, status, run/configuration records, and enabled projection mirrors | `agent/runtime/operations.sqlite` on a fresh integrated/public startup |
| Batch records | Serial schedule, item ownership/status, and scheduler logs | `agent/workspace_jobs/_batches/` |
| Optional SQLAlchemy ORM | Enterprise, product, and order business models | `local_orm.sqlite`, or the configured `DATABASE_URL` |

These stores serve different interfaces. Initializing ORM tables does not create operations jobs, populate the results browser, or import a run archive. Publishing an archive creates a derived inspection copy rather than an execution checkpoint.

## Integrated operations storage

The startup script points `SIMULATION_OPERATIONS_SQLITE_PATH` and `SIMULATION_SQL_MIRROR_PATH` at the same selected local database. Fresh installations use the runtime folder. If the legacy `agent/workspace_jobs/_operations/supplychain_integrated.sqlite` exists, the integrated startup retains it unless overridden.

| Setting | Effect |
| --- | --- |
| `SUPPLY_CHAIN_RUNTIME_ROOT` | Writable runtime folder, including default operations DB, logs, caches, and quarantine records |
| `SIMULATION_OPERATIONS_SQLITE_PATH` | Explicit operations repository path |
| `SIMULATION_SQL_MIRROR_PATH` | Explicit SQLite projection mirror path; integrated startup gives this override precedence when choosing the shared DB |
| `SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT` | Root of published `runs/` copies available to visitors |

For example, to keep public data and mutable runtime files outside the source checkout:

```bash
SUPPLY_CHAIN_PLATFORM_MODE=visitor \
SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT=/data/supplychain/public \
SUPPLY_CHAIN_RUNTIME_ROOT=/data/supplychain/runtime \
  bash scripts/start_integrated_app.sh
```

An enabled integration storage profile can mirror projections to PostgreSQL through `SIMULATION_POSTGRES_DSN`. That projection interface is separate from the optional ORM `DATABASE_URL`. Local visitor browsing and the default setup do not require PostgreSQL. The integration profiles and mirror interfaces are in [config/integration_profiles.py](../config/integration_profiles.py) and [persistence/sql_mirror_runtime.py](../persistence/sql_mirror_runtime.py).

## Optional ORM tables

[database/](../database/) retains SQLAlchemy enterprise, product, and order models plus repository/session helpers. The default engine uses `local_orm.sqlite` at the project root. `DATABASE_URL` selects an alternative SQLAlchemy backend.

Initialize these tables only when using that ORM integration:

```bash
.venv/bin/python -c 'from database.db_manager import init_db; init_db()'
```

[db_manager.py](../database/db_manager.py) provides the session context and table initialization; [repository.py](../database/repository.py) provides business-record access.

## Backups and transfer

For a browse-only deployment, transfer reviewed publication copies and preserve their manifests. For experiment recovery, preserve the private raw run, its sealed complete-round checkpoint, matching configuration/source identities, and the relevant job/batch records.

Stop writers before taking a consistent database/filesystem backup, or use a database-aware snapshot procedure. Copying only a database file cannot recreate missing per-firm action files or private checkpoints. Runtime caches and downloaded ZIP caches can be regenerated.

Generated stores, databases, and private diagnostics are ignored by Git. Source-controlled scenario templates, portable baseline configuration files, tests, and checksums remain part of the code release.
