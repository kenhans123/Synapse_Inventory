"""
Object lineage for the Synapse datamart inventory.

  build_graph()     nodes + edges from the dependency rows produced by analyze.analyze()
  neighbourhood()   upstream / downstream subgraph around one object
  layout()          layered left-to-right layout (sources left, consumers right)
  migration_waves() dependency-ordered wave per object (what must exist / load first)
  render_png()      per-object lineage diagram (matplotlib, optional) for the Excel Lineage sheet
  write_html()      self-contained interactive lineage explorer (no CDN / internet needed)

Edge kinds (data flows left -> right):
  data  table/view -> reader,  writer -> target table
  call  function -> object that calls it
  exec  called procedure -> procedure that calls it (dashed)
Every edge reads "is needed by": upstream = what an object depends on, downstream = what is impacted if it changes.
"""
import json
import os
from collections import defaultdict, deque

NODE_COLORS = {  # fill, stroke  (same hues as the workbook tab colours)
    "Stored Procedure": ("#DCE8F7", "#2E75B6"),
    "Function": ("#E2F0D9", "#548235"),
    "Materialized View": ("#FFF2CC", "#BF8F00"),
    "View": ("#EADCF4", "#7030A0"),
    "Databricks Mirror": ("#FBE3D6", "#C55A11"),
    "External Table": ("#FBE3D6", "#C55A11"),
    "Table": ("#F2F2F2", "#7F7F7F"),
    "Cross-database object": ("#FFFFFF", "#7F7F7F"),
}


# --------------------------------------------------------------------------- graph
def build_graph(objects, deps):
    try:
        from analyze import FEATURE_BY_KEY
    except ImportError:
        FEATURE_BY_KEY = {}
    nodes = {}
    for o in objects:
        nodes[o["full_name"]] = {
            "id": o["full_name"], "schema": o["schema"], "name": o["name"], "type": o["object_type"],
            "rating": o.get("rating"), "lines": o.get("lines"), "score": o.get("complexity_score"),
            "features": [FEATURE_BY_KEY[f][1] if f in FEATURE_BY_KEY else f for f in o.get("features", [])],
            "inventory": True,
            "location": o.get("location"), "evidence": o.get("mirror_evidence"),
        }
    edges = {}
    for d in deps:
        obj, ref = d["object"], d["referenced"]
        if ref not in nodes:
            parts = ref.split(".")
            nodes[ref] = {"id": ref, "schema": ".".join(parts[:-1]) or "dbo", "name": parts[-1],
                          "type": "Cross-database object" if d["referenced_type"] == "Cross-database object" else
                          ("Table" if d["referenced_type"] == "Table / unresolved" else d["referenced_type"]),
                          "inventory": False, "features": []}
        ops = {x.strip() for x in d["operations"].split(",")}
        if "READ" in ops:
            edges[(ref, obj, "data")] = {"src": ref, "dst": obj, "kind": "data", "readback": "WRITE" in ops}
        if "CALL" in ops:
            edges[(ref, obj, "call")] = {"src": ref, "dst": obj, "kind": "call"}
        if "WRITE" in ops:
            edges[(obj, ref, "data")] = {"src": obj, "dst": ref, "kind": "data"}
        if "EXEC" in ops:   # callee is needed by caller -> callee on the left
            edges[(ref, obj, "exec")] = {"src": ref, "dst": obj, "kind": "exec"}
    return nodes, list(edges.values())


def _adj(edges):
    out_, in_ = defaultdict(list), defaultdict(list)
    for e in edges:
        out_[e["src"]].append(e["dst"])
        in_[e["dst"]].append(e["src"])
    return out_, in_


def closure(start, adj, max_depth=None):
    """BFS over adj; returns {node: depth} excluding start."""
    seen, q = {start: 0}, deque([start])
    while q:
        n = q.popleft()
        if max_depth is not None and seen[n] >= max_depth:
            continue
        for m in adj.get(n, []):
            if m not in seen:
                seen[m] = seen[n] + 1
                q.append(m)
    seen.pop(start)
    return seen


