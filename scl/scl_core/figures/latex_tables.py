"""The paper's tables as LaTeX bodies: ONE writer over the figure tools' table grids (2026-10-17).

Until this day the three tables the paper prints (the score split by passability, the driving score and
decision accuracy per agent, the identity ladder) were hand-typed LaTeX with the pipeline's values pasted
in; scripts/check_paper_numbers.py proved the cells equal the pipeline, and every regeneration meant
retyping. Now a figure tool resolves each table's cells from the pipeline's table documents as a
paper-tables spec says (artifacts/examples/figure_specs/paper_tables.json, paper_tables/v0.1: which
sources, which agents under which printed label, which columns in which format, and the row rules) and
this module writes the tabular body; the paper's file keeps the float, the font size, the caption and the
label and inputs the body, the way the figures are included.

The split: a figure tool RESOLVES (it has the documents) and this module TYPESETS (it has the rules).
The grid the tool hands over is backend-neutral:

  {"columns": [{"header": "Agent"}, {"header": "All", "group": "Mean DS", "format": "one_decimal"}, ...],
   "blocks":  [{"label": "Driving (CARLA)" | None,
                "rows": [{"id": "plant2", "label": "PlanT 2.0",
                          "cells": [cell | None, ...],            # one per column after the label column
                          "footnote_value": <any> | None}, ...]}],
   "column_spec": "@{}lrrrrrr@{}", "tabcolsep_pt": 3.5}

A cell is typed by its column's format: {"value": number} (one_decimal, two_decimals, integer),
{"value": number, "interval": [lo, hi]} (value_with_interval), {"clean": k, "n": n, "other": {outcome: count}}
(clean_of_runs), {"text": str} (text); None is a missing value and prints the spec's dash. The rules a table
row declares (order, bold with its exclusions, the cell rule with its shading and outcome codes, the collapse
rule, the footnote marker) are applied here, from an admitted vocabulary; an unknown format, rule, code or
cell shape refuses naming the vocabulary (refusal discipline). Headers and markers are authored LaTeX and
pass through; row labels and text cells are escaped.

Stdlib only, no matplotlib: the body is a string the tool writes beside its figure data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SPEC_KIND = "paper_tables/v0.1"
FORMATS = ("one_decimal", "two_decimals", "value_with_interval", "integer", "clean_of_runs", "text")
ORDER_RULES = ("source", "by", "shaded_first")
CELL_RULES = ("clean_below_runs",)
COLLAPSE_RULES = ("every_cell_clean",)
ROW_KINDS = ("agents", "objects")
TABLE_FIELDS = ("table", "body", "sources", "column_spec", "tabcolsep_pt", "columns", "order")
_ESCAPES = (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"),
            ("_", r"\_"), ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}"))


def escape(text) -> str:
    """Plain text made safe for a LaTeX cell (row labels, text cells); headers and markers are authored LaTeX
    and never pass through here."""
    out = str(text)
    for raw, rep in _ESCAPES:
        out = out.replace(raw, rep)
    return out


def load_spec(path) -> tuple:
    """(spec, sha256) of a paper-tables spec file, checked."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    check_spec(doc)
    return doc, hashlib.sha256(raw).hexdigest()


