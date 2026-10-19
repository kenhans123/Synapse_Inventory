# Running the Synapse inventory skill in VS Code with GitHub Copilot Chat

One-time setup per laptop, then a 2-minute setup per datamart.

## 1. Prerequisites (once per machine)

| Item | Install |
|---|---|
| VS Code + **GitHub Copilot Chat** extension | Sign in with the GitHub account that has your Copilot licence |
| **Python 3.10+** and the **Python** extension | `python --version` |
| **ODBC Driver 18 for SQL Server** | Windows: MSI from Microsoft Learn ("Download ODBC Driver for SQL Server"). macOS: `brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release && brew install msodbcsql18`. Linux: Microsoft's apt/yum repo, package `msodbcsql18` |
| **Azure CLI** (recommended) | `az login` gives the scripts a token without any password |
| **SQL Server (mssql)** extension (optional) | Lets you browse the datamart and run queries inside VS Code |

## 2. Put the skill in the workspace

Create (or open) a folder per engagement, e.g. `ccep-synapse-discovery/`, and copy the skill so Copilot can find it:

```
ccep-synapse-discovery/
├─ .github/
│  └─ skills/
│     └─ synapse-datamart-inventory/      ← unzip the skill here
│        ├─ SKILL.md
│        ├─ scripts/  assets/  references/
│        ├─ requirements.txt
│        └─ .env.example
├─ .env                                   ← your connection settings (step 4)
├─ .gitignore                             ← add: .env  .venv/  *.xlsx  inventory*.json
└─ output/
```

VS Code Copilot loads skills from `.github/skills/`, `.claude/skills/` or `.agents/skills/` in the workspace,
or from `~/.copilot/skills/` for all workspaces. Folder name must match `name:` in SKILL.md
(`synapse-datamart-inventory`). Extra folders can be added with the `chat.agentSkillsLocations` setting.

## 3. Python environment

In the VS Code terminal at the workspace root:

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r .github/skills/synapse-datamart-inventory/requirements.txt
```

Then **Python: Select Interpreter → .venv** so Copilot's terminal uses it.

## 4. Connect to the Synapse datamart

### 4a. Find the endpoint
Azure portal → Synapse workspace → **Overview**:
- **Dedicated SQL endpoint**: `<workspace>.sql.azuresynapse.net` — dedicated pool datamarts (materialized views live here)
- **Serverless SQL endpoint**: `<workspace>-ondemand.sql.azuresynapse.net` — serverless/lake databases (external tables, OPENROWSET views)

The database name is the dedicated SQL pool name (or the serverless database name).

### 4b. Network and pool checks
- **Dedicated pool must be running** (not Paused) — a paused pool refuses connections.
- **Firewall**: Synapse workspace → Networking → add your client IP, *or* connect over the customer VPN if the
  workspace uses private endpoints / "public network access disabled". Port **1433** outbound must be open.
- **Permissions**: your Entra ID account needs to be a database user with **VIEW DEFINITION**
  (`GRANT VIEW DEFINITION TO [you@client.com];`). Read access to data is not required.

### 4c. Sign in and configure
```bash
az login --tenant <client-tenant-id-or-domain>     # use the identity that has access to the datamart
cp .github/skills/synapse-datamart-inventory/.env.example .env
```
Edit `.env`:
```
SYNAPSE_SERVER=ccep-syn-prod.sql.azuresynapse.net
SYNAPSE_DATABASE=dm_red
SYNAPSE_AUTH=token
# SYNAPSE_SCHEMAS=dm,rpt,mirror
```
`token` reuses your `az login` (or the VS Code Azure Account sign-in) — no password stored.
Alternatives: `ActiveDirectoryInteractive` (Windows browser pop-up), `ActiveDirectoryServicePrincipal`
(`SYNAPSE_USER=<appId>`, `SYNAPSE_PASSWORD=<secret>`), or `SqlPassword`. Keep `.env` out of git and never paste secrets into chat.

### 4d. Test
```bash
python .github/skills/synapse-datamart-inventory/scripts/extract.py live --test-connection
```
Expect `"connected": true`, `"view_definition_permission": true` and object counts.

### 4e. Connect with a URL in VS Code (SQL Server / mssql extension)

**Where to copy the URL from**
| Copy this | From |
|---|---|
| Endpoint URL `<workspace>.sql.azuresynapse.net` | Azure portal → Synapse workspace → Overview → *Dedicated SQL endpoint* (or *Serverless SQL endpoint*) |
| ADO.NET / ODBC / JDBC connection string | Azure portal → Synapse workspace → **SQL pools** → your pool → **Connection strings** |
| Synapse Studio URL `https://web.azuresynapse.net/...?workspace=...` | Browser address bar in Synapse Studio (gives the workspace only — add the database) |

