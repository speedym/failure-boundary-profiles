#!/usr/bin/env python3
"""Propose and write the bindings of a paper's numbers (F97, 2026-10-19): the migration tool for the one-time pass over
the paper and the tool for every new sentence after it.

    conda run -n scl_prototype python scripts/bind_paper_numbers.py --paper DIR --numbers TN.json \\
        --scopes artifacts/examples/figure_specs/paper_scopes.json --out OUTDIR [--decisions FILE] [--report FILE]

For every numeral the checker would refuse (scripts/check_paper_numbers.py: its scan, its exclusions, applied here to
the raw source text so the macro is written at the numeral's own place), in every source file:
  phrase    the numeral is read first as part of a phrase the sentence prints around it ("13 of its 29", "4.50 to
            5.12", "[0.67, 0.81]", "220 hours", "1 cm") and the phrase is matched against the entries' prints; then
            as the bare number; a candidate is an entry one of whose prints equals the form (the checker's rule);
  scope     one covering entry: bound ("unique"). Several: an entry's family and agent words (the scopes file) must
            appear in the context; the entries whose every part appears are kept; one kept: bound ("scoped");
            several kept and the quantity words single one out: bound ("quantity"); otherwise a HAND CASE, listed
            with its candidates (tried and dropped 2026-10-19: the agent named nearest before the number; it bound
            "the expert ... contacts the machine in all 36 of its runs" to BridgeDrive, named in the subordinate clause). The context widens in three steps until one step decides: the sentence; the sentence
            with the one before it (the agent or the family named a sentence earlier); the paragraph. A caption file
            adds the whole caption at every step. The report names the step ("scoped@sentence", "quantity@paragraph").
            No covering entry: listed as NO ENTRY (the numbers document must gain it or the prose must drop it);
  decision  a decisions file settles the hand cases and the no-entry cases: a list of {"file", "number",
            "sentence_prefix" (the first 50 characters of the sentence as the report prints it), "ordinal" (which
            occurrence of the number in that sentence, from 1)} with one of "bind": <entry name, or several names
            comma-separated when the sentence claims the number for several agents at once>, "quote": <origin>
            (a \\pq, a number from outside the pipeline) or "skip": <reason> (left unbound, on purpose, for now);
            a decision wins over the scope rules;
  write     the annotated copy of the whole paper tree goes under --out (files without a change copied as they are);
            "10{,}000" is written as "10,000" (one numeral, the same in print); a printed form that the source broke
            across a line ("20 of\\n36") is written on one line; nothing else but the macros changes;
  wrap      a macro makes a line longer than the number it replaced; from every changed line that runs past
            --width columns (default 78, the width the paper's hand-wrapped prose keeps; 0 leaves the lines alone)
            to the end of its run of running text, the lines are joined and filled again: broken at a space, never
            inside a macro, except that a macro longer than a line may break in the argument TeX discards (after a
            comma of a \\pn's name list, at a space of a \\pq's origin), the printed form staying whole; every
            piece keeps the first line's indentation. The run ends at a line that is not running text (blank, a
            label, an item, an environment, a heading, a rule, a comment, a line ending in \\\\); a changed line
            that carries a comment is re-broken alone, the comment at the end of its last piece. Lines before the
            change, changed lines that still fit, and paragraphs without a change keep their bytes, so the diff
            against the source shows each changed paragraph from its first change to its end.
The report (--report, JSON) lists every binding written with how it was decided, every hand case with its candidates,
every no-entry case, and the decisions applied or unmatched. A unique binding is still a proposal: the report lists
each with its sentence for the reading that follows.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_paper_numbers as cpn  # noqa: E402

PHRASES = [  # anchored at the numeral's start (or one character before it for the interval); the raw source may
             # break a line inside a phrase, so any whitespace run is one space
    ("k_of_n", re.compile(r"(\d+)\s+of\s+(?:its\s+|the\s+|their\s+)?(\d+)(?!\w)(?!\.\d)")),
    ("unit", re.compile(r"(\d+(?:\.\d+)?)(?:\\,|~|\s)+(?:cm|mm|hours|m|s|km/h|m/s)\b")),   # "1\\,cm": an entry may print the unit
    ("hours", re.compile(r"(\d+(?:\.\d+)?)\s+hours\b")),
    ("a_to_b", re.compile(r"(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)(?!\w)(?!\.\d)")),
    ("a_and_b", re.compile(r"(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)(?!\w)(?!\.\d)")),
]
INTERVAL = re.compile(r"\[(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)\]")
WIDTH = 78          # the paper's hand-wrapped prose runs to 75 columns; a changed line is re-broken at this width
MACRO_RE = re.compile(r"\\(" + cpn.PN + "|" + cpn.PQ + r")\{([^{}]*)\}\{([^{}]*)\}")   # name, first and second argument


def mask_comments(text: str) -> str:
    return cpn.COMMENT_RE.sub(lambda m: " " * len(m.group(0)), text)


def _words_in(words, context: str) -> bool:
    c = context.lower()
    return any(w.lower() in c for w in words)


def name_parts(name: str, scopes: dict):
    parts = name.split(".")
    fam = next((p for p in parts if p in scopes["families"]), None)
    agent = next((p for p in parts if p in scopes["agents"]), None)
    rest = ".".join(p for p in parts if p not in (fam, agent))
    return fam, agent, rest


def candidates_for(printed: str, numbers: dict, whole: bool) -> list:
    plain = re.sub(r"\s+", " ", cpn._plain(printed)).strip()
    return [name for name, e in numbers.items() if cpn.is_covered(plain, e.get("prints", []))]


def choose(cands: list, context: str, scopes: dict):
    """(name, how) or (None, kept-or-all candidates)."""
    if len(cands) == 1:
        return cands[0], "unique"
    kept = []
    for n in cands:
        fam, agent, rest = name_parts(n, scopes)
        ok = (fam is None or _words_in(scopes["families"][fam], context)) and \
             (agent is None or _words_in(scopes["agents"][agent], context))
        if ok:
            kept.append(n)
    if len(kept) == 1:
        return kept[0], "scoped"
    if len(kept) > 1:
        q = [n for n in kept if name_parts(n, scopes)[2] in scopes["quantities"]
             and _words_in(scopes["quantities"][name_parts(n, scopes)[2]], context)]
        if len(q) == 1:
            return q[0], "quantity"
        return None, q or kept
    return None, cands


def bind_file(rel: str, raw: str, numbers: dict, scopes: dict, decisions: dict, is_caption: bool,
              width: int = WIDTH) -> tuple:
    raw = raw.replace("{,}", ",")
    masked = mask_comments(raw)
    bound = [(m.start(), m.end()) for m in cpn.PN_RE.finditer(masked)] + [(m.start(), m.end()) for m in cpn.PQ_RE.finditer(masked)]
    rows = [r for r in cpn.classify(masked, bound) if r["kind"] is None]
    sents = cpn.sentence_spans(masked)
    paras = paragraph_spans(raw)          # on the raw text: a comment-only line is not a blank line (same offsets)
    whole_caption = cpn._clean_sentence(masked) if is_caption else ""
    edits, report, taken = [], [], set()
    seen_in_sentence = {}
    for r in rows:
        s, e = r["start"], r["end"]
        if s in taken:
            continue
        sent = cpn._sentence_of(masked, sents, s)
        contexts = [("sentence", sent + " " + whole_caption),
                    ("previous", _previous_sentence(masked, sents, s) + " " + sent + " " + whole_caption),
                    ("paragraph", _paragraph_of(masked, paras, s) + " " + whole_caption)]
        key_prefix = sent[:50]
        ordinal = seen_in_sentence.get((key_prefix, r["number"]), 0) + 1
        seen_in_sentence[(key_prefix, r["number"])] = ordinal
        # the phrase around the numeral, matched whole against the prints
        ps, pe, cands, kind = s, e, [], "number"
        for pname, pat in PHRASES:
            m = pat.match(masked, s)
            if m:
                c = candidates_for(masked[m.start():m.end()], numbers, whole=True)
                if c:
                    ps, pe, cands, kind = m.start(), m.end(), c, pname; break
        if not cands and s > 0 and masked[s - 1] == "[":
            m = INTERVAL.match(masked, s - 1)
            if m:
                c = candidates_for(masked[m.start():m.end()], numbers, whole=True)
                if c:
                    ps, pe, cands, kind = m.start(), m.end(), c, "interval"
        if not cands:
            cands = candidates_for(r["number"], numbers, whole=False)
        row = {"file": rel, "number": r["number"], "phrase": masked[ps:pe], "sentence": sent, "ordinal": ordinal}
        form = re.sub(r"\s+", " ", raw[ps:pe])     # the printed form on one line, whatever the source's line breaks
        dec = decisions.get((rel, r["number"], key_prefix, ordinal))
        if dec is not None:
            dec["_used"] = True
            if "bind" in dec:
                row.update(decided="decision", name=dec["bind"]); edits.append((ps, pe, "\\%s{%s}{%s}" % (cpn.PN, dec["bind"], form)))
            elif "quote" in dec:
                row.update(decided="quote", origin=dec["quote"]); edits.append((ps, pe, "\\%s{%s}{%s}" % (cpn.PQ, form, dec["quote"])))
            else:
                row.update(decided="skip", reason=dec.get("skip", ""))
        elif not cands:
            row.update(decided="no entry")
        else:
            name, how, left, levels = None, None, cands, {}
            for level, context in contexts:
                name, how = choose(cands, context, scopes)
                levels[level] = [name] if name else list(how)
                if name:
                    how = how if how == "unique" else f"{how}@{level}"; break
                left = how
            if name:
                row.update(decided=how, name=name, candidates=cands if how != "unique" else None)
                edits.append((ps, pe, "\\%s{%s}{%s}" % (cpn.PN, name, form)))
            else:
                row.update(decided="hand case", candidates=left, levels=levels)   # what each context left standing
        if "name" in row or row.get("decided") == "quote":
            for n in cpn.NUMERAL_RE.finditer(masked, ps, pe):
                taken.add(n.start())
        report.append(row)
    out, changed, shift = raw, [], 0
    for ps, pe, rep in sorted(edits):
        out = out[:ps + shift] + rep + out[pe + shift:]
        changed.append((ps + shift, ps + shift + len(rep)))
        shift += len(rep) - (pe - ps)
    return wrap_changed_lines(out, changed, width), report


def wrap_changed_lines(text: str, changed: list, width: int) -> str:
    """Re-fill the text from every changed line that runs past the width to the end of its run of running text;
    every other line keeps its bytes. A changed line with a comment is re-broken alone."""
    if not width:
        return text
    lines, pos, flags = text.split("\n"), 0, []
    for line in lines:
        start, end = pos, pos + len(line)
        pos = end + 1
        flags.append(any(s < end and e > start for s, e in changed))
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not (flags[i] and len(line.rstrip()) > width):
            out.append(line); i += 1
        elif cpn.COMMENT_RE.search(line):
            out += wrap_line(line, width); i += 1
        else:
            j = i + 1
            while j < len(lines) and _running(lines[j]):
                j += 1
            out += fill(lines[i:j], width); i = j
    return "\n".join(out)


RUNNING_RE = re.compile(r"\s*(?:[A-Za-z0-9(\[$`'\"-]|\\(?:" + cpn.PN + "|" + cpn.PQ + r"|autoref|cite|emph)\{)")


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def _running(line: str) -> bool:
    """A line of running text, one a re-fill takes into the line before it: it starts with a word or an inline macro,
    holds no comment and does not end a row or a line by hand (\\\\)."""
    s = line.rstrip()
    return bool(RUNNING_RE.match(s)) and not cpn.COMMENT_RE.search(s) and not s.endswith("\\\\")


def fill(unit: list, width: int) -> list:
    """The lines joined and broken again at the width, every piece with the first line's indentation."""
    indent = _indent(unit[0])
    return _break(indent + " ".join(l.strip() for l in unit), indent, width)