def check_spec(doc: dict) -> None:
    """The spec's contract: its kind, its formats among the admitted ones, every table row complete and its rules
    from the admitted vocabulary. A refusal names the contract and the vocabulary."""
    if not isinstance(doc, dict) or doc.get("spec_kind") != SPEC_KIND:
        raise ValueError(f"a paper-tables spec is {SPEC_KIND}; got spec_kind {doc.get('spec_kind') if isinstance(doc, dict) else doc!r}")
    bad = sorted(set(doc.get("formats") or []) - set(FORMATS))
    if bad:
        raise ValueError(f"formats {bad} are not admitted; the formats are {list(FORMATS)}")
    if not isinstance(doc.get("missing"), str):
        raise ValueError("the spec declares `missing`, the string a cell without a value prints")
    tables = doc.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("the spec holds `tables`, a list with one row per paper table")
    seen = set()
    for t in tables:
        missing = [f for f in TABLE_FIELDS if f not in t]
        if missing:
            raise ValueError(f"table {t.get('table')!r} lacks {missing}; a table row holds {list(TABLE_FIELDS)}")
        if t["table"] in seen:
            raise ValueError(f"table {t['table']!r} is declared twice; the table id is the key")
        seen.add(t["table"])
        kind = t.get("rows_are", "agents")
        if kind not in ROW_KINDS:
            raise ValueError(f"table {t['table']!r}: rows_are {kind!r} is not one of {list(ROW_KINDS)}")
        if kind == "agents" and not t.get("blocks"):
            raise ValueError(f"table {t['table']!r}: rows of agents come in `blocks`, each with its `agents` (and a label)")
        if kind == "objects" and not t.get("agents"):
            raise ValueError(f"table {t['table']!r}: rows of objects take the printed `agents` as columns")
        cols = t["columns"]
        if not cols or "header" not in cols[0] or any(k in cols[0] for k in ("source", "format")):
            raise ValueError(f"table {t['table']!r}: the first column is the label column, a header only")
        for c in cols[1:]:
            if c.get("format") not in FORMATS:
                raise ValueError(f"table {t['table']!r}: column {c.get('header')!r} format {c.get('format')!r} is not one of {list(FORMATS)}")
            if not isinstance(c.get("source"), str) or ":" not in c["source"]:
                raise ValueError(f"table {t['table']!r}: column {c.get('header')!r} names its source as '<table id>:<dotted path>'")
        order_rule(t["order"])          # refuses by name
        if t.get("bold") is not None:
            b = t["bold"]
            if not isinstance(b.get("columns"), list) or not b["columns"]:
                raise ValueError(f"table {t['table']!r}: bold names the `columns` (headers) it marks")
        if t.get("cell") is not None:
            c = t["cell"]
            if c.get("shade_when") not in CELL_RULES:
                raise ValueError(f"table {t['table']!r}: cell.shade_when {c.get('shade_when')!r} is not one of {list(CELL_RULES)}")
            if not isinstance(c.get("codes"), dict):
                raise ValueError(f"table {t['table']!r}: cell.codes maps an outcome to its printed letter")
        if t.get("collapse") is not None:
            c = t["collapse"]
            if c.get("when") not in COLLAPSE_RULES:
                raise ValueError(f"table {t['table']!r}: collapse.when {c.get('when')!r} is not one of {list(COLLAPSE_RULES)}")
            if "{n}" not in str(c.get("label", "")):
                raise ValueError(f"table {t['table']!r}: collapse.label carries '{{n}}', the count of the rows it folds")
        if t.get("footnote") is not None and not t["footnote"].get("marker"):
            raise ValueError(f"table {t['table']!r}: footnote names its `marker` (authored LaTeX) and its `when` source")


def order_rule(order) -> tuple:
    """(rule, argument) of a table's order field: 'source', {'by': header, 'descending': bool} or
    {'shaded_first': true, 'then': 'family'}."""
    if order == "source":
        return "source", None
    if isinstance(order, dict) and "by" in order:
        return "by", order
    if isinstance(order, dict) and order.get("shaded_first"):
        return "shaded_first", order
    raise ValueError(f"order {order!r} is not one of {list(ORDER_RULES)} ('source', {{'by': <header>, 'descending': bool}}, "
                     f"{{'shaded_first': true, 'then': 'family'}})")


def parse_source(ref: str) -> tuple:
    """('T7', 'cells.{family}.{agent}.mean_all') of a column's source reference."""
    table, _, path = ref.partition(":")
    if not table or not path:
        raise ValueError(f"source {ref!r} is '<table id>:<dotted path>' with {{agent}}, {{object}}, {{family}} placeholders")
    return table, path


