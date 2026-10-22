#!/usr/bin/env python3
"""Check a paper's LaTeX source against the pipeline's numbers document TN, binding by binding (v0.2, F97 2026-10-19).

    conda run -n scl_prototype python scripts/check_paper_numbers.py --paper ~/scl/paper \\
        --numbers <render>/figure_data/TN.json [--render <render>] [--deltas] [--words] [--out report.json] [--strict]

Every number the paper's text files print in digits is bound in the source to the TN entry it comes from, with one of
two macros the paper's preamble defines (both typeset their printed argument only, the other argument is read by this
tool and discarded by TeX):
    \\pn{<entry name>}{<printed form>}      a pipeline number       \\newcommand{\\pn}[2]{#2}
    \\pn{<name>,<name>,...}{<printed form>}  a number the sentence claims for several entries at once (three agents
                                            that share a boundary): every name must exist and print the form
    \\pq{<printed form>}{<origin>}          a number from outside   \\newcommand{\\pq}[2]{#1}
The checks, all reported, one exit code:
  bindings         every \\pn: the name is a TN entry and the printed form equals one of the entry's prints (made
                   plain; an interval is bound as one form, "[0.67, 0.81]"); every \\pq is recorded with its origin,
                   counted, never judged;
  unbound          every numeral outside a binding is a defect: a digit run in the prose or a caption, with the
                   exclusions below, each counted in the report, never silent;
  generated        with --render DIR: every member under fig/atlas/ the source includes (\\includegraphics, \\input) is
                   compared by sha256 with the file of the same name under DIR: fresh, stale, or missing; a member
                   outside fig/atlas/ is hand-made and listed as outside the render;
  named_not_bound  the TN entries no binding uses (a number the paper could cite and does not), as in v0.1;
  deltas           with --deltas: for every \\pn whose printed form is no longer among its entry's prints, FIND (the
                   macro as it stands) and REPLACE (the macro with the entry's print of the same shape: the same count
                   of numbers with the same decimals in the same skeleton); an entry with no print of that shape, or
                   several, is a named case for a hand decision, never a guess;
  number_words     every small count written as a word ("three seeds", "once") outside a binding, listed with its
                   sentence for the human read, never refused.
Exclusions, declared here and counted in the report: comments; math ($...$, \\(...\\), \\[...\\], the equation, align,
split, gather, multline and eqnarray environments); the arguments of the citation, reference, label, graphics, input,
url and layout macros (LAYOUT_MACROS); years 1900 to 2100; a version after a product name (PRODUCT_NAMES); an
identifier, a noun then a number or a list of them ("seed 2", "Checkpoint 1", "checkpoints 2 and 3", "seeds 0 to 2":
names of seeds and checkpoints, never counts; IDENTIFIER_NOUNS); digits inside a word
(RegNetY-032, Town13, v01); a length glued to a TeX unit (5pt, 0.9\\linewidth); the generated table bodies under
fig/atlas/tables/, never scanned, checked by hash. The source read: main.tex, sec/*.tex and fig/**/*.tex under --paper.
Exit 0 when every numeral is bound, every binding holds and every generated member is fresh; with --strict, 1
otherwise. The report is a JSON document (paper_numbers_check/v0.2) when --out is given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PN, PQ = "pn", "pq"                       # the macro names the preamble defines
GENERATED_BODIES = "fig/atlas/tables/"    # generated table bodies: hashed, never scanned
RENDER_ROOT = "fig/atlas/"                # the paper's generated figures and bodies live here

PN_RE = re.compile(r"\\" + PN + r"\{([^{}]*)\}\{([^{}]*)\}")
PQ_RE = re.compile(r"\\" + PQ + r"\{([^{}]*)\}\{([^{}]*)\}")
MACRO_HEAD_RE = re.compile(r"\\(?:" + PN + "|" + PQ + r")\b")   # a macro the two patterns above did not consume: malformed

MATH_RES = [re.compile(p, re.S) for p in (
    r"\$\$.*?\$\$", r"(?<!\\)\$[^$]*?(?<!\\)\$", r"\\\(.*?\\\)", r"\\\[.*?\\\]",
    r"\\begin\{(equation|align|alignat|split|gather|multline|eqnarray|displaymath)\*?\}.*?\\end\{\1\*?\}")]
REFERENCE_MACROS = ("cite", "citep", "citet", "citealp", "ref", "autoref", "cref", "Cref", "eqref", "pageref", "label",
                    "hyperref", "nameref")
GRAPHICS_MACROS = ("includegraphics", "input", "include", "bibliography", "bibliographystyle", "usepackage",
                   "documentclass", "url", "href", "graphicspath")
LAYOUT_MACROS = ("setlength", "addtolength", "vspace", "hspace", "vskip", "hskip", "rule", "resizebox", "scalebox",
                 "multicolumn", "multirow", "cmidrule", "cline", "parbox", "makebox", "raisebox", "renewcommand",
                 "newcommand", "providecommand", "definecolor", "newcolumntype", "arrayrulecolor", "rowcolor",
                 "cellcolor", "fontsize", "linespread", "setcounter", "addtocounter", "hyphenation", "captionsetup",
                 "begin", "end", "columnwidth", "linewidth", "textwidth", "hline", "toprule", "midrule", "bottomrule",
                 "addlinespace", "arraystretch", "tabcolsep", "pagestyle", "thispagestyle", "numberwithin", "newpage",
                 "clearpage", "pdfoutput")
FORMAT_HEADS = ("textbf", "textit", "emph", "texttt", "textsc", "textrm", "textsf", "mbox", "text", "caption",
                "paragraph", "section", "subsection", "subsubsection", "footnote", "underline", "small",
                "footnotesize", "scriptsize", "centering", "noindent", "item", "maketitle", "and", "title", "author",
                "tt", "bf", "it", "em", "sl", "quad", "qquad", "par", "newline", "linebreak", "nolinebreak", "hfill",
                "vfill", "smallskip", "medskip", "bigskip", "ldots", "dots", "cdot", "times", "degree", "textdegree")
PRODUCT_NAMES = ("PlanT", "CARLA", "Leaderboard", "Isaac Sim", "Bench2Drive", "Fail2Drive", "Python", "Ubuntu", "CUDA",
                 "TeX Live", "Town")
IDENTIFIER_NOUNS = ("seed", "Seed", "checkpoint", "Checkpoint", "ckpt", "Figure", "figure", "Fig.", "Table", "table",
                    "Section", "section", "Sec.", "subsection", "Subsection", "Eq.", "equation", "Equation", "page",
                    "Page", "footnote", "line", "Line", "step", "Step", "version", "Version", "arXiv:")
TEX_UNITS = ("pt", "em", "ex", "bp", "pc", "sp", r"\linewidth", r"\textwidth", r"\columnwidth", r"\textheight")
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
                "thirty", "forty", "fifty", "hundred", "thousand", "once", "twice", "half", "dozen")

# a numeral: a thousands-grouped integer, or digits with an optional decimal part; it ends before a word character
# and before ".digit" (so a numeral a sentence ends on is read, "scores 0.34.", 2026-10-17), and it does not start
# inside a word, a decimal or a thousands group
NUMERAL_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?!\w)(?!\.\d)(?!,\d{3})")
YEAR_RE = re.compile(r"^(?:19\d\d|20\d\d|2100)$")
VERSION_RE = re.compile(r"(?:" + "|".join(re.escape(n) for n in PRODUCT_NAMES) + r")\s*~?\s*v?(\d+(?:\.\d+)*)")
IDENTIFIER_RE = re.compile(r"(?:" + "|".join(re.escape(n) for n in IDENTIFIER_NOUNS) + r")s?\s*~?\s*(\d+)\b"
                           r"(?:\s*(?:,|and|to|or)\s*(\d+)\b)*")
LENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)(?:" + "|".join(re.escape(u) for u in TEX_UNITS) + r")(?![A-Za-z])")
WORD_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.I)
COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
HEAD_RE = re.compile(r"\\(?:" + "|".join(FORMAT_HEADS) + r")\*?\s*(?=\{|\s|$|\\)")


# ----------------------------------------------------------------------------------------------- the source, read
def _consume_args(text: str, pos: int) -> int:
    """From pos, consume any run of [..], (..) and balanced {..} argument groups; returns the end offset."""
    n = len(text)
    while pos < n:
        c = text[pos]
        if c == "*":
            pos += 1
        elif c == "[":
            j = text.find("]", pos)
            if j < 0:
                return pos
            pos = j + 1
        elif c == "{":
            depth, j = 0, pos
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            pos = j + 1
        elif c == "(" and text[max(0, pos - 9):pos].endswith(("cmidrule", "cline", "rule")):
            j = text.find(")", pos)
            pos = j + 1 if j >= 0 else pos
        else:
            return pos
    return pos


def _macro_spans(text: str, names) -> list:
    spans = []
    pat = re.compile(r"\\(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z])")
    for m in pat.finditer(text):
        end = _consume_args(text, m.end())
        spans.append((m.start(), end))
    return spans


def _display(text: str):
    """The display text of one file: comments gone, the two macros replaced by their printed forms with the spans of
    those forms recorded (bindings), whitespace collapsed, TeX spacing and escapes made plain. Returns
    (display_text, bindings) where each binding is {"macro", "name"|"origin", "printed", "span", "raw"}."""
    text = _plain(COMMENT_RE.sub(" ", text))
    out, bindings, pos = [], [], 0
    both = sorted([(m.start(), m, PN) for m in PN_RE.finditer(text)] + [(m.start(), m, PQ) for m in PQ_RE.finditer(text)])
    for start, m, kind in both:
        out.append(text[pos:start])
        printed = (m.group(2) if kind == PN else m.group(1))
        here = sum(len(s) for s in out)
        out.append(printed)
        b = {"macro": kind, "printed": printed, "raw": m.group(0), "span": (here, here + len(printed))}
        if kind == PN:
            b["name"] = m.group(1)
        else:
            b["origin"] = m.group(2)
        bindings.append(b)
        pos = m.end()
    out.append(text[pos:])
    disp = "".join(out)
    # collapse runs of whitespace to one space, re-mapping the binding spans through the collapse: offs[k] is the
    # offset in the collapsed text of the display character k (a dropped whitespace maps to the kept space before it)
    collapsed, offs, prev_space = [], [], False
    for ch in disp:
        if ch.isspace():
            if not prev_space:
                collapsed.append(" ")
            offs.append(len(collapsed) - 1)
            prev_space = True
        else:
            offs.append(len(collapsed)); collapsed.append(ch); prev_space = False
    offs.append(len(collapsed))
    for b in bindings:
        s, e = b["span"]
        b["span"] = (offs[s], offs[e])
    disp = "".join(collapsed)
    return disp, bindings


def _plain(s: str) -> str:
    """A string as the reader sees it: thin spaces, ties and escapes made plain."""
    return (s.replace("\\,", " ").replace("~", " ").replace("\\%", "%").replace("\\&", "&").replace("\\_", "_")
            .replace("{,}", ",").replace("--", "-"))


def _protected_spans(disp: str) -> list:
    """(start, end, kind) for every region whose digits are not the paper's numbers."""
    spans = []
    for r in MATH_RES:
        spans += [(m.start(), m.end(), "math") for m in r.finditer(disp)]
    spans += [(s, e, "reference") for s, e in _macro_spans(disp, REFERENCE_MACROS)]
    spans += [(s, e, "graphics") for s, e in _macro_spans(disp, GRAPHICS_MACROS)]
    spans += [(s, e, "layout") for s, e in _macro_spans(disp, LAYOUT_MACROS)]
    return spans