**Option A — paste the connection string (quickest)**
1. Activity bar → **SQL Server** icon → **+ Add Connection** (or Command Palette `Ctrl+Shift+P` → *MS SQL: Add Connection*).
2. Choose input type **Connection String** and paste the ADO.NET string, e.g.
   `Server=tcp:ccep-syn.sql.azuresynapse.net,1433;Initial Catalog=dm_red;Encrypt=True;TrustServerCertificate=False;Authentication="Active Directory Interactive";`
   Remove any `Password=`/`User ID=` placeholder from the portal string when using Entra ID.
3. **Connect** → sign in with the account that has access → give the profile a name (e.g. `CCEP dm_red`).

**Option B — enter the URL as parameters**
1. **+ Add Connection** → input type **Parameters**.
2. Server name: `ccep-syn.sql.azuresynapse.net` (dedicated) or `ccep-syn-ondemand.sql.azuresynapse.net` (serverless). Port 1433 is the default.
3. Authentication type: **Microsoft Entra ID - Universal with MFA** → **Sign in** (pick the client tenant if prompted).
4. Database: the dedicated pool name (e.g. `dm_red`). Encrypt: **Mandatory**, Trust server certificate: **off**.
5. **Connect**. The datamart appears in the SQL Server explorer: expand *Programmability → Stored Procedures / Functions*,
   *Views*, and *Tables → External Tables*; right-click an object → **Script as Create** to read its definition.

**Option C — Browse Azure**: choose **Browse Azure**, sign in, then pick subscription → resource group → workspace → database.

**Use the same URL for the skill.** Put any of the shapes above in `.env` instead of server/database:
```
SYNAPSE_URL=Server=tcp:ccep-syn.sql.azuresynapse.net,1433;Initial Catalog=dm_red;Encrypt=True;
```
or pass it directly:
```bash
python .github/skills/synapse-datamart-inventory/scripts/extract.py live --url "ccep-syn.sql.azuresynapse.net/dm_red" --test-connection
```
Accepted: `host`, `host/db`, `https://host/db`, `sqlserver://host:1433/db`, JDBC (`jdbc:sqlserver://host:1433;database=db;…`),
ADO.NET/ODBC (`Server=tcp:host,1433;Initial Catalog=db;…`), and the Synapse Studio URL (add `--database`).
Only the server and database are taken from it; authentication still comes from `SYNAPSE_AUTH` (default: your `az login`),
so a password inside a pasted string is ignored — don't paste strings containing passwords into chat.

## 4f. Locked-down / production machine — export, then build in VS Code with no installs

Only **Python 3.8+** is needed to build the Excel report — no `pip install`, no ODBC driver, no extensions
(the Excel library is bundled in `scripts/_vendor`). Check in the VS Code terminal: `python --version`
(or `py --version`). No Python at all? Build on another machine, or attach the export in Claude.

**1. Export the definitions (your existing SQL connection)**
1. SQL Server panel → right-click the datamart connection → **New Query**.
2. Paste all of `assets/export_definitions.sql` and run it (`Ctrl+Shift+E`). It is a plain catalog read
   (one row per object) and should return in seconds; if even `SELECT COUNT(*) FROM sys.sql_modules`
   is slow, the pool is busy or queued — try a quieter time.
3. Results grid → icons on its right edge → **Save as JSON** (CSV / Excel also work), e.g. `bidw_definitions.json`.
   Save it inside the opened folder so VS Code can see it.

**2. Build the Excel report — pick one**
- **VS Code task (no typing):** open the saved JSON in the editor → **Terminal → Run Task…** →
  *Synapse inventory: build Excel from the open export file* → choose the Python command, client and target.
  Works when the skill folder is the opened folder (tasks are in `.vscode/tasks.json`).
- **Terminal:**
  ```bash
  python scripts/inventory.py bidw_definitions.json --client CCEP --target databricks --open
  ```
- **Copilot Chat (Agent mode):** *"Use the synapse-datamart-inventory skill to build the Excel report from
  bidw_definitions.json for client CCEP. Don't install any packages."* — approve the terminal command when asked.

