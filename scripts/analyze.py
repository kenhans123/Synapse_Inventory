"""
Static analysis of T-SQL object definitions for a Synapse -> Databricks / Fabric migration inventory.
Pure regex (no SQL parser dependency) so it runs anywhere; tuned for Synapse dedicated pool T-SQL.
"""
import re

NAME = r"(?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_#@][\w$#@]*)(?:\s*\.\s*(?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w$]*)){0,3}"

# key, label, regex, weight, databricks note, fabric note
FEATURES = [
    ("cursor", "Cursor", r"\bCURSOR\b", 5,
     "Rewrite row-by-row cursor as set-based Spark SQL / DataFrame logic.",
     "Rewrite cursor as set-based T-SQL or notebook logic (cursors unsupported in Fabric Warehouse)."),
    ("while", "WHILE loop", r"\bWHILE\b", 3,
     "Move loop control to Python in a notebook or to a Workflow for-each task.",
     "WHILE is supported in Warehouse; review for set-based rewrite or pipeline ForEach."),
    ("dynamic_sql", "Dynamic SQL", r"\bsp_executesql\b|\bEXEC(?:UTE)?\s*\(", 4,
     "Build SQL strings in Python (spark.sql) with parameter validation.",
     "sp_executesql supported in Warehouse; review string building and injection risk."),
    ("temp_table", "Temp tables (#)", r"(?<![\w@])#\w+", 2,
     "Replace #temp tables with temp views, cached DataFrames or Delta staging tables.",
     "Session #temp tables supported in Warehouse; check distribution/CTAS syntax on them."),
    ("table_variable", "Table variables", r"\bDECLARE\s+@\w+\s+(?:AS\s+)?TABLE\b", 1,
     "Replace table variables with DataFrames or temp views.",
     "Table variables are not supported in Fabric Warehouse; use #temp tables."),
    ("ctas", "CTAS", r"\bCREATE\s+TABLE\s+" + NAME + r"\s+WITH\s*\(", 2,
     "Convert to CREATE OR REPLACE TABLE ... AS SELECT (Delta).",
     "CTAS supported; remove WITH (DISTRIBUTION/INDEX) options."),
    ("distribution", "Distribution / index hints", r"\bDISTRIBUTION\s*=|\bCLUSTERED\s+COLUMNSTORE\b|\bHEAP\b", 1,
     "Drop distribution/CCI options; use liquid clustering, partitioning or Z-ORDER.",
     "Drop DISTRIBUTION / CCI / HEAP options; Warehouse manages layout."),
    ("rename", "RENAME OBJECT", r"\bRENAME\s+OBJECT\b", 2,
     "Replace CTAS+RENAME swap with CREATE OR REPLACE TABLE or Delta MERGE / overwrite.",
     "Use sp_rename or CREATE OR REPLACE / overwrite pattern."),
    ("merge", "MERGE", r"\bMERGE\s+(?:INTO\s+)?" + NAME + r"(?:\s+(?:AS\s+)?\w+)?\s+USING\b", 2,
     "Delta MERGE INTO is supported; port syntax (no OUTPUT clause, single match per target row).",
     "MERGE supported in Warehouse; verify OUTPUT clause and match semantics."),
    ("try_catch", "TRY/CATCH", r"\bBEGIN\s+TRY\b", 1,
     "Move error handling to Python try/except or job retry policy.",
     "TRY/CATCH supported."),
    ("transaction", "Explicit transactions", r"\bBEGIN\s+TRAN(?:SACTION)?\b", 2,
     "No multi-statement transactions in Spark; redesign for idempotent Delta writes.",
     "Explicit transactions supported in Warehouse (snapshot isolation)."),
    ("goto", "GOTO", r"\bGOTO\b", 3,
     "Restructure control flow (GOTO has no equivalent).",
     "Restructure control flow; GOTO is unsupported."),
    ("nested_exec", "Calls other procedures", r"\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?(?!sp_executesql\b)(?![@(])" + NAME, 2,
     "Map call chain to notebook %run / dbutils.notebook.run or Workflow task dependencies.",
     "Nested procedure calls supported; confirm callees are migrated first."),
    ("openrowset", "OPENROWSET", r"\bOPENROWSET\s*\(", 2,
     "Replace with spark.read / read_files() or a Unity Catalog external table.",
     "Replace with OPENROWSET over OneLake (Warehouse) or a Lakehouse shortcut."),
    ("copy_into", "COPY INTO", r"\bCOPY\s+INTO\b", 2,
     "Databricks COPY INTO or Auto Loader (syntax differs).",
     "COPY INTO supported in Warehouse; update source path/credential."),
    ("cross_db", "Cross-database names", None, 3,
     "Map database.schema.object to catalog.schema.object in Unity Catalog.",
     "Use cross-warehouse/lakehouse 3-part names within the same workspace."),
    ("window", "Window functions", r"\bOVER\s*\(", 1,
     "Window functions port directly to Spark SQL.",
     "Supported."),
    ("pivot", "PIVOT / UNPIVOT", r"\b(?:UN)?PIVOT\s*\(", 2,
     "Spark SQL PIVOT syntax differs (requires explicit value list).",
     "Supported; verify syntax."),
    ("truncate", "TRUNCATE", r"\bTRUNCATE\s+TABLE\b", 1,
     "TRUNCATE TABLE supported on Delta; consider INSERT OVERWRITE.",
     "Supported."),
    ("error_raise", "RAISERROR / THROW", r"\bRAISERROR\b|\bTHROW\b", 1,
     "Raise Python exceptions or use raise_error() in SQL.",
     "THROW supported; RAISERROR has limited support."),
    ("sys_vars", "@@ROWCOUNT / @@ERROR etc.", r"@@(?:ROWCOUNT|ERROR|TRANCOUNT|IDENTITY|SPID)\b|\bSCOPE_IDENTITY\s*\(", 2,
     "Use Delta operation metrics (DESCRIBE HISTORY / DataFrame counts).",
     "@@ROWCOUNT supported; @@ERROR/@@IDENTITY limited - review."),
    ("identity", "IDENTITY columns", r"\bIDENTITY\s*\(", 1,
     "Use GENERATED ALWAYS AS IDENTITY on Delta.",
     "IDENTITY supported in Warehouse (non-sequential values)."),
    ("statistics", "Statistics maintenance", r"\b(?:CREATE|UPDATE)\s+STATISTICS\b", 1,
     "Remove; use ANALYZE TABLE / predictive optimisation.",
     "Auto statistics in Warehouse; manual statements optional."),
    ("label", "Query LABEL hints", r"\bOPTION\s*\(\s*LABEL\b", 0,
     "Remove OPTION(LABEL); use query tags / job names.",
     "Remove OPTION(LABEL) (unsupported)."),
    ("scalar_udf_call", "Calls user functions", None, 1,
     "Port called UDFs first (SQL UDFs in Unity Catalog or inline the logic).",
     "Port called functions first; scalar UDF inlining rules differ."),
]
FEATURE_BY_KEY = {f[0]: f for f in FEATURES}