def resolve(tables: dict, ref: str, **names) -> object:
    """The object a source reference names in the built tables: the table by id, then the dotted path with its
    placeholders filled; a missing table or path refuses with what is there."""
    tid, path = parse_source(ref)
    if tid not in tables:
        raise ValueError(f"source {ref!r}: table {tid!r} is not among the built tables {sorted(tables)}")
    try:
        filled = path.format(**names)
    except KeyError as exc:
        raise ValueError(f"source {ref!r}: placeholder {exc} has no name among {sorted(names)}") from None
    node = tables[tid]
    if isinstance(node, dict) and filled in node:      # a flat key (the numbers document names its entries with dots)
        return node[filled]
    for part in filled.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            where = sorted(node)[:12] if isinstance(node, dict) else type(node).__name__
            raise ValueError(f"source {ref!r}: {filled!r} has no {part!r} (there: {where})")
    return node


def cell_of(obj, fmt: str) -> dict | None:
    """A resolved source object as the typed cell its column's format reads: a number, a numbers-document entry
    ({value, ...} with the value possibly carrying its interval), a ladder cell ({clean, n, other_outcomes}) or
    text; None stays missing."""
    if obj is None:
        return None
    if fmt in ("one_decimal", "two_decimals", "integer"):
        v = _number(obj)
        return None if v is None else {"value": v}
    if fmt == "value_with_interval":
        if isinstance(obj, dict):
            inner = obj.get("value") if isinstance(obj.get("value"), dict) else obj
            v, iv = _number(inner.get("value")), inner.get("interval")
            return None if v is None else {"value": v, "interval": iv}
        return {"value": _number(obj), "interval": None}
    if fmt == "clean_of_runs":
        if isinstance(obj, dict) and isinstance(obj.get("value"), dict):
            obj = obj["value"]
        if not isinstance(obj, dict) or "clean" not in obj or "n" not in obj:
            raise ValueError(f"a clean_of_runs cell is {{clean, n, other_outcomes}}; got {obj!r}")
        return {"clean": int(obj["clean"]), "n": int(obj["n"]), "other": dict(obj.get("other_outcomes") or obj.get("other") or {})}
    if fmt == "text":
        return {"text": str(obj)}
    raise ValueError(f"format {fmt!r} is not one of {list(FORMATS)}")


def _number(obj):
    if isinstance(obj, bool):
        raise ValueError(f"a numeric cell holds a number, got {obj!r}")
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, dict):
        inner = obj.get("value")
        if isinstance(inner, dict):
            inner = inner.get("value")
        if inner is None or isinstance(inner, (int, float)) and not isinstance(inner, bool):
            return inner
    raise ValueError(f"a numeric cell holds a number or a numbers-document entry with one; got {obj!r}")


def _fmt_number(v, fmt, bold=False) -> str:
    if fmt == "one_decimal":
        s = f"{v:.1f}"
    elif fmt == "two_decimals":
        s = f"{v:.2f}"
    elif fmt == "integer":
        if float(v) != int(v):
            raise ValueError(f"an integer cell holds a whole number; got {v!r}")
        s = f"{int(v)}"
    else:
        raise ValueError(f"format {fmt!r} is not numeric")
    return f"\\textbf{{{s}}}" if bold else s


def format_cell(cell, fmt: str, missing: str, bold=False, cell_rule=None) -> str:
    """One cell's printed form under its column's format and the table's cell rule; the bold flag marks the value
    only (an interval stays plain)."""
    if cell is None:
        return missing
    if fmt in ("one_decimal", "two_decimals", "integer"):
        if "value" not in cell or cell["value"] is None:
            return missing
        return _fmt_number(cell["value"], fmt, bold)
    if fmt == "value_with_interval":
        if cell.get("value") is None:
            return missing
        v = _fmt_number(cell["value"], "two_decimals", bold)
        iv = cell.get("interval")
        if iv is None:
            return v
        return f"{v} [{iv[0]:.2f}, {iv[1]:.2f}]"
    if fmt == "clean_of_runs":
        s = f"{cell['clean']}/{cell['n']}"
        if cell_rule is not None and shaded(cell, cell_rule):
            codes = cell_rule["codes"]
            unknown = sorted(o for o in cell.get("other", {}) if o not in codes)
            if unknown:
                raise ValueError(f"outcomes {unknown} have no code; the codes are {codes}")
            marks = " ".join(f"{cell['other'][o]}{letter}" for o, letter in codes.items() if cell["other"].get(o))
            s = f"\\cellcolor{{{cell_rule['shade']}}}{s}" + (f"{cell_rule.get('code_separator', ' ')}{marks}" if marks else "")
        return s
    if fmt == "text":
        return escape(cell.get("text", ""))
    raise ValueError(f"format {fmt!r} is not one of {list(FORMATS)}")