def wrap_line(line: str, width: int) -> list:
    """One line broken at the width, its comment at the end of the last piece; the code keeps its trailing spaces,
    which TeX reads before a comment."""
    indent = _indent(line)
    m = cpn.COMMENT_RE.search(line)
    code, comment = (line[:m.start()], line[m.start():]) if m else (line, "")
    pieces = _break(code, indent, width)
    pieces[-1] += comment
    return pieces


def _break(code: str, indent: str, width: int) -> list:
    """The code into pieces of at most the width where a cut allows it: the cut falls at the last allowed position
    whose piece fits, or at the first one after it when none fits; a continuation keeps the indentation."""
    cuts = _cuts(code, indent, width)
    out, start = [], 0
    while len(_piece(code, start, len(code), indent).rstrip()) > width:
        room = [c for c in cuts if c > start and len(_piece(code, start, c, indent).rstrip()) <= width]
        later = [c for c in cuts if c > start]
        cut = room[-1] if room else (later[0] if later else None)
        if cut is None:
            break
        out.append(_piece(code, start, cut, indent).rstrip())
        start = cut
        while start < len(code) and code[start] == " ":
            start += 1
    out.append(_piece(code, start, len(code), indent))
    return out


def _piece(code: str, start: int, end: int, indent: str) -> str:
    return code[start:end] if start == 0 else indent + code[start:end]


