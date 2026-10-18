#!/usr/bin/env python3
"""
Extract object definitions from a Synapse datamart into inventory JSON.

Two sources:
  live   - connect to a Synapse dedicated (or serverless) SQL pool with pyodbc
  folder - read exported .sql scripts (SSDT project, sqlpackage /a:Extract, SSMS "Generate Scripts", git repo)

Object types captured:
  Stored Procedure, Function, View, Materialized View, External Table, Databricks Mirror

"Databricks Mirror" = an external table / view / OPENROWSET that reads Delta data written by
Databricks, or any object whose name, data source or location matches --mirror-pattern.

Usage:
  python extract.py live   --server myws.sql.azuresynapse.net --database dm_sales --out inventory.json
  python extract.py folder --path ./sql_scripts --out inventory.json
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402,F401  (bundled openpyxl for reading .xlsx exports)

DEFAULT_MIRROR_PATTERN = r"mirror|dbx|databricks|delta|unity|abfss://[^'\"]*(lakehouse|delta|uc)"

TYPE_MAP = {
    "P": "Stored Procedure",
    "PC": "Stored Procedure",
    "FN": "Function",
    "IF": "Function",
    "TF": "Function",
    "FS": "Function",
    "FT": "Function",
    "V": "View",
}
FUNC_KIND = {"FN": "Scalar", "IF": "Inline TVF", "TF": "Multi-statement TVF",
             "FS": "CLR Scalar", "FT": "CLR TVF"}


# --------------------------------------------------------------------------- live
def _load_dotenv():
    """Read KEY=VALUE pairs from a .env next to the workspace root or cwd (no extra dependency)."""
    for path in (os.path.join(os.getcwd(), ".env"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")):
        if os.path.isfile(path):
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _access_token_attr():
    """Entra ID token from the current Azure login (az login / VS Code Azure sign-in / managed identity)."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError:
        sys.exit("azure-identity is not installed. Run: pip install azure-identity  "
                 "(or use --auth ActiveDirectoryInteractive on Windows)")
    import struct
    token = DefaultAzureCredential(exclude_interactive_browser_credential=False) \
        .get_token("https://database.windows.net/.default").token
    raw = token.encode("utf-16-le")
    return {1256: struct.pack(f"<I{len(raw)}s", len(raw), raw)}  # SQL_COPT_SS_ACCESS_TOKEN


def parse_url(url):
    """Accept any of the URL / connection-string shapes people copy from the Azure portal or Synapse Studio.

    Returns (server, database); database may be None.
      myws.sql.azuresynapse.net[/db]            sqlserver://myws.sql.azuresynapse.net/db
      https://myws.sql.azuresynapse.net/db      jdbc:sqlserver://myws.sql.azuresynapse.net:1433;database=db;...
      Server=tcp:myws.sql.azuresynapse.net,1433;Initial Catalog=db;...   (ADO.NET / ODBC)
      https://web.azuresynapse.net/...?workspace=/subscriptions/.../workspaces/myws   (Synapse Studio)
    """
    from urllib.parse import unquote, urlparse, parse_qs
    u = unquote(url.strip().strip('"').strip("'"))
    if "web.azuresynapse.net" in u:
        ws = parse_qs(urlparse(u).query).get("workspace", [""])[0]
        m = re.search(r"/workspaces/([^/?&]+)", ws or u)
        if not m:
            raise ValueError("Synapse Studio URL has no workspace=... parameter")
        return f"{m.group(1)}.sql.azuresynapse.net", None
    kv = dict((k.strip().lower(), v.strip()) for k, v in
              (p.split("=", 1) for p in re.split(r";", re.sub(r"^jdbc:sqlserver://[^;]*", "", u)) if "=" in p))
    db = kv.get("database") or kv.get("initial catalog") or kv.get("databasename")
    m = re.match(r"jdbc:sqlserver://([^:;/]+)", u, re.I)
    if m:
        return m.group(1), db
    srv = kv.get("server") or kv.get("data source") or kv.get("address")
    if srv:
        return re.sub(r"^tcp:", "", srv, flags=re.I).split(",")[0].split(":")[0], db
    m = re.match(r"(?:[a-z]+://)?([^/:;?\s]+)(?::\d+)?(?:/([^/?;\s]+))?", u, re.I)
    if not m:
        raise ValueError(f"Could not read a server name from: {url}")
    return m.group(1), db or m.group(2)