def shaded(cell, cell_rule) -> bool:
    """Whether the cell rule shades a cell: clean_below_runs, fewer clean runs than runs."""
    if cell is None:
        return False
    if cell_rule["shade_when"] == "clean_below_runs":
        return "clean" in cell and cell["clean"] < cell["n"]
    raise ValueError(f"cell rule {cell_rule['shade_when']!r} is not one of {list(CELL_RULES)}")


def _col_index(columns, header) -> int:
    for i, c in enumerate(columns[1:]):
        if c["header"] == header:
            return i
    raise ValueError(f"column {header!r} is not among {[c['header'] for c in columns[1:]]}")


def ordered_rows(rows: list, columns: list, order, cell_rule=None) -> list:
    """The rows of one block in the table's order: as given (source), by a column's value descending or ascending
    (missing values last), or the shaded rows first by their shaded-cell count, ties in the given (family) order."""
    rule, arg = order_rule(order)
    if rule == "source":
        return list(rows)
    if rule == "by":
        j = _col_index(columns, arg["by"])
        desc = bool(arg.get("descending", True))

        def key(r):
            c = r["cells"][j]
            v = None if c is None else c.get("value")
            return (v is None, (-v if desc else v) if v is not None else 0)
        return sorted(rows, key=key)
    if cell_rule is None:
        raise ValueError("order shaded_first needs the table's cell rule (which cells are shaded)")
    return sorted(rows, key=lambda r: -sum(shaded(c, cell_rule) for c in r["cells"]))


def collapse_rows(rows: list, collapse, cell_rule) -> tuple:
    """(kept rows, collapsed row or None) under the collapse rule every_cell_clean: the rows whose every cell is a
    clean_of_runs cell with clean == n fold into one row of summed cells, labelled by the pattern with n = how many
    rows folded; a row with a missing cell never folds."""
    if collapse is None:
        return list(rows), None
    if collapse["when"] != "every_cell_clean":
        raise ValueError(f"collapse rule {collapse['when']!r} is not one of {list(COLLAPSE_RULES)}")

    def clean(r):
        return all(c is not None and "clean" in c and c["clean"] == c["n"] for c in r["cells"])
    folded = [r for r in rows if clean(r)]
    kept = [r for r in rows if not clean(r)]
    if not folded:
        return kept, None
    cells = []
    for j in range(len(folded[0]["cells"])):
        cells.append({"clean": sum(r["cells"][j]["clean"] for r in folded), "n": sum(r["cells"][j]["n"] for r in folded), "other": {}})
    return kept, {"id": "collapsed", "label": collapse["label"].format(n=len(folded)), "cells": cells,
                  "footnote_value": None, "members": [r["id"] for r in folded]}


def bold_cells(blocks: list, columns: list, bold) -> set:
    """{(block index, row id, column index)} of the cells the bold rule marks: in the named block (or every block),
    for each named column, the rows whose value is the largest among the rows not excluded."""
    marks = set()
    if bold is None:
        return marks
    excluded = set(bold.get("exclude") or [])
    for bi, b in enumerate(blocks):
        if bold.get("block") is not None and b.get("label") != bold["block"]:
            continue
        for header in bold["columns"]:
            j = _col_index(columns, header)
            vals = [(r["cells"][j]["value"], r["id"]) for r in b["rows"]
                    if r["id"] not in excluded and r["cells"][j] is not None and r["cells"][j].get("value") is not None]
            if not vals:
                continue
            top = max(v for v, _ in vals)
            marks.update((bi, rid, j) for v, rid in vals if v == top)
    return marks