Output: `output/bidw/bidw_Object_Inventory.xlsx` and `output/bidw/bidw_Object_Inventory_Lineage.html`
(`--open` opens the lineage explorer in the browser). The datamart name comes from the file name; override with `--datamart`.

The query is read-only (catalog views only, no table data). It also returns each definition's true length, and the
build checks every definition against it.

**If the build says "definition(s) cut short by the results grid"** (only possible for very long procedures):
1. Open `assets/export_long_definitions.sql`, paste the listed names into `@names`
   (e.g. `N'dm.usp_Load_FactSales, dm.usp_Big_Merge'`), run it, **Save as JSON** → `bidw_long_definitions.json`.
2. Rebuild with both files:
   `python scripts/inventory.py bidw_definitions.json bidw_long_definitions.json --datamart bidw --client CCEP`
   (with the VS Code task, put both files in one folder and give the folder path).
Cut-short objects are also flagged in the report's Migration Notes until fixed.

Without matplotlib the *Lineage Diagrams* sheet is skipped; the HTML explorer has every diagram.

**Option B — SQL Database Project (dedicated pool only)**
Needs the **SQL Database Projects** extension (usually installed alongside the mssql extension).
1. SQL Server panel → right-click the connection/database → **Create Project from Database**
   (or Command Palette → *Database Projects: Create Project From Database*).
2. Choose a folder, project name, and folder structure **Schema/Object Type**; target platform is detected as
   *Azure Synapse Dedicated SQL Pool*.
3. Build from the project folder (same machine is fine — no installs needed):
   ```bash
   python scripts/inventory.py ./dm_red_project --datamart dm_red --client CCEP
   ```
Serverless pools can't be imported as a project — use Option A there.
Without the extension, Visual Studio (SSDT) → SQL Server Database Project → **Import → Database** gives the same result.

## 5. Run the skill from Copilot Chat

1. Open Copilot Chat, switch the mode picker to **Agent** (skills and terminal commands need agent mode).
2. Ask, for example:
   - `Build the Synapse datamart object inventory for dm_red, target Databricks, client CCEP`
   - `/synapse-datamart-inventory dm_red --target fabric` (skills appear as slash commands)
   - Offline: `Run the synapse inventory skill on the scripts in ./sql_export`
3. Copilot loads SKILL.md, runs `extract.py` then `build_report.py` in the terminal (approve the commands when prompted),
   and writes `output/<Datamart>_Object_Inventory.xlsx`.
4. Open the workbook in Excel — formulas calculate on open.
5. Open `output/<Datamart>_Object_Inventory_Lineage.html` in a browser (right-click → *Open with Live Preview* or
   *Reveal in File Explorer* → double-click) for the interactive lineage explorer. It needs no internet.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Data source name not found` / `Can't open lib 'ODBC Driver 18…'` | Install msodbcsql18, or set `SYNAPSE_ODBC_DRIVER=ODBC Driver 17 for SQL Server` |
| Login timeout / TCP error 10060 | Pool paused, firewall IP missing, or need VPN / private endpoint |
| `Login failed for user '<token-identified principal>'` | `az login` to the wrong tenant, or your account isn't a user in that database |
| `DefaultAzureCredential failed` | Run `az login`; or use `SYNAPSE_AUTH=ActiveDirectoryInteractive` on Windows |
| Definitions empty / "not readable" | Missing VIEW DEFINITION, or object is `WITH ENCRYPTION` |
| Materialized views show as views | Serverless endpoint (no MVs) or `sys.pdw_materialized_view_*` not visible — see About sheet warnings |
| Export query runs for minutes | Use the current `export_definitions.sql` (one row per object). Uncomment the schema filter for very large datamarts; check the pool isn't paused/queued |
| `'python' is not recognized` / opens Microsoft Store | Use `py` instead (Windows launcher); in the task pick `py` |
| `f-string` / `SyntaxError` on an old Python | Python 3.8+ is required — check `python --version` |
| No Lineage Diagrams sheet | matplotlib isn't installed — expected; use the `_Lineage.html` explorer |
| Copilot doesn't use the skill | Agent mode on? Folder name = `name:` in SKILL.md? Ask by name: "use the synapse-datamart-inventory skill" |
| No network to the client at all | Export scripts (SSMS Generate Scripts / `sqlpackage /a:Extract` / git repo) and use folder mode |