def _connect(args):
    try:
        import pyodbc  # noqa
    except ImportError:
        sys.exit("pyodbc is not installed. Run: pip install pyodbc  (and install 'ODBC Driver 18 for SQL Server')")
    if args.connection_string:
        return pyodbc.connect(args.connection_string)
    cs = (f"Driver={{{args.driver}}};Server=tcp:{args.server},1433;Database={args.database};"
          "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;")
    auth = args.auth or "token"
    if auth.lower() == "token":
        return pyodbc.connect(cs, attrs_before=_access_token_attr())
    if args.user:
        cs += f"Uid={args.user};Pwd={args.password or os.environ.get('SYNAPSE_PASSWORD', '')};"
        if auth.lower() != "sqlpassword":
            cs += f"Authentication={auth};"
    else:
        cs += f"Authentication={auth};"
    return pyodbc.connect(cs)


def test_connection(args):
    conn = _connect(args)
    cur = conn.cursor()

    def one(sql):
        try:
            cur.execute(sql)
            return cur.fetchone()
        except Exception as e:  # function not supported on this pool type
            return ("n/a: " + str(e).splitlines()[0][:80],)

    db, who = one("SELECT DB_NAME(), SUSER_SNAME()")
    ver = one("SELECT @@VERSION")[0]
    can_view = one("SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION')")[0]
    cur.execute("SELECT RTRIM(o.type) AS t, COUNT(*) AS n, SUM(CASE WHEN m.definition IS NULL THEN 1 ELSE 0 END) AS hidden "
                "FROM sys.objects o LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id "
                "WHERE o.is_ms_shipped = 0 AND RTRIM(o.type) IN ('P','FN','IF','TF','V') GROUP BY RTRIM(o.type)")
    rows = cur.fetchall()
    counts = {TYPE_MAP.get(t, t) + (f" ({FUNC_KIND[t]})" if t in FUNC_KIND else ""): n for t, n, _ in rows}
    hidden = sum(h or 0 for *_, h in rows)
    conn.close()
    print(json.dumps({"connected": True, "database": db, "login": who,
                      "view_definition_permission": can_view, "definitions_not_readable": hidden,
                      "engine": str(ver).splitlines()[0].strip()[:100], "object_counts": counts}, indent=1))
    if hidden:
        print(f"WARNING: {hidden} definitions are not readable. Ask a DBA for: GRANT VIEW DEFINITION TO [{who}];")


def _rows(cur, sql):
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _try_rows(cur, sql, warnings, label):
    try:
        return _rows(cur, sql)
    except Exception as e:  # catalog view not present on this pool type
        warnings.append(f"{label}: skipped ({str(e).splitlines()[0][:160]})")
        return []


def _iso(v):
    return v.isoformat(sep=" ", timespec="seconds") if isinstance(v, dt.datetime) else (str(v) if v else None)