RATING_BANDS = [(6, "Low"), (14, "Medium"), (25, "High"), (10 ** 9, "Very High")]

SKIP_NAMES = {
    "select", "values", "openrowset", "openquery", "openjson", "string_split", "generate_series", "statistics",
    "set", "where", "as", "on", "with", "top", "distinct", "lateral", "dual", "inserted", "deleted", "target",
    "source", "exec", "execute", "table", "into", "from", "join", "matched", "object", "sys", "information_schema",
}


def strip_comments_and_strings(sql):
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", sql[i:j])); i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2; continue
                if sql[j] == "'":
                    break
                j += 1
            out.append("''" + " " * max(0, j - i - 1)); i = j + 1
        else:
            out.append(c); i += 1
    return "".join(out)


def clean_name(raw):
    parts = re.split(r"\s*\.\s*", raw.strip())
    return ".".join(p.strip().strip("[]\"") for p in parts)


def _header(clean, raw):
    """Text between CREATE ... <name> and the body start (taken from the raw text so defaults keep their literals)."""
    m = re.search(r"\b(?:PROC|PROCEDURE|FUNCTION)\s+" + NAME, clean, re.I)
    if not m:
        return ""
    rest = clean[m.end():]
    end = re.search(r"\bRETURNS\b|\bAS\b\s*(?:BEGIN\b|RETURN\b|SET\b|SELECT\b|DECLARE\b|IF\b|WITH\b|EXEC|INSERT\b|UPDATE\b|DELETE\b|TRUNCATE\b|CREATE\b|$)|\bWITH\s+(?:EXECUTE|RECOMPILE|SCHEMABINDING|NATIVE)", rest, re.I | re.M)
    stop = m.end() + (end.start() if end else min(len(rest), 2000))
    head = raw[m.end():stop] if len(raw) == len(clean) else rest[: stop - m.end()]
    return re.sub(r"--[^\n]*|/\*.*?\*/", " ", head, flags=re.S)