def neighbourhood(focus, nodes, edges, up=2, down=2, max_nodes=40):
    out_, in_ = _adj(edges)
    for u, d in ((up, down), (1, 1), (1, 0)):
        ups, downs = closure(focus, in_, u), closure(focus, out_, d)
        keep = {focus} | set(ups) | set(downs)
        if len(keep) <= max_nodes:
            break
    else:
        ranked = sorted(list(ups.items()) + list(downs.items()), key=lambda kv: kv[1])
        keep = {focus} | {k for k, _ in ranked[: max_nodes - 1]}
    sub_edges = [e for e in edges if e["src"] in keep and e["dst"] in keep]
    return {k: nodes[k] for k in keep}, sub_edges


# --------------------------------------------------------------------------- layout
def _acyclic(node_ids, edges):
    """Edges used for layering: drop READ edges that also have a WRITE back, then DFS back-edges."""
    layer_edges = [e for e in edges if not e.get("readback")]
    out_ = defaultdict(list)
    for e in layer_edges:
        out_[e["src"]].append(e)
    state, back = {}, set()

    def dfs(n):
        state[n] = 1
        for e in out_[n]:
            m = e["dst"]
            if state.get(m) == 1:
                back.add(id(e))
            elif m not in state:
                dfs(m)
        state[n] = 2

    import sys
    sys.setrecursionlimit(max(10000, len(node_ids) * 4))
    for n in sorted(node_ids):
        if n not in state:
            dfs(n)
    return [e for e in layer_edges if id(e) not in back]


def layout(node_ids, edges, gap_x=1.0, gap_y=0.28, w=2.5, h=0.55):
    dag = _acyclic(node_ids, edges)
    preds = defaultdict(list)
    for e in dag:
        preds[e["dst"]].append(e["src"])
    layer = {}

    def lay(n, guard=0):
        if n in layer:
            return layer[n]
        layer[n] = 0
        layer[n] = max((lay(p) + 1 for p in preds[n]), default=0)
        return layer[n]

    for n in node_ids:
        lay(n)
    cols = defaultdict(list)
    for n in sorted(node_ids):
        cols[layer[n]].append(n)
    nbrs = defaultdict(list)
    for e in edges:
        nbrs[e["src"]].append(e["dst"]); nbrs[e["dst"]].append(e["src"])
    pos_idx = {n: i for c in cols.values() for i, n in enumerate(c)}
    for _ in range(6):  # barycenter sweeps
        for c in sorted(cols):
            cols[c].sort(key=lambda n: (sum(pos_idx[m] for m in nbrs[n]) / len(nbrs[n])) if nbrs[n] else pos_idx[n])
            for i, n in enumerate(cols[c]):
                pos_idx[n] = i
    tallest = max((len(c) for c in cols.values()), default=1)
    pos = {}
    for c, members in cols.items():
        offset = (tallest - len(members)) * (h + gap_y) / 2
        for i, n in enumerate(members):
            pos[n] = (c * (w + gap_x), offset + i * (h + gap_y))
    return pos, len(cols), tallest


# --------------------------------------------------------------------------- waves
def migration_waves(nodes, edges):
    """Wave 1 = objects that only depend on base tables; wave N depends on wave N-1. Cycles share a wave."""
    dep_edges = []  # (prereq, dependent)
    for e in edges:
        if e.get("readback"):
            continue
        dep_edges.append((e["src"], e["dst"]))   # every edge already points prerequisite -> dependent
    # Tarjan SCC (iterative)
    graph = defaultdict(list)
    for a, b in dep_edges:
        graph[a].append(b)
    index, low, onstack, stack, comp, counter = {}, {}, set(), [], {}, [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(graph[root]))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); onstack.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]; counter[0] += 1
                    stack.append(nxt); onstack.add(nxt)
                    work.append((nxt, iter(graph[nxt])))
                elif nxt in onstack:
                    low[v] = min(low[v], index[nxt])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    cid = v
                    while True:
                        w_ = stack.pop(); onstack.discard(w_); comp[w_] = cid
                        if w_ == v:
                            break
    cpreds = defaultdict(set)
    for a, b in dep_edges:
        if comp[a] != comp[b]:
            cpreds[comp[b]].add(comp[a])
    members = defaultdict(list)
    for n, c in comp.items():
        members[c].append(n)
    wave = {}

    def cw(c):
        if c in wave:
            return wave[c]
        wave[c] = 0
        base = max((cw(p) for p in cpreds[c]), default=0)
        wave[c] = base + (1 if any(nodes[m]["inventory"] for m in members[c]) else 0)
        return wave[c]

    import sys
    sys.setrecursionlimit(max(10000, len(nodes) * 4))
    return {n: cw(comp[n]) for n in nodes}, {n for c, ms in members.items() if len(ms) > 1 for n in ms}