def extract_live(args):
    conn = _connect(args)
    cur = conn.cursor()
    warnings = []
    schema_filter = ""
    if args.schemas:
        names = ",".join("'" + s.strip().replace("'", "''") + "'" for s in args.schemas.split(","))
        schema_filter = f" AND s.name IN ({names})"

    modules = _rows(cur, f"""
        SELECT s.name AS schema_name, o.name AS object_name, o.object_id, RTRIM(o.type) AS type_code,
               o.create_date, o.modify_date,
               m.definition AS definition
        FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
        WHERE o.is_ms_shipped = 0 AND RTRIM(o.type) IN ('P','PC','FN','IF','TF','FS','FT','V'){schema_filter}
        ORDER BY s.name, o.name""")

    # materialized views (dedicated pool only)
    mv_ids = set()
    for sql in ("SELECT object_id FROM sys.pdw_materialized_view_distribution_properties",
                "SELECT DISTINCT object_id FROM sys.pdw_materialized_view_mappings"):
        for r in _try_rows(cur, sql, warnings, "materialized view catalog"):
            mv_ids.add(r["object_id"])

    params = {}
    for r in _try_rows(cur, """
        SELECT p.object_id, p.name, ty.name AS type_name, p.max_length, p.precision,
               p.scale, p.is_output, p.parameter_id
        FROM sys.parameters p JOIN sys.types ty ON ty.user_type_id = p.user_type_id
        ORDER BY p.object_id, p.parameter_id""", warnings, "parameters"):
        if not r["name"]:
            continue  # return value of scalar function
        t = r["type_name"]
        if t in ("varchar", "char", "varbinary", "binary"):
            t += f"({'max' if r['max_length'] == -1 else r['max_length']})"
        elif t in ("nvarchar", "nchar"):
            t += f"({'max' if r['max_length'] == -1 else r['max_length'] // 2})"
        elif t in ("decimal", "numeric"):
            t += f"({r['precision']},{r['scale']})"
        params.setdefault(r["object_id"], []).append(f"{r['name']} {t}{' OUTPUT' if r['is_output'] else ''}")

    deps = {}
    for r in _try_rows(cur, """
        SELECT d.referencing_id, COALESCE(d.referenced_schema_name,'') AS ref_schema, d.referenced_entity_name AS ref_name,
               d.referenced_database_name AS ref_db
        FROM sys.sql_expression_dependencies d""", warnings, "sql_expression_dependencies"):
        name = ".".join(x for x in (r["ref_db"], r["ref_schema"], r["ref_name"]) if x)
        deps.setdefault(r["referencing_id"], set()).add(name)

    objects = []
    for r in modules:
        code = r["type_code"]
        otype = TYPE_MAP.get(code, code)
        definition = r["definition"] or ""
        if code == "V" and (r["object_id"] in mv_ids or re.match(r"\s*CREATE\s+MATERIALIZED\s+VIEW", definition, re.I)):
            otype = "Materialized View"
        objects.append({
            "schema": r["schema_name"], "name": r["object_name"], "object_type": otype,
            "type_code": code, "function_kind": FUNC_KIND.get(code),
            "created": _iso(r["create_date"]), "modified": _iso(r["modify_date"]),
            "definition": definition,
            "definition_available": bool(r["definition"]),
            "parameters": params.get(r["object_id"]),
            "catalog_dependencies": sorted(deps.get(r["object_id"], [])) or None,
            "source": "sys.sql_modules",
        })

    # external tables -> External Table / Databricks Mirror
    ext = _try_rows(cur, f"""
        SELECT s.name AS schema_name, t.name AS object_name, t.object_id, t.create_date, t.modify_date,
               et.location, ds.name AS data_source, ds.location AS data_source_location, ds.type_desc AS data_source_type,
               ff.name AS file_format, ff.format_type
        FROM sys.external_tables et
        JOIN sys.tables t ON t.object_id = et.object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        LEFT JOIN sys.external_data_sources ds ON ds.data_source_id = et.data_source_id
        LEFT JOIN sys.external_file_formats ff ON ff.file_format_id = et.file_format_id
        WHERE 1=1{schema_filter}""", warnings, "external tables")
    cols = {}
    if ext:
        for r in _rows(cur, """
            SELECT c.object_id, c.name, ty.name AS type_name, c.max_length, c.precision, c.scale, c.is_nullable
            FROM sys.columns c JOIN sys.external_tables et ON et.object_id = c.object_id
            JOIN sys.types ty ON ty.user_type_id = c.user_type_id
            ORDER BY c.object_id, c.column_id"""):
            t = r["type_name"]
            if t in ("varchar", "char"):
                t += f"({'max' if r['max_length'] == -1 else r['max_length']})"
            elif t in ("nvarchar", "nchar"):
                t += f"({'max' if r['max_length'] == -1 else r['max_length'] // 2})"
            elif t in ("decimal", "numeric"):
                t += f"({r['precision']},{r['scale']})"
            cols.setdefault(r["object_id"], []).append(f"[{r['name']}] {t}{'' if r['is_nullable'] else ' NOT NULL'}")
    for r in ext:
        c = cols.get(r["object_id"], [])
        definition = (f"CREATE EXTERNAL TABLE [{r['schema_name']}].[{r['object_name']}]\n(\n    "
                      + ",\n    ".join(c) + "\n)\nWITH (\n"
                      f"    LOCATION = '{r['location']}',\n    DATA_SOURCE = [{r['data_source']}],\n"
                      f"    FILE_FORMAT = [{r['file_format']}]\n);"
                      f"\n-- data source location: {r['data_source_location']} ({r['data_source_type']})"
                      f"\n-- file format type: {r['format_type']}")
        objects.append({
            "schema": r["schema_name"], "name": r["object_name"], "object_type": "External Table",
            "type_code": "ET", "created": _iso(r["create_date"]), "modified": _iso(r["modify_date"]),
            "definition": definition, "definition_available": True,
            "location": r["location"], "data_source": r["data_source"],
            "data_source_location": r["data_source_location"], "file_format": r["file_format"],
            "format_type": r["format_type"], "column_count": len(c),
            "source": "sys.external_tables (definition reconstructed)",
        })

    conn.close()
    return objects, warnings, {"mode": "live", "server": args.server, "database": args.database}


