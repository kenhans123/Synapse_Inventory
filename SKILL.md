---
name: synapse-datamart-inventory
description: Read the definitions of stored procedures, functions, views, materialized views and Databricks mirror objects (Delta external tables / OPENROWSET views) in an Azure Synapse datamart — live from the SQL pool or from exported .sql scripts — and produce a formatted Excel inventory report with complexity ratings, dependencies, visual object lineage diagrams, migration waves, T-SQL pattern flags and Databricks or Fabric migration notes, plus an interactive HTML lineage explorer. Use for Synapse object inventories, migration assessments, discovery or sizing.
argument-hint: "<database or script folder> [--target databricks|fabric] [--client NAME]"
---

# Synapse Datamart Object Inventory

Produces `<Datamart>_Object_Inventory.xlsx` from a Synapse datamart in two steps:

1. `scripts/extract.py` — reads object definitions into `inventory.json`
2. `scripts/build_report.py` — analyses the T-SQL and writes the Excel workbook

Requirements: `pip install -r requirements.txt` (openpyxl; pyodbc + azure-identity for live mode) and
**ODBC Driver 18 for SQL Server** for live mode. VS Code / GitHub Copilot setup: `references/vscode-setup.md`.

Paths below are relative to this skill's folder (in VS Code: `.github/skills/synapse-datamart-inventory/`).
Run commands from the workspace root so `.env` is picked up; write outputs to `output/` there.

## Step 1 — Work out the source

Ask only what is missing:

| Source | When | Command |
|---|---|---|
| **Live** | User can reach the dedicated / serverless SQL endpoint (their machine, VPN, or a linked device shell) | `python scripts/extract.py live --test-connection` then `python scripts/extract.py live --out output/inventory.json` |
| **Folder** | User has scripts: SSDT/`.sqlproj`, `sqlpackage /a:Extract`, SSMS *Generate Scripts*, or a git repo | `python scripts/extract.py folder --path <dir> --out inventory.json` |

The cloud workspace usually cannot reach a customer Synapse endpoint (network allowlist, private endpoints, MFA).
If live access isn't possible from here, either run the scripts on the user's machine through the device shell,
or ask the user to upload a `.zip` of scripts (or run `extract.py live` themselves and upload `inventory.json`).

If the user gives a URL or connection string (endpoint `host/db`, JDBC, ADO.NET/ODBC, or Synapse Studio URL),
pass it as `--url "<value>"` (or `SYNAPSE_URL` in `.env`); only server and database are read from it.
Connection settings come from a `.env` in the workspace root (template: `.env.example`) — `SYNAPSE_SERVER`,
`SYNAPSE_DATABASE`, `SYNAPSE_AUTH`, optional `SYNAPSE_SCHEMAS` — or the matching CLI flags, which override it.
If `.env` is missing, ask the user for server and database only and pass them as flags.