def header_lines(columns: list) -> list:
    """The header: a group line with \\multicolumn and \\cmidrule when columns declare groups, then the headers."""
    lines = []
    groups = []
    for i, c in enumerate(columns):
        g = c.get("group")
        if g is not None and groups and groups[-1][0] == g and groups[-1][2] == i - 1:
            groups[-1][2] = i
        else:
            groups.append([g, i, i])
    if any(g for g, _, _ in groups):
        parts, rules = [], []
        last = len(columns) - 1
        for g, a, b in groups:
            if g is None:
                parts.extend([""] * (b - a + 1))
            else:
                parts.append(f"\\multicolumn{{{b - a + 1}}}{{c}}{{{g}}}")
                trim = ("l" if a > 0 else "") + ("r" if b < last else "")
                rules.append(f"\\cmidrule({trim}){{{a + 1}-{b + 1}}}")
        lines.append(" & ".join(parts) + " \\\\")
        lines.append("".join(rules))
    lines.append(" & ".join(c["header"] for c in columns) + " \\\\")
    return lines


def body(grid: dict, spec: dict, missing: str) -> str:
    """The tabular body of one paper table: the column spacing, the tabular with its header, the blocks (each with its
    label line, its rows in the table's order, the collapsed row under its rule) and the bottom rule."""
    columns = grid["columns"]
    cell_rule = spec.get("cell")
    n_cols = len(columns)
    blocks = []
    for b in grid["blocks"]:
        rows = ordered_rows(b["rows"], columns, spec["order"], cell_rule)
        rows, folded = collapse_rows(rows, spec.get("collapse"), cell_rule)
        blocks.append({"label": b.get("label"), "rows": rows, "folded": folded})
    marks = bold_cells([{"label": b["label"], "rows": b["rows"]} for b in blocks], columns, spec.get("bold"))
    footnote = spec.get("footnote")
    printed = []     # (block index, label text, [cell texts]) in print order, with None rows for rule lines
    for bi, b in enumerate(blocks):
        for r in b["rows"]:
            label = escape(r["label"])
            if footnote is not None and r.get("footnote_value") not in (None, 0, 0.0, False):
                label += footnote["marker"]
            cells = [format_cell(c, columns[j + 1]["format"], missing, bold=(bi, r["id"], j) in marks, cell_rule=cell_rule)
                     for j, c in enumerate(r["cells"])]
            printed.append((bi, label, cells, False))
        if b["folded"] is not None:
            f = b["folded"]
            printed.append((bi, escape(f["label"]), [format_cell(c, columns[j + 1]["format"], missing, cell_rule=cell_rule)
                                                      for j, c in enumerate(f["cells"])], True))
    widths = [max((len(p[1]) for p in printed), default=0)] + [max((len(p[2][j]) for p in printed), default=0) for j in range(n_cols - 1)]
    lines = [f"\\setlength{{\\tabcolsep}}{{{grid['tabcolsep_pt']:g}pt}}", f"\\begin{{tabular}}{{{grid['column_spec']}}}", "\\toprule"]
    lines.extend(header_lines(columns))
    lines.append("\\midrule")
    for bi, b in enumerate(blocks):
        if bi > 0:
            lines.append("\\midrule")
        if b["label"] is not None:
            lines.append(f"\\multicolumn{{{n_cols}}}{{@{{}}l}}{{\\emph{{{b['label']}}}}} \\\\")
        rows_here = [p for p in printed if p[0] == bi]
        for k, (_, label, cells, is_folded) in enumerate(rows_here):
            if is_folded and k > 0 and spec.get("collapse", {}).get("rule_before") == "midrule":
                lines.append("\\midrule")
            padded = [label.ljust(widths[0])] + [c.ljust(widths[j + 1]) for j, c in enumerate(cells)]
            lines.append(" & ".join(padded).rstrip() + " \\\\")
    lines.extend(["\\bottomrule", "\\end{tabular}"])
    return "\n".join(lines) + "\n"