# --------------------------------------------------------------------------- PNG
def render_png(focus, sub_nodes, sub_edges, path, dpi=110):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    except ImportError:
        return None
    W, H = 2.5, 0.55
    pos, ncols, tallest = layout(list(sub_nodes), sub_edges, w=W, h=H)
    width = ncols * W + (ncols - 1) * 1.0 + 0.4
    height = tallest * H + (tallest - 1) * 0.28 + 0.4
    scale = 0.62
    fig = plt.figure(figsize=(max(width, 3.5) * scale, max(height, 0.9) * scale), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-0.2, max(width, 3.5) - 0.2)
    ax.set_ylim(max(height, 0.9) - 0.2, -0.2)
    ax.axis("off")
    for e in sub_edges:
        (x1, y1), (x2, y2) = pos[e["src"]], pos[e["dst"]]
        start, end = (x1 + W, y1 + H / 2), (x2, y2 + H / 2)
        rad = 0.0
        if x2 <= x1:  # backward / same column
            start, end, rad = (x1 + W / 2, y1 + (H if y2 > y1 else 0)), (x2 + W / 2, y2 + (0 if y2 > y1 else H)), 0.35
        style = {"exec": (0, (4, 3)), "call": (0, (1, 2))}.get(e["kind"], "solid")
        color = {"exec": "#7F7F7F", "call": "#548235"}.get(e["kind"], "#5B6B82")
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=7, lw=0.8, color=color,
                                     linestyle=style, connectionstyle=f"arc3,rad={rad}", shrinkA=0, shrinkB=1,
                                     zorder=1))
    for n, (x, y) in pos.items():
        nd = sub_nodes[n]
        fill, stroke = NODE_COLORS.get(nd["type"], NODE_COLORS["Table"])
        is_focus = n == focus
        ax.add_patch(FancyBboxPatch((x, y), W, H, boxstyle="round,pad=0,rounding_size=0.08",
                                    fc=fill, ec="#1F3864" if is_focus else stroke, lw=2.2 if is_focus else 0.9,
                                    ls="--" if nd["type"] == "Cross-database object" else "-", zorder=2))
        label = nd["name"] if len(nd["name"]) <= 30 else nd["name"][:28] + "…"
        ax.text(x + 0.08, y + 0.19, nd["schema"], fontsize=5.2, color="#595959", va="center", zorder=3)
        ax.text(x + 0.08, y + 0.39, label, fontsize=6.3, color="#1F1F1F", va="center",
                fontweight="bold" if is_focus else "normal", zorder=3)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- HTML
def write_html(nodes, edges, waves, cyclic, meta, path):
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "lineage_template.html")
    tpl = open(tpl_path, encoding="utf-8").read()
    payload = {
        "meta": {"datamart": meta.get("datamart"), "extracted": meta.get("extracted_at"),
                 "source": meta.get("database") or meta.get("path")},
        "nodes": [dict(n, wave=waves.get(k), cyclic=k in cyclic) for k, n in sorted(nodes.items())],
        "edges": edges,
        "colors": NODE_COLORS,
    }
    data = json.dumps(payload, default=str).replace("</", "<\\/")
    html = tpl.replace("__LINEAGE_DATA__", data).replace("__TITLE__", f"{meta.get('datamart')} lineage")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