def _cuts(code: str, indent: str, width: int) -> list:
    """Where a line may break: at a space outside every macro; inside a macro longer than a line, in the argument
    TeX discards: after a comma of a \\pn's name list, at a space of a \\pq's origin. The printed form never
    breaks."""
    macros = list(MACRO_RE.finditer(code))
    cuts = [i for i in range(len(indent) + 1, len(code)) if code[i] == " " and code[i - 1] != " "
            and not any(mm.start() < i < mm.end() for mm in macros)]
    for mm in macros:
        if mm.end() - mm.start() > width - len(indent):
            if mm.group(1) == cpn.PN:
                cuts += [i for i in range(mm.start(2) + 1, mm.end(2)) if code[i - 1] == ","]
            else:
                cuts += [i for i in range(mm.start(3) + 1, mm.end(3)) if code[i] == " " and code[i - 1] != " "]
    return sorted(cuts)


def paragraph_spans(text: str) -> list:
    spans, start = [], 0
    for m in re.finditer(r"\n\s*\n|\\item\b|\\(?:begin|end)\{(?:itemize|enumerate|description)\}"
                         r"|\\(?:paragraph|section|subsection|subsubsection)\*?\{", text):
        # a blank line, a list item, a list's beginning or end, or a heading starts a paragraph
        spans.append((start, m.start())); start = m.end()
    spans.append((start, len(text)))
    return spans