def parse_parameters(clean, raw):
    head = _header(clean, raw)
    params = []
    for m in re.finditer(r"(@\w+)\s+(?:AS\s+)?(\[?[A-Za-z_][\w\.]*\]?(?:\s*\(\s*[\w\s,]*\))?)(\s*=\s*(?:N?'[^']*'|[^,]+?))?(\s+(?:OUTPUT|OUT)\b)?\s*(?=,|\)|$|READONLY)", head, re.I | re.S):
        ptype = re.sub(r"[\s\[\]]+", "", m.group(2)).upper()
        p = m.group(1) + " " + ptype
        if m.group(3):
            p += " " + re.sub(r"\s+", " ", m.group(3).strip())
        if m.group(4):
            p += " OUTPUT"
        params.append(p)
    return params


def function_kind(clean):
    m = re.search(r"\bRETURNS\s+(@\w+\s+)?(TABLE\b|[A-Za-z_]\w*(?:\s*\([\w\s,]*\))?)", clean, re.I)
    if not m:
        return None, None
    if m.group(1):
        return "Multi-statement TVF", "TABLE"
    if m.group(2).upper() == "TABLE":
        return "Inline TVF", "TABLE"
    return "Scalar", re.sub(r"\s+", "", m.group(2))


def extract_references(clean, own_name):
    """Return {referenced_name: set(ops)} from a comment/string-stripped definition."""
    refs = {}
    ctes = {m.group(1).lower() for m in re.finditer(r"(?:\bWITH|,)\s*([A-Za-z_]\w*)\s*(?:\([^()]*\))?\s+AS\s*\(", clean, re.I)}
    aliases = {}
    for m in re.finditer(
            r"\b(?:FROM|JOIN|UPDATE|INTO|USING)\s+(" + NAME + r")\s+(?:AS\s+)?(?!(?:ON|WHERE|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|OUTER|GROUP|ORDER|SET|WITH|SELECT|UNION|VALUES|USING|WHEN|OPTION|PIVOT|UNPIVOT|AS|INTO)\b)([A-Za-z_]\w*)", clean, re.I):
        aliases[m.group(2).lower()] = m.group(1)
    cursors = {m.group(1).lower() for m in re.finditer(r"\bDECLARE\s+(\w+)\s+(?:INSENSITIVE\s+|SCROLL\s+)*CURSOR\b", clean, re.I)}
    own = own_name.lower()

    def add(raw, op, followed_by_paren=False):
        name = clean_name(raw)
        low = name.lower()
        last = low.split(".")[-1]
        first = low.split(".")[0]
        if (not name or name[0] in "#@" or last in SKIP_NAMES or first in ("sys", "information_schema", "tempdb")
                or low in ctes or low in cursors or low in (own, own.split(".")[-1])):
            return
        if "." not in name and low in aliases:
            if op == "WRITE":   # UPDATE alias SET ... FROM real_table alias
                target = aliases[low]
                if "." in target or target.lower() not in aliases:
                    add(target, op)
            return
        refs.setdefault(name, set()).add(op)

    delete_spans = []
    patterns = [
        (r"\bDELETE\s+(?:TOP\s*\([^)]*\)\s+)?(?:FROM\s+)?(" + NAME + ")", "WRITE"),
        (r"\b(?:INSERT\s+(?:INTO\s+)?|(?<!INSERT\s)INTO\s+)(" + NAME + ")", "WRITE"),
        (r"\bUPDATE\s+(?!STATISTICS\b)(" + NAME + ")", "WRITE"),
        (r"\bMERGE\s+(?:INTO\s+)?(" + NAME + ")", "WRITE"),
        (r"\bTRUNCATE\s+TABLE\s+(" + NAME + ")", "WRITE"),
        (r"\bCREATE\s+TABLE\s+(" + NAME + ")", "WRITE"),
        (r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(" + NAME + ")", "WRITE"),
        (r"\bRENAME\s+OBJECT\s+(?:::)?(" + NAME + ")", "WRITE"),
        (r"\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?(?!sp_executesql\b)(?![@(])(" + NAME + ")", "EXEC"),
    ]
    for rx, op in patterns:
        for m in re.finditer(rx, clean, re.I):
            if op == "WRITE" and rx.startswith(r"\bDELETE"):
                delete_spans.append((m.start(), m.end()))
            add(m.group(1), op)
    for m in re.finditer(r"\b(FROM|JOIN|APPLY|USING)\s+(" + NAME + r")(\s*\()?", clean, re.I):
        if any(s <= m.start() < e for s, e in delete_spans):
            continue
        add(m.group(2), "READ", bool(m.group(3)))
    # user function calls schema.func(
    for m in re.finditer(r"(?<![\w\.\]])(" + r"(?:\[[^\]]+\]|[A-Za-z_]\w*)\s*\.\s*(?:\[[^\]]+\]|[A-Za-z_]\w*)" + r")\s*\(", clean):
        name = clean_name(m.group(1))
        if name.split(".")[0].lower() not in ("sys",) and not re.match(r"(?i)^\w+\.(value|query|nodes|exist|modify)$", name):
            refs.setdefault(name, set()).add("CALL?")
    return refs


def analyze(objects, target="databricks"):
    note_idx = 4 if target == "databricks" else 5
    known = {}
    by_short = {}
    for o in objects:
        full = f"{o['schema']}.{o['name']}"
        o["full_name"] = full
        known[full.lower()] = o
        by_short.setdefault(o["name"].lower(), []).append(o)

    deps = []
    for o in objects:
        raw = o.get("definition") or ""
        clean = strip_comments_and_strings(raw)
        o["lines"] = sum(1 for ln in raw.splitlines() if ln.strip())
        o["chars"] = len(raw)

        refs = extract_references(clean, o["full_name"]) if o["object_type"] not in ("External Table",) else {}
        for cd in o.get("catalog_dependencies") or []:
            refs.setdefault(cd, set()).add("READ")

        resolved = {}
        for name, ops in refs.items():
            parts = name.split(".")
            tgt = None
            if len(parts) >= 2:
                tgt = known.get(".".join(parts[-2:]).lower())
            elif len(by_short.get(parts[0].lower(), [])) == 1:
                tgt = by_short[parts[0].lower()][0]
            if "CALL?" in ops:
                if tgt is None or tgt["object_type"] != "Function":
                    ops = ops - {"CALL?"}
                    if not ops:
                        continue
                else:
                    ops = (ops - {"CALL?"}) | {"CALL"}
            if tgt is not None and tgt is o:
                continue
            key = tgt["full_name"] if tgt else name
            r = resolved.setdefault(key, {"ops": set(), "target": tgt, "cross_db": len(parts) >= 3})
            r["ops"] |= ops

        feats = []
        for key, label, rx, weight, *_ in FEATURES:
            if rx and re.search(rx, clean, re.I):
                feats.append(key)
        if any(r["cross_db"] for r in resolved.values()):
            feats.append("cross_db")
        if any("CALL" in r["ops"] for r in resolved.values()):
            feats.append("scalar_udf_call")
        if "nested_exec" in feats and not any("EXEC" in r["ops"] for r in resolved.values()):
            feats.remove("nested_exec")
        o["features"] = feats

        o["reads"] = sorted(k for k, r in resolved.items() if r["ops"] & {"READ", "CALL"})
        o["writes"] = sorted(k for k, r in resolved.items() if "WRITE" in r["ops"])
        o["calls"] = sorted(k for k, r in resolved.items() if "EXEC" in r["ops"])
        for k, r in sorted(resolved.items()):
            deps.append({
                "object": o["full_name"], "object_type": o["object_type"], "referenced": k,
                "referenced_type": r["target"]["object_type"] if r["target"] else
                ("Cross-database object" if r["cross_db"] else "Table / unresolved"),
                "operations": ", ".join(sorted(r["ops"])), "resolved": "Y" if r["target"] else "N",
            })

        score = min(o["lines"] / 25.0, 20) + sum(FEATURE_BY_KEY[f][3] for f in feats) \
            + min(len(resolved) * 0.5, 8)
        o["complexity_score"] = round(score, 1)
        o["rating"] = next(lbl for lim, lbl in RATING_BANDS if score <= lim)
        notes = [FEATURE_BY_KEY[f][note_idx] for f in feats if FEATURE_BY_KEY[f][3] > 0 or f == "label"]
        if o["object_type"] == "Materialized View":
            notes.insert(0, "Materialized view: rebuild as Databricks materialized view / Delta Live Table, or a scheduled aggregate table."
                         if target == "databricks" else
                         "Materialized view: not supported in Fabric Warehouse; use a scheduled aggregate table or Lakehouse materialized view.")
        if o["object_type"] in ("Databricks Mirror", "External Table"):
            notes.insert(0, "Source already lives in Delta: register directly in Unity Catalog (no copy) and retire the Synapse external object."
                         if target == "databricks" else
                         "Expose via OneLake shortcut / Mirrored Azure Databricks catalog instead of an external table.")
        if o.get("definition_truncated"):
            notes.insert(0, "Definition was cut short in the export - analysis is partial; re-export with "
                            "export_long_definitions.sql.")
        if not o.get("definition_available", True):
            notes.insert(0, "Definition not readable (encrypted or permissions) - obtain source manually.")
        o["migration_notes"] = " ".join(dict.fromkeys(notes)) or "Straight port; syntax review only."

        if o["object_type"] in ("Stored Procedure", "Function"):
            if not o.get("parameters"):
                o["parameters"] = parse_parameters(clean, raw)
        if o["object_type"] == "Function":
            kind, ret = function_kind(clean)
            o["function_kind"] = o.get("function_kind") or kind
            o["returns"] = ret
        if o["object_type"] in ("Materialized View", "View", "Databricks Mirror"):
            d = re.search(r"\bDISTRIBUTION\s*=\s*(HASH\s*\([^)]*\)|ROUND_ROBIN|REPLICATE)", clean, re.I)
            o["distribution"] = re.sub(r"\s+", "", d.group(1)) if d else None
            o["has_group_by"] = bool(re.search(r"\bGROUP\s+BY\b", clean, re.I))
            o["aggregates"] = ", ".join(sorted({a.upper() for a in re.findall(r"\b(COUNT_BIG|COUNT|SUM|AVG|MIN|MAX)\s*\(", clean, re.I)}))
    return deps