Auth (`SYNAPSE_AUTH` / `--auth`): `token` (default — the user's `az login` / VS Code Azure sign-in via azure-identity),
`ActiveDirectoryInteractive` (Windows), `ActiveDirectoryServicePrincipal`, `SqlPassword`, or a full
`SYNAPSE_CONNECTION_STRING`. Never ask for, echo, or write passwords or secrets in chat; they belong in `.env`.

Always run `--test-connection` first. If it fails, use the troubleshooting table in `references/vscode-setup.md`.
If `view_definition_permission` is false, stop and tell the user the GRANT they need.

What live mode reads: `sys.objects` + `sys.sql_modules` / `OBJECT_DEFINITION` (procs, functions, views),
`sys.pdw_materialized_view_*` (materialized views), `sys.parameters`, `sys.sql_expression_dependencies`,
and `sys.external_tables` / `external_data_sources` / `external_file_formats` / `sys.columns`
(external table DDL is reconstructed). Catalog views missing on serverless pools are skipped and logged as warnings.

## Step 2 — Databricks mirror objects

An object is classed **Databricks Mirror** when it is an external table or view and any of these hold:
file format type `DELTA`; `OPENROWSET(... FORMAT='DELTA')`; or its name / schema / data source / location
matches `--mirror-pattern` (default `mirror|dbx|databricks|delta|unity|abfss://…(lakehouse|delta|uc)`).
Other external tables stay as **External Table**. The evidence is shown per object.

If the engagement uses a naming convention (e.g. schema `dbx_`, data source `eds_uc_gold`), pass it:
`--mirror-pattern "^dbx_|eds_uc_"`. When unsure, run with the default and show the user the Databricks Mirror
and External Tables sheets so they can confirm.

## Step 3 — Build the report

```bash
python scripts/build_report.py --in inventory.json --out "<Datamart>_Object_Inventory.xlsx" \
       --target databricks   # or: fabric  (changes migration notes only)
       [--client "CCEP"] [--no-definitions] [--lineage-depth 2] [--no-lineage-images] [--no-lineage]
```

Summary and "Referenced By" use live formulas; the workbook is flagged to calculate when Excel opens it.
If LibreOffice and an xlsx recalc script are available (e.g. Claude's `/mnt/skills/public/xlsx/scripts/recalc.py`),
run it and confirm 0 errors; otherwise skip — do not install LibreOffice just for this.

Use `--no-definitions` for very large datamarts (> ~300k lines of code) or when the user wants metadata only.

Outputs (same folder):
- `<name>.xlsx` — the workbook below
- `<name>_Lineage.html` — interactive lineage explorer: one self-contained file, works offline (no CDN), so it
  opens on locked-down client laptops. Search or click any object to see N hops upstream/downstream, filter by
  type, hide tables or procedure calls, view the whole datamart, zoom/pan, export the current view as SVG.
  Deep link: `…_Lineage.html#dm.usp_Load_FactSales`.

Lineage options: `--lineage-depth` (hops each way in the Excel diagrams, default 2; diagrams over 40 boxes are
trimmed automatically), `--max-diagrams` (default 400), `--no-lineage-images` (table only — use for 1,000+ objects
or when matplotlib is unavailable), `--no-lineage` (skip lineage entirely).

Delivering: always hand over both files. In VS Code, open the HTML with the browser (Live Preview / "Open with
default browser"). In Claude, send both files; if an Artifact tool is available, also publish the HTML so the user
gets a shareable private link — but only when the datamart is not confidential to the client; ask if unsure.

## Workbook layout

| Sheet | Content |
|---|---|
| Summary | Counts by type × rating (formulas), total lines, avg complexity, stacked chart, pattern usage, top 15 most complex |
| Inventory | One row per object: type, lines, score, rating, referenced-by count (formula), reads/writes/calls, features, migration notes |
| Stored Procedures | Parameters, reads, writes, procedure calls |
| Functions | Kind (scalar / inline TVF / multi-statement TVF), parameters, return type |
| Materialized Views | Distribution, base objects, aggregates |
| Views | Base objects, aggregation flag |
| Databricks Mirror | Implemented as (external table / view), location, data source, file format, evidence |
| External Tables | Non-mirror external tables (only when present) |
| Feature Matrix | Object × T-SQL pattern grid (cursor, dynamic SQL, CTAS, RENAME OBJECT, MERGE, temp tables, …) |
| Dependencies | Object → referenced object, operation (READ/WRITE/EXEC/CALL), resolved in datamart |
| Lineage | Per object: migration wave, direct upstream / downstream, transitive counts, circular-dependency flag; names link to diagrams |
| Lineage Diagrams | One diagram per object (bold border = the object) beside its upstream / downstream list |
| Definitions | Full source, one line per row with line numbers; names on other sheets hyperlink here |
| About | Method, scoring weights, rating bands, mirror rule, extraction warnings |

## Lineage rules

Every arrow reads **"is needed by"**, so left → right is build/load order:
`table → view/proc that reads it`, `proc → table it writes`, `function ⋯→ object that uses it` (dotted),
`called proc - - → calling proc` (dashed). Upstream = what an object depends on; downstream = what is impacted
if it changes. A proc that reads and writes the same table keeps the table to its right (read drawn as a loop back).

**Migration wave** = 1 + the highest wave among inventory objects it depends on (base tables don't count), so
wave 1 can be migrated first. Objects in a dependency cycle share a wave and are flagged "Circular Dependency".
Lineage is only as complete as the dependency parsing: names built in dynamic SQL are not seen.

## Scoring

`score = min(lines/25, 20) + Σ pattern weights + 0.5 × referenced objects (max 8)`;
Low ≤ 6 < Medium ≤ 14 < High ≤ 25 < Very High. Weights and migration notes live in `FEATURES` in
`scripts/analyze.py` — adjust there if the engagement has its own estimation model. Tell the user the
ratings are a triage aid, not an effort estimate.

## Checks before handing over

- Object counts in the extract output match what the user expects (ask if a type is unexpectedly zero).
- Recalc reports `"status": "success"` with 0 errors (when recalc is available).
- Open Summary + one type sheet (render or read back with `data_only=True`) to confirm values.
- Spot-check lineage for 2–3 objects you know (Lineage sheet and the HTML explorer) and mention any circular dependencies.
- Mention any extraction warnings and objects whose definition was not readable (encrypted / permissions).

## Test data

`assets/sample_sql/` holds a small Synapse datamart (CTAS+RENAME load proc, cursor proc, MERGE proc, scalar /
inline / multi-statement functions, views, a materialized view, Delta mirror external table and OPENROWSET view).
`python scripts/extract.py folder --path assets/sample_sql --datamart DM_Sales --out /tmp/inv.json`