def _paragraph_of(text: str, paras: list, pos: int) -> str:
    for s, e in paras:
        if s <= pos < e:
            return cpn._clean_sentence(text[s:e])[:2000]
    return ""


def _previous_sentence(text: str, sents: list, pos: int) -> str:
    prev = ""
    for s, e in sents:
        if s <= pos < e:
            return prev
        prev = cpn._clean_sentence(text[s:e])
    return ""


def load_decisions(path) -> dict:
    if path is None:
        return {}
    rows = json.loads(Path(path).read_text())
    return {(d["file"], d["number"], d["sentence_prefix"], d.get("ordinal", 1)): d for d in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper", required=True, type=Path)
    ap.add_argument("--numbers", required=True, type=Path)
    ap.add_argument("--scopes", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="the annotated copy of the paper tree (created or replaced)")
    ap.add_argument("--decisions", type=Path, default=None)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--width", type=int, default=WIDTH,
                    help=f"re-break every changed line at this many columns (default {WIDTH}; 0 leaves lines as they are)")
    args = ap.parse_args()
    numbers = json.loads(args.numbers.read_text())["numbers"]
    scopes = json.loads(args.scopes.read_text())
    decisions = load_decisions(args.decisions)
    if args.out.exists():
        shutil.rmtree(args.out)
    shutil.copytree(args.paper, args.out)
    sources = cpn.load_sources(args.paper)
    report, counts = [], {}
    for rel, raw in sources.items():
        if rel.startswith(cpn.GENERATED_BODIES):
            continue
        text, rows = bind_file(rel, raw, numbers, scopes, decisions, is_caption=rel.startswith("fig/"), width=args.width)
        if text != raw:
            (args.out / rel).write_text(text, encoding="utf-8")
        report += rows
    for r in report:
        counts[r["decided"]] = counts.get(r["decided"], 0) + 1
    unmatched = [{k: v for k, v in d.items() if k != "_used"} for d in decisions.values() if not d.get("_used")]
    print(f"{len(report)} numerals: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
          + f"; decisions unmatched {len(unmatched)}")
    for r in report:
        if r["decided"] in ("hand case", "no entry"):
            print(f"   {r['decided'].upper():9s} {r['file']}: {r['phrase']!r} ordinal {r['ordinal']} | {r['sentence'][:110]}")
            for c in (r.get("candidates") or [])[:8]:
                print(f"              {c}")
    for d in unmatched:
        print(f"   UNMATCHED DECISION {d}")
    if args.report:
        args.report.write_text(json.dumps({"report_kind": "paper_numbers_bind/v0.1", "paper": str(args.paper),
                                           "out": str(args.out), "width": args.width, "counts": counts, "rows": report,
                                           "unmatched_decisions": unmatched}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