# --------------------------------------------------------------------------- folder
CREATE_RE = re.compile(
    r"\b(?:CREATE|ALTER|CREATE\s+OR\s+ALTER)\s+"
    r"(PROCEDURE|PROC|FUNCTION|MATERIALIZED\s+VIEW|VIEW|EXTERNAL\s+TABLE)\s+"
    r"((?:\[[^\]]+\]|\"[^\"]+\"|[\w$#@]+)(?:\s*\.\s*(?:\[[^\]]+\]|\"[^\"]+\"|[\w$#@]+)){0,2})",
    re.I)
GO_RE = re.compile(r"^\s*GO\s*(?:--.*)?$", re.I | re.M)


def _strip_comments(sql):
    sql = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), sql, flags=re.S)
    return re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), sql)


def _split_name(raw):
    parts = [p.strip().strip("[]\"") for p in re.split(r"\s*\.\s*(?=(?:\[|\"|[\w$#@]))", raw)]
    if len(parts) == 1:
        return "dbo", parts[0]
    return parts[-2], parts[-1]


def _read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def extract_folder(args):
    objects, warnings, seen = [], [], {}
    files = []
    for root, _, names in os.walk(args.path):
        for n in names:
            if n.lower().endswith(".sql"):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        warnings.append(f"No .sql files under {args.path}")
    for f in files:
        text = _read_text(f)
        batches = GO_RE.split(text)
        for batch in batches:
            m = CREATE_RE.search(_strip_comments(batch))
            if not m:
                continue
            kind = re.sub(r"\s+", " ", m.group(1).upper())
            schema, name = _split_name(m.group(2))
            otype = {"PROCEDURE": "Stored Procedure", "PROC": "Stored Procedure", "FUNCTION": "Function",
                     "MATERIALIZED VIEW": "Materialized View", "VIEW": "View",
                     "EXTERNAL TABLE": "External Table"}[kind]
            definition = batch[m.start():].strip() if not batch[:m.start()].strip() else batch.strip()
            # keep leading comments but drop SET ANSI_NULLS / USE noise before CREATE
            pre = batch[:m.start()]
            if re.sub(r"(?is)--[^\n]*|/\*.*?\*/|\bSET\s+\w+\s+(ON|OFF)\b|\bUSE\s+\[?\w+\]?\s*;?|\s", "", pre) == "":
                definition = batch[m.start():].strip()
            obj = {
                "schema": schema, "name": name, "object_type": otype, "type_code": None,
                "created": None,
                "modified": dt.datetime.fromtimestamp(os.path.getmtime(f)).isoformat(sep=" ", timespec="seconds"),
                "definition": definition, "definition_available": True,
                "source": os.path.relpath(f, args.path),
            }
            if otype == "External Table":
                body = _strip_comments(definition)
                for key, field in (("LOCATION", "location"), ("DATA_SOURCE", "data_source"), ("FILE_FORMAT", "file_format")):
                    mm = re.search(rf"\b{key}\s*=\s*(?:N?'([^']*)'|\[?([\w\.]+)\]?)", body, re.I)
                    if mm:
                        obj[field] = mm.group(1) or mm.group(2)
                cols_block = re.search(r"\((.*?)\)\s*WITH\s*\(", body, re.S | re.I)
                obj["column_count"] = len(re.findall(r"^\s*\[?\w+\]?\s+\w+", cols_block.group(1), re.M)) if cols_block else None
            key = (schema.lower(), name.lower(), otype)
            if key in seen:
                warnings.append(f"Duplicate definition of {schema}.{name} in {obj['source']} (kept {seen[key]})")
                continue
            seen[key] = obj["source"]
            objects.append(obj)
    return objects, warnings, {"mode": "folder", "path": os.path.abspath(args.path)}