def _in(spans, start, end):
    for s, e, *rest in spans:
        if s <= start and end <= e:
            return rest[0] if rest else True
    return None


def sentence_spans(disp: str) -> list:
    """(start, end) of each sentence of a display text: a sentence ends at punctuation followed by whitespace."""
    spans, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+", disp):
        spans.append((start, m.start())); start = m.end()
    spans.append((start, len(disp)))
    return spans


def _sentence_of(disp: str, spans: list, pos: int) -> str:
    for s, e in spans:
        if s <= pos < e:
            return _clean_sentence(disp[s:e])
    return _clean_sentence(disp[max(0, pos - 120):pos + 120])


NOISE_RE = re.compile(r"\\(?:" + "|".join(re.escape(n) for n in REFERENCE_MACROS + GRAPHICS_MACROS)
                      + r")\*?(?:\[[^\]]*\])?\{[^{}]*\}")


def _clean_sentence(s: str) -> str:
    s = NOISE_RE.sub(" ", s)
    s = HEAD_RE.sub("", s)
    s = re.sub(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?", " ", s)
    s = _plain(s).replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()[:240]


def load_sources(paper: Path) -> dict:
    """path -> raw text, for main.tex, sec/*.tex and fig/**/*.tex; the generated bodies are listed under their own key."""
    files = sorted([paper / "main.tex"] * (paper / "main.tex").exists()
                   + list((paper / "sec").glob("*.tex")) + list((paper / "fig").rglob("*.tex")))
    return {str(f.relative_to(paper)): f.read_text(encoding="utf-8") for f in files}


# ----------------------------------------------------------------------------------------------- the checks
def is_covered(tok: str, prints) -> bool:
    """A printed form is covered when it EQUALS one of the entry's prints, made plain (thin spaces, ties and escapes
    removed, whitespace collapsed). History: 2026-10-16 any substring; 2026-10-17 a whole number token inside a print
    ("0.69" in "[0.69, 0.83]"), after a wrong digit passed inside a longer number; 2026-10-19 (F97) equality, after
    the whole-token rule bound "0.79" to an entry whose interval ended in 0.79 and "10" to the 10,000 replicates. A
    paper prints an interval as one form, "[0.67, 0.81]", and binds it whole; an interval end on its own is a number
    of its own and needs an entry that prints it."""
    tok = re.sub(r"\s+", " ", _plain(tok)).strip()
    return any(tok == re.sub(r"\s+", " ", _plain(p)).strip() for p in prints)


def shape(printed: str) -> str:
    """The skeleton of a printed form: every number replaced by #<decimals>; "13 of its 29" -> "#0 of its #0"."""
    def sub(m):
        t = m.group(0).replace(",", "")
        return "#" + str(len(t.split(".")[1]) if "." in t else 0)
    return NUMERAL_RE.sub(sub, _plain(printed).strip())


def classify(text: str, bound_spans: list) -> list:
    """Every numeral of a text with its kind: None when it is unbound (the paper's number, not yet bound), "bound"
    inside one of bound_spans, or the exclusion it falls under (math, reference, graphics, layout, in_word, length,
    version, identifier, year). Offsets are the text's own, so a caller may classify raw source text as well as
    the display text (the binder does)."""
    prot = _protected_spans(text)
    versions = {m.start(1) for m in VERSION_RE.finditer(text)}
    identifiers = set()
    for m in IDENTIFIER_RE.finditer(text):                  # every number of the list is the identifier's
        identifiers |= {n.start() for n in NUMERAL_RE.finditer(text, m.start(), m.end())}
    lengths = {m.start(1) for m in LENGTH_RE.finditer(text)}
    rows = []
    for m in NUMERAL_RE.finditer(text):
        s, e, tok = m.start(), m.end(), m.group(0)
        if _in(bound_spans, s, e) is not None:
            rows.append({"start": s, "end": e, "number": tok, "kind": "bound"}); continue
        kind = _in(prot, s, e)
        if kind is None:
            before = text[s - 1] if s > 0 else " "
            after = text[e] if e < len(text) else " "
            if before.isalpha() or before in "-_" or after.isalpha() or after == "_":
                kind = "in_word"
            elif s in lengths:
                kind = "length"
            elif s in versions:
                kind = "version"
            elif s in identifiers:
                kind = "identifier"
            elif YEAR_RE.match(tok):
                kind = "year"
        rows.append({"start": s, "end": e, "number": tok, "kind": kind})
    return rows


def scan_file(rel: str, raw: str, numbers: dict) -> dict:
    disp, bindings = _display(raw)
    prot = _protected_spans(disp)
    sents = sentence_spans(disp)
    bound_spans = [(b["span"][0], b["span"][1]) for b in bindings]
    out = {"bindings": [], "quoted": [], "unbound": [], "excluded": {}, "number_words": [], "malformed": []}
    for b in bindings:
        sent = _sentence_of(disp, sents, b["span"][0])
        if b["macro"] == PN:
            names = [n.strip() for n in b["name"].split(",") if n.strip()]
            entries = [numbers.get(n) for n in names]
            if not names or any(e is None for e in entries):
                verdict = "unknown name"
            elif not re.search(r"\d|" + WORD_RE.pattern, b["printed"], re.I):
                verdict = "empty printed form"
            elif all(is_covered(b["printed"], e.get("prints", [])) for e in entries):
                verdict = "verified"
            else:
                verdict = "wrong"
            prints = sorted(set.intersection(*[set(e.get("prints", [])) for e in entries if e])) if entries and all(entries) else []
            out["bindings"].append({"file": rel, "name": b["name"], "names": names, "printed": b["printed"], "verdict": verdict,
                                    "prints": prints, "raw": b["raw"], "sentence": sent})
        else:
            problem = None if (re.search(r"\d", b["printed"]) and b["origin"].strip()) else "a quoted number needs digits and an origin"
            out["quoted"].append({"file": rel, "printed": b["printed"], "origin": b["origin"], "raw": b["raw"],
                                  "sentence": sent, **({"problem": problem} if problem else {})})
    for m in MACRO_HEAD_RE.finditer(disp):
        if _in(bound_spans, m.start(), m.end()) is None:
            out["malformed"].append({"file": rel, "at": _sentence_of(disp, sents, m.start())})
    for r in classify(disp, bound_spans):
        if r["kind"] == "bound":
            continue
        row = {"file": rel, "number": r["number"], "sentence": _sentence_of(disp, sents, r["start"])}
        if r["kind"] is None:
            out["unbound"].append(row)
        else:
            out["excluded"].setdefault(r["kind"], []).append(row)
    for m in WORD_RE.finditer(disp):
        if _in(bound_spans, m.start(), m.end()) is None and _in(prot, m.start(), m.end()) is None:
            out["number_words"].append({"file": rel, "word": m.group(0), "sentence": _sentence_of(disp, sents, m.start())})
    return out


def generated_members(sources: dict, paper: Path, render) -> list:
    """Every file the source includes under fig/atlas/ (graphics and bodies), hashed against the render."""
    seen, members = set(), []
    inc = re.compile(r"\\(includegraphics|input|include)\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
    for rel, raw in sources.items():
        for m in inc.finditer(COMMENT_RE.sub(" ", raw)):
            kind, path = m.group(1), m.group(2).strip()
            if kind != "includegraphics" and not path.endswith(".tex"):
                path += ".tex"
            if kind != "includegraphics" and not path.startswith(GENERATED_BODIES):
                continue                                   # a hand-written input is a source file, scanned above
            if path in seen:
                continue
            seen.add(path)
            p = paper / path
            row = {"member": path, "included_by": rel}
            if not path.startswith(RENDER_ROOT):
                row["verdict"] = "outside the render"
            elif not p.exists():
                row["verdict"] = "missing in the paper"
            elif render is None:
                row["verdict"] = "not checked"
            else:
                hits = [h for h in Path(render).rglob(p.name) if h.is_file()]
                row["paper_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
                if not hits:
                    row["verdict"] = "missing in the render"
                elif len(hits) > 1:
                    row["verdict"] = "several in the render"; row["render_paths"] = [str(h) for h in hits]
                else:
                    row["render_sha256"] = hashlib.sha256(hits[0].read_bytes()).hexdigest()
                    row["render_path"] = str(hits[0])
                    row["verdict"] = "fresh" if row["render_sha256"] == row["paper_sha256"] else "stale"
            members.append(row)
    return members


def deltas(bindings: list) -> list:
    """FIND/REPLACE for every wrong binding whose entry has exactly one print of the printed form's shape."""
    out = []
    for b in bindings:
        if b["verdict"] != "wrong":
            continue
        sh = shape(b["printed"])
        same = [p for p in b["prints"] if shape(p) == sh]
        row = {"file": b["file"], "name": b["name"], "find": b["raw"], "shape": sh}
        if len(same) == 1:
            row["replace"] = "\\%s{%s}{%s}" % (PN, b["name"], same[0])
        elif not same:
            row["case"] = "no print of this shape shared by every named entry; choose by hand from " + json.dumps(b["prints"])
        else:
            row["case"] = "several prints of this shape; choose by hand from " + json.dumps(same)
        out.append(row)
    return out


def check(paper: Path, numbers: dict, render=None) -> dict:
    sources = load_sources(paper)
    rep = {"report_kind": "paper_numbers_check/v0.2", "paper": str(paper), "render": str(render) if render else None,
           "numbers": len(numbers), "files": [], "bindings": [], "quoted": [], "unbound": [], "excluded": {},
           "number_words": [], "malformed": []}
    for rel, raw in sources.items():
        if rel.startswith(GENERATED_BODIES):
            continue
        rep["files"].append(rel)
        r = scan_file(rel, raw, numbers)
        for k in ("bindings", "quoted", "unbound", "number_words", "malformed"):
            rep[k] += r[k]
        for k, v in r["excluded"].items():
            rep["excluded"].setdefault(k, []).extend(v)
    rep["generated"] = generated_members(sources, paper, render)
    used = {n for b in rep["bindings"] for n in b["names"]}
    rep["named_not_bound"] = sorted(n for n in numbers if n not in used)
    rep["deltas"] = deltas(rep["bindings"])
    counts = {"bindings": len(rep["bindings"]),
              "verified": sum(b["verdict"] == "verified" for b in rep["bindings"]),
              "wrong": sum(b["verdict"] == "wrong" for b in rep["bindings"]),
              "unknown_name": sum(b["verdict"] == "unknown name" for b in rep["bindings"]),
              "empty": sum(b["verdict"] == "empty printed form" for b in rep["bindings"]),
              "quoted": len(rep["quoted"]), "quoted_problems": sum("problem" in q for q in rep["quoted"]),
              "unbound": len(rep["unbound"]), "malformed": len(rep["malformed"]),
              "excluded": {k: len(v) for k, v in sorted(rep["excluded"].items())},
              "number_words": len(rep["number_words"]),
              "generated": {v: sum(g["verdict"] == v for g in rep["generated"])
                            for v in sorted({g["verdict"] for g in rep["generated"]})},
              "named_not_bound": len(rep["named_not_bound"]), "deltas": len(rep["deltas"])}
    rep["counts"] = counts
    rep["clean"] = (counts["unbound"] == 0 and counts["wrong"] == 0 and counts["unknown_name"] == 0 and counts["empty"] == 0
                    and counts["malformed"] == 0 and counts["quoted_problems"] == 0
                    and all(g["verdict"] in ("fresh", "outside the render", "not checked") for g in rep["generated"]))
    return rep


def print_report(rep: dict, words: bool) -> None:
    c = rep["counts"]
    print(f"{rep['numbers']} named numbers; {len(rep['files'])} source files; "
          f"{c['bindings']} bindings: {c['verified']} verified, {c['wrong']} wrong, {c['unknown_name']} unknown name, "
          f"{c['empty']} empty; {c['quoted']} quoted ({c['quoted_problems']} problems); "
          f"{c['unbound']} unbound numerals; {c['malformed']} malformed macros; "
          f"excluded {c['excluded']}; {c['number_words']} number words; "
          f"generated {c['generated']}; {c['named_not_bound']} entries bound nowhere; {c['deltas']} deltas")
    for b in rep["bindings"]:
        if b["verdict"] != "verified":
            print(f"   {b['verdict'].upper():18s} {b['file']}: {b['raw']}  prints {b['prints'][:4]}")
    for q in rep["quoted"]:
        if "problem" in q:
            print(f"   QUOTED PROBLEM      {q['file']}: {q['raw']}  ({q['problem']})")
    for u in rep["unbound"]:
        print(f"   UNBOUND             {u['file']}: {u['number']}  |  {u['sentence'][:150]}")
    for m in rep["malformed"]:
        print(f"   MALFORMED           {m['file']}: {m['at'][:150]}")
    for g in rep["generated"]:
        if g["verdict"] not in ("fresh", "outside the render", "not checked"):
            print(f"   GENERATED {g['verdict'].upper():10s} {g['member']}")
    if rep["deltas"]:
        print("DELTAS (FIND / REPLACE, one per wrong binding):")
        for d in rep["deltas"]:
            print(f"  {d['file']}\n    FIND:    {d['find']}\n    REPLACE: {d.get('replace', 'CASE: ' + d.get('case', ''))}")
    if words:
        for w in rep["number_words"]:
            print(f"   WORD   {w['file']}: {w['word']}  |  {w['sentence'][:150]}")
    print("CLEAN" if rep["clean"] else "NOT CLEAN")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper", required=True, type=Path)
    ap.add_argument("--numbers", required=True, type=Path, help="the figure tool's TN.json")
    ap.add_argument("--render", type=Path, default=None, help="the figure tool's output directory, for the generated members")
    ap.add_argument("--deltas", action="store_true", help="print FIND/REPLACE for every wrong binding")
    ap.add_argument("--words", action="store_true", help="list every number word with its sentence")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    tn = json.loads(args.numbers.read_text())
    rep = check(args.paper, tn["numbers"], args.render)
    if args.out:
        args.out.write_text(json.dumps(rep, indent=1) + "\n")
    if not args.deltas:
        rep = dict(rep, deltas=[])
    print_report(rep, args.words)
    return 1 if args.strict and not rep["clean"] else 0


if __name__ == "__main__":
    sys.exit(main())