# --------------------------------------------------------------------------- export (query results)
def _read_export_rows(path):
    """Rows from a results-grid export of assets/export_definitions.sql (JSON, CSV or XLSX)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        data = json.load(open(path, encoding="utf-8-sig"))
        if isinstance(data, dict):  # some tools wrap rows
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return data
    if ext == ".csv":
        import csv
        csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        it = ws.iter_rows(values_only=True)
        hdr = [str(h).strip() if h is not None else "" for h in next(it)]
        return [dict(zip(hdr, r)) for r in it]
    raise ValueError(f"Unsupported export file type: {path} (use .json, .csv or .xlsx)")


def _val(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip().upper() == "NULL":
        return None
    return v


def extract_export(args):
    files = []
    if os.path.isdir(args.path):
        for root, _, names in os.walk(args.path):
            files += [os.path.join(root, n) for n in names if n.lower().endswith((".json", ".csv", ".xlsx", ".xlsm"))]
    else:
        files = [args.path]
    warnings, grouped = [], {}
    required = {"object_schema", "object_name", "type_code", "chunk_no", "chunk_text"}
    for f in sorted(files):
        rows = _read_export_rows(f)
        if not rows:
            warnings.append(f"{f}: no rows")
            continue
        cols = {str(k).strip().lower() for k in rows[0].keys()}
        if not required <= cols:
            warnings.append(f"{f}: skipped - not an export of export_definitions.sql (missing {sorted(required - cols)})")
            continue
        for r in rows:
            r = {str(k).strip().lower(): v for k, v in r.items()}
            key = (r["object_schema"], r["object_name"], str(r["type_code"]).strip())
            g = grouped.setdefault(key, {"meta": r, "chunks": {}})
            try:
                n = int(float(r["chunk_no"]))
            except (TypeError, ValueError):
                n = 0
            g["chunks"][n] = "" if _val(r.get("chunk_text")) is None else str(r["chunk_text"])
    objects = []
    for (schema, name, code), g in grouped.items():
        r = g["meta"]
        nums = sorted(g["chunks"])
        if code != "ET" and nums and nums != list(range(nums[-1] + 1)):
            warnings.append(f"{schema}.{name}: missing definition chunks {sorted(set(range(nums[-1] + 1)) - set(nums))}")
        text = "".join(g["chunks"][n] for n in nums).replace("\r\n", "\n")
        is_mv = str(_val(r.get("is_materialized")) or "0").strip().lower() in ("1", "true", "1.0")
        if code == "ET":
            parts = [g["chunks"][n] for n in nums]
            cols = [c for p in parts for c in (p.split("|") if len(parts) == 1 else [p]) if c and c.strip()]
            definition = (f"CREATE EXTERNAL TABLE [{schema}].[{name}]\n(\n    " + ",\n    ".join(cols) + "\n)\nWITH (\n"
                          f"    LOCATION = '{_val(r.get('location'))}',\n    DATA_SOURCE = [{_val(r.get('data_source'))}],\n"
                          f"    FILE_FORMAT = [{_val(r.get('file_format'))}]\n);"
                          f"\n-- data source location: {_val(r.get('data_source_location'))}"
                          f"\n-- file format type: {_val(r.get('format_type'))}")
            cc = _val(r.get("column_count"))
            objects.append({
                "schema": schema, "name": name, "object_type": "External Table", "type_code": "ET",
                "created": _val(r.get("created")), "modified": _val(r.get("modified")),
                "definition": definition, "definition_available": True,
                "location": _val(r.get("location")), "data_source": _val(r.get("data_source")),
                "data_source_location": _val(r.get("data_source_location")), "file_format": _val(r.get("file_format")),
                "format_type": _val(r.get("format_type")), "column_count": int(float(cc)) if cc not in (None, "") else len(cols),
                "source": f"{os.path.basename(args.path)} (external table, definition reconstructed)",
            })
            continue
        otype = TYPE_MAP.get(code, code)
        if code == "V" and (is_mv or re.match(r"\s*CREATE\s+MATERIALIZED\s+VIEW", text, re.I)):
            otype = "Materialized View"
        objects.append({
            "schema": schema, "name": name, "object_type": otype, "type_code": code,
            "function_kind": FUNC_KIND.get(code),
            "created": _val(r.get("created")), "modified": _val(r.get("modified")),
            "definition": text, "definition_available": bool(text.strip()),
            "source": os.path.basename(args.path),
        })
        if not text.strip():
            warnings.append(f"{schema}.{name}: empty definition (needs VIEW DEFINITION, or object is encrypted)")
    if not objects:
        warnings.append("No objects found - check the file is the saved result of export_definitions.sql")
    return objects, warnings, {"mode": "export", "path": os.path.abspath(args.path)}


# --------------------------------------------------------------------------- mirror classification
def classify_mirrors(objects, pattern):
    rx = re.compile(pattern, re.I)
    for o in objects:
        evidence = []
        if o["object_type"] == "External Table":
            if (o.get("format_type") or "").upper() == "DELTA":
                evidence.append("file format type DELTA")
            for f in ("data_source", "data_source_location", "location", "file_format"):
                if o.get(f) and rx.search(str(o[f])):
                    evidence.append(f"{f} matches pattern ({o[f]})")
            if not o.get("format_type") and o.get("file_format") and re.search(r"delta", o["file_format"], re.I):
                evidence.append("file format name contains 'delta'")
        body = _strip_comments(o.get("definition") or "")
        if re.search(r"OPENROWSET\s*\(.*?FORMAT\s*=\s*'DELTA'", body, re.I | re.S):
            evidence.append("OPENROWSET ... FORMAT='DELTA'")
        if rx.search(f"{o['schema']}.{o['name']}"):
            evidence.append("object/schema name matches pattern")
        o["mirror_evidence"] = "; ".join(evidence) or None
        if evidence and o["object_type"] in ("External Table", "View"):
            o["original_type"] = o["object_type"]
            o["object_type"] = "Databricks Mirror"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    lv = sub.add_parser("live", help="read from a Synapse SQL pool")
    _load_dotenv()
    env = os.environ.get
    lv.add_argument("--url", default=env("SYNAPSE_URL"),
                    help="endpoint URL, JDBC/ADO.NET connection string or Synapse Studio URL (sets server/database)")
    lv.add_argument("--server", default=env("SYNAPSE_SERVER"),
                    help="<workspace>.sql.azuresynapse.net (dedicated) or <workspace>-ondemand.sql.azuresynapse.net")
    lv.add_argument("--database", default=env("SYNAPSE_DATABASE"))
    lv.add_argument("--user", default=env("SYNAPSE_USER"))
    lv.add_argument("--password", help="or set SYNAPSE_PASSWORD (env / .env)")
    lv.add_argument("--auth", default=env("SYNAPSE_AUTH", "token"),
                    help="token (default: current Azure login via azure-identity), ActiveDirectoryInteractive "
                         "(Windows), ActiveDirectoryPassword, ActiveDirectoryServicePrincipal, SqlPassword")
    lv.add_argument("--driver", default=env("SYNAPSE_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"))
    lv.add_argument("--connection-string", default=env("SYNAPSE_CONNECTION_STRING"))
    lv.add_argument("--schemas", default=env("SYNAPSE_SCHEMAS"), help="comma-separated schema filter")
    lv.add_argument("--test-connection", action="store_true", help="connect, report permissions and object counts, then exit")
    fd = sub.add_parser("folder", help="read exported .sql scripts (SQL project, sqlpackage, Generate Scripts)")
    fd.add_argument("--path", required=True)
    ex = sub.add_parser("export", help="read the saved results of assets/export_definitions.sql (.json/.csv/.xlsx)")
    ex.add_argument("--path", required=True, help="saved results file, or a folder of them")
    for p in (lv, fd, ex):
        p.add_argument("--out", default="inventory.json")
        p.add_argument("--mirror-pattern", default=DEFAULT_MIRROR_PATTERN,
                       help="regex identifying Databricks mirror objects by name/location/data source")
        p.add_argument("--datamart", help="label for the report (defaults to database or folder name)")
    args = ap.parse_args()
    if args.mode == "live" and args.url:
        try:
            srv, db = parse_url(args.url)
        except ValueError as e:
            ap.error(str(e))
        args.server = srv if "--server" not in sys.argv else args.server
        args.database = (db or args.database) if "--database" not in sys.argv else args.database
        print(f"Using server={args.server} database={args.database}", file=sys.stderr)
    if args.mode == "live" and not (args.connection_string or (args.server and args.database)):
        ap.error("live mode needs --server and --database (or SYNAPSE_SERVER / SYNAPSE_DATABASE in .env)")
    if args.mode == "live" and args.test_connection:
        return test_connection(args)

    objects, warnings, meta = {"live": extract_live, "folder": extract_folder, "export": extract_export}[args.mode](args)
    classify_mirrors(objects, args.mirror_pattern)
    meta.update({
        "datamart": args.datamart or meta.get("database") or os.path.basename(os.path.abspath(meta.get("path", "."))),
        "extracted_at": dt.datetime.now().isoformat(sep=" ", timespec="seconds"),
        "mirror_pattern": args.mirror_pattern,
        "warnings": warnings,
    })
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "objects": objects}, fh, indent=1, default=str)
    counts = {}
    for o in objects:
        counts[o["object_type"]] = counts.get(o["object_type"], 0) + 1
    print(json.dumps({"out": args.out, "objects": len(objects), "by_type": counts, "warnings": warnings}, indent=1))


if __name__ == "__main__":
    main()
