"""The one per-run row every scoring chain writes and every later stage reads (F82, principle 15,
2026-10-11).

A run becomes a row, rows become a fit, a fit becomes a document, documents pool, pools draw: one
pipeline, and this module is the shape between its first two stages. The three chains (the CARLA
threading scorer, the CARLA flow scorer, the Isaac warehouse derivation) differ in how a run is
measured, so each writes the row from its own evidence; from here on nothing branches on the chain.

A row holds, in this order:
  identity   family, model, variant, repetition (an int, or null for the one deterministic reference
             run a family declares; the declaration's reference model only)
  axis       x (what the fit uses), x_metric, x_basis (measured: a per-variant measurement, the
             threading corridor width, the flow headway at the crossing from the gap certificate (F96,
             2026-10-18; until then the flow axis was the declared gap, basis "nominal", now retired),
             the warehouse gap; level: a categorical identity), x_nominal (the declared value the
             variant was generated for, where the chain has one), x_measured
  outcome    the chain's label, named in the chain's outcome vocabulary document
  codings    commitment, clean, passage: true, false or null (not applicable to this run: an
             anomaly, a late commit, an unmeasured go), each either read from the label through the
             vocabulary or measured by the scorer, as the vocabulary declares per coding
  decision   decision_correct (true, false or null) and decision_scope, set by the scorer through the
             one rule below (decision): a clean pass is the right call on a passable variant, no
             attempt at the passage on a blocked one; the decision-accuracy table sums them
  assignment what the family's certificate or manifest says of the variant: passable, blocked,
             conditional, mixed, unmeasured, defect
  annex      everything else the chain measured, chain-owned, read by the chain's own figures and
             summed into the document's points where the vocabulary names a field

The stamp beside the rows (scoring_provenance/v0.2) names the scorer by path and hash, the
vocabulary, every instrument by path and hash, the family manifest, the inputs' digest and the row
count: one shape for the three chains, self-describing, so a reader checks any chain's stamp the
same way (check_stamp). Stdlib only: the scorers run under the backends' own environments.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scl_core.records import portable_path, render, sha256_file

ROWS_KIND = "run_rows/v0.1"
STAMP_KIND = "scoring_provenance/v0.2"
VOCABULARY_KIND = "outcome_vocabulary/v0.1"
DOCUMENT_KIND = "findings_summary/v0.3"

# the member table: where an interpretation holds each stage's output, every chain alike
ROWS_MEMBER = "scoring_out/run_rows.jsonl"
STAMP_MEMBER = "scoring_out/scoring_provenance.json"
STATS_MEMBER = "stats/stats_rows.jsonl"
STATS_STAMP_MEMBER = "stats/stats_provenance.json"
FINDINGS_DIR = "findings"

CORE_FIELDS = ("family", "model", "variant", "repetition", "x", "x_metric", "x_basis", "x_nominal",
               "x_measured", "assignment", "outcome", "commitment", "clean", "passage",
               "decision_correct", "decision_scope", "annex")
X_BASES = ("measured", "level")
ASSIGNMENTS = ("passable", "blocked", "conditional", "mixed", "unmeasured", "defect")
CODINGS = ("commitment", "clean", "passage")
CODING_SOURCES = ("label", "measured")
REDUCES = ("sum", "any", "all", "constant", "median", "max_abs", "range")


def _is_tristate(v) -> bool:
    """True, False or None by identity (1 == True in Python, and a count is not a coding)."""
    return v is True or v is False or v is None
DECISION_SCOPES = ("balanced", "conditional")


def document_name(campaign_id: str, family: str, model: str) -> str:
    """One naming rule for every findings document (C9 on both backends, the kind since F82)."""
    return f"fs_{campaign_id}_{family}_{model}.v0.3.json"


# --- the row -----------------------------------------------------------------

def row(*, family, model, variant, repetition, x, x_metric, x_basis, x_nominal=None, x_measured=None,
        assignment, outcome, commitment, clean, passage, decision_correct=None, decision_scope=None,
        annex=None) -> dict:
    """A row in the contract's key order, validated."""
    r = {"family": family, "model": model, "variant": variant, "repetition": repetition,
         "x": x, "x_metric": x_metric, "x_basis": x_basis, "x_nominal": x_nominal, "x_measured": x_measured,
         "assignment": assignment, "outcome": outcome,
         "commitment": commitment, "clean": clean, "passage": passage,
         "decision_correct": decision_correct, "decision_scope": decision_scope,
         "annex": dict(annex or {})}
    validate_row(r)
    return r


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_row(r: dict, vocabulary: dict | None = None) -> None:
    """Refuses a row outside the contract, naming the field and the vocabulary (principle 9). With a
    vocabulary, every label-coded coding must equal what the vocabulary gives the row's outcome, and
    every annex field a point extra names must be present (null allowed)."""
    if not isinstance(r, dict):
        raise ValueError(f"a run row is an object with the fields {CORE_FIELDS}")
    missing = [k for k in CORE_FIELDS if k not in r]
    extra = [k for k in r if k not in CORE_FIELDS]
    if missing or extra:
        raise ValueError(f"run row fields are exactly {CORE_FIELDS}; missing {missing}, unknown {extra}")
    for k in ("family", "model", "variant", "x_metric", "outcome"):
        if not isinstance(r[k], str) or not r[k]:
            raise ValueError(f"run row {k} must be a non-empty string, got {r[k]!r}")
    if r["repetition"] is not None and (not isinstance(r["repetition"], int) or isinstance(r["repetition"], bool)):
        raise ValueError(f"run row repetition is an int or null (the one deterministic reference run), got {r['repetition']!r}")
    if r["x_basis"] not in X_BASES:
        raise ValueError(f"run row x_basis must be one of {X_BASES}, got {r['x_basis']!r}")
    if r["x_basis"] == "level":
        if not isinstance(r["x"], str):
            raise ValueError(f"run row x is the level's identity (a string) when x_basis is level, got {r['x']!r}")
    elif not _is_number(r["x"]):
        raise ValueError(f"run row x must be a number when x_basis is {r['x_basis']}, got {r['x']!r}")
    for k in ("x_nominal", "x_measured"):
        if r[k] is not None and not _is_number(r[k]) and not isinstance(r[k], str):
            raise ValueError(f"run row {k} is a number, a level or null, got {r[k]!r}")
    if r["assignment"] not in ASSIGNMENTS:
        raise ValueError(f"run row assignment must be one of {ASSIGNMENTS}, got {r['assignment']!r}")
    for k in CODINGS + ("decision_correct",):
        if not _is_tristate(r[k]):
            raise ValueError(f"run row {k} must be true, false or null, got {r[k]!r}")
    if r["decision_scope"] is not None and r["decision_scope"] not in DECISION_SCOPES:
        raise ValueError(f"run row decision_scope must be one of {DECISION_SCOPES} or null, got {r['decision_scope']!r}")
    if not isinstance(r["annex"], dict):
        raise ValueError("run row annex must be an object (the chain's own measured fields)")
    if vocabulary is not None:
        expected = code(vocabulary, r["outcome"])
        for k, v in expected.items():
            if r[k] != v:
                raise ValueError(f"run row {k} is {r[k]!r} but the vocabulary {vocabulary.get('chain')} codes "
                                 f"outcome {r['outcome']!r} as {v!r} (a label coding is the vocabulary's, never the scorer's)")
        for key in ("point_extras", "document_extras"):
            for extra_spec in vocabulary.get(key, []):
                if extra_spec["annex"] not in r["annex"]:
                    raise ValueError(f"run row annex lacks {extra_spec['annex']!r}, which the vocabulary's {key[:-1]} "
                                     f"{extra_spec['name']!r} reduces")


def cell(r: dict) -> tuple:
    """(model, repetition, variant): the declaration's cell of a row."""
    return r["model"], r["repetition"], r["variant"]


def read_rows(path) -> list:
    """The rows of a run_rows.jsonl, validated row by row; blank lines are skipped."""
    rows = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{i}: not JSON ({exc})") from None
        validate_row(r)
        rows.append(r)
    return rows


def write_rows(path, rows, vocabulary: dict | None = None) -> None:
    """Validated rows, one per line, no NaN (a measurement that is not a number is null, named in the
    chain's annex), in the contract's key order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for r in rows:
        validate_row(r, vocabulary)
        out.append(json.dumps({k: r[k] for k in CORE_FIELDS}, allow_nan=False))
    path.write_text("".join(line + "\n" for line in out))


# --- the vocabulary ----------------------------------------------------------

def load_vocabulary(path) -> tuple:
    """(document, sha256) of a chain's outcome vocabulary; refuses another kind or a malformed one."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    validate_vocabulary(doc)
    return doc, hashlib.sha256(raw).hexdigest()


def validate_vocabulary(doc: dict) -> None:
    if doc.get("vocabulary_kind") != VOCABULARY_KIND:
        raise ValueError(f"vocabulary_kind {doc.get('vocabulary_kind')!r}; this reader reads {VOCABULARY_KIND}")
    for k in ("chain", "backend", "codings", "outcomes", "point_extras", "note"):
        if k not in doc:
            raise ValueError(f"an outcome vocabulary names {k}")
    codings = doc["codings"]
    if set(codings) != set(CODINGS) or any(v not in CODING_SOURCES for v in codings.values()):
        raise ValueError(f"codings must map each of {CODINGS} to one of {CODING_SOURCES}, got {codings}")
    label_coded = [c for c in CODINGS if codings[c] == "label"]
    if not isinstance(doc["outcomes"], dict) or not doc["outcomes"]:
        raise ValueError("outcomes must map every label the chain writes to its label codings")
    for label, values in doc["outcomes"].items():
        if set(values) != set(label_coded):
            raise ValueError(f"outcome {label!r} must give exactly the label-coded codings {label_coded}, got {sorted(values)}")
        if any(not _is_tristate(v) for v in values.values()):
            raise ValueError(f"outcome {label!r}: a coding is true, false or null")
    for key in ("point_extras", "document_extras"):
        for e in doc.get(key, []):
            if set(e) != {"name", "annex", "reduce"} or e["reduce"] not in REDUCES:
                raise ValueError(f"a {key[:-1].replace('_', ' ')} is {{name, annex, reduce in {REDUCES}}}, got {e}")
    meanings = doc.get("meanings", {})
    if set(meanings) - set(CODINGS) or any(not isinstance(v, str) for v in meanings.values()):
        raise ValueError(f"meanings name the codings {CODINGS} with a sentence each, got {sorted(meanings)}")


def code(vocabulary: dict, outcome: str) -> dict:
    """The label-coded codings of an outcome, from the vocabulary; an outcome the vocabulary does not
    name refuses with the vocabulary stated (never a silent null)."""
    try:
        return dict(vocabulary["outcomes"][outcome])
    except KeyError:
        raise ValueError(f"outcome {outcome!r} is not in the {vocabulary.get('chain')} vocabulary "
                         f"{sorted(vocabulary['outcomes'])}") from None


def codings(vocabulary: dict, outcome: str, measured: dict | None = None) -> dict:
    """{commitment, clean, passage} for a run: the label-coded ones from the vocabulary, the measured
    ones from `measured` (every measured coding must be given, true, false or null)."""
    out = code(vocabulary, outcome)
    measured = measured or {}
    for c, source in vocabulary["codings"].items():
        if source == "measured":
            if c not in measured or not _is_tristate(measured[c]):
                raise ValueError(f"the {vocabulary['chain']} vocabulary measures {c}; the scorer must give it as true, false or null")
            out[c] = measured[c]
    return out


# --- the decision --------------------------------------------------------------

def decision(assignment: str, commitment, clean, passage_attempt=None) -> tuple:
    """(decision_correct, decision_scope) of one run under the one rule every chain applies (the paper's
    definition, adopted 2026-10-12; until then each scorer credited the go on the passable side):
      passable  the right call is a CLEAN PASS, committed and clean; a commitment that ends in contact,
                times out or comes late (clean null) is a miss, and so is a refusal;
      blocked   the right call is NO ATTEMPT at the designated passage: `passage_attempt` is the chain's
                bit for crossing the gap itself (the warehouse gate line, so the detour stays the right
                call there, Milan 2026-10-03) and defaults to the commitment where the chain has one route.
    Both sides are the balanced scope. A run without a measured commitment (an anomaly, an unreadable
    go) carries no decision, and so does every other assignment: the flow certificate's conditional
    band is its own scope (flow_gap_certificate.decision_for); mixed, unmeasured and defect count in none."""
    if commitment is None or assignment not in ("passable", "blocked"):
        return None, None
    if passage_attempt is None:
        passage_attempt = commitment
    if assignment == "passable":
        return (commitment is True and clean is True), "balanced"
    return (not passage_attempt), "balanced"


def reduce_values(values, how: str):
    """One reduction of a point extra over a variant's rows; nulls are left out of sums and
    medians, and a `constant` that differs across the rows reads "mixed" (the flow certificate's own
    word for seeds that disagree)."""
    present = [v for v in values if v is not None]
    if how == "sum":
        return int(sum(1 for v in present if v is True)) if all(isinstance(v, bool) for v in present) else sum(present)
    if how == "any":
        return any(bool(v) for v in present) if present else None
    if how == "all":
        return all(bool(v) for v in present) if present else None
    if how == "constant":
        distinct = sorted({json.dumps(v, sort_keys=True) for v in present})
        return json.loads(distinct[0]) if len(distinct) == 1 else ("mixed" if distinct else None)
    if how == "median":
        if not present:
            return None
        s = sorted(present)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2
    if how == "max_abs":
        return max((abs(v) for v in present), default=None)
    if how == "range":
        return [min(present), max(present)] if present else None
    raise ValueError(f"reduce must be one of {REDUCES}, got {how!r}")


def document_extras(vocabulary: dict, rows: list) -> dict:
    """The vocabulary's per-document sums over every row of one document (the decomposition carries
    them: the warehouse timeouts by kind, the flow stalls by their place against the go)."""
    return {e["name"]: reduce_values([r["annex"].get(e["annex"]) for r in rows], e["reduce"])
            for e in vocabulary.get("document_extras", [])}


def point_extras(vocabulary: dict, rows: list) -> dict:
    """The vocabulary's per-variant extras over the rows of one variant (the emitter's points carry
    them beside n and k: the warehouse gate and detour split, the flow band and its measured gap)."""
    out = {}
    for e in vocabulary.get("point_extras", []):
        out[e["name"]] = reduce_values([r["annex"].get(e["annex"]) for r in rows], e["reduce"])
    return out


# --- coverage ----------------------------------------------------------------

def reference_runs_once(declaration) -> bool:
    """Whether the declaration's reference model runs once per variant: on a policy-sample axis
    (policy_reps) the repetitions sample a policy's stochasticity, and a deterministic reference
    policy has one sample (the warehouse expert, one ledger per variant); on a traffic-seed axis
    every model, the reference included, runs once per seed. A forced divergence between the
    backends, recorded in adapters/PARITY.md."""
    return bool(declaration.expert) and declaration.repetition_axis["kind"] == "policy_reps"


def expected_cells(declaration, reference_single=None, repetitions=None) -> set:
    """The declaration's (model, repetition, variant) cells; with `reference_single` (default: what the
    declaration's axis says, reference_runs_once) the reference model runs once per variant as the
    cell (reference_model, None, variant). `repetitions`: the repetitions a cohort takes from this
    campaign (a pool's declared list, F88 2026-10-15); None means every declared repetition; an id the
    declaration does not hold refuses by name."""
    if reference_single is None:
        reference_single = reference_runs_once(declaration)
    if repetitions is None:
        reps = list(declaration.repetitions)
    else:
        extra = sorted(set(repetitions) - set(declaration.repetitions))
        if extra:
            raise ValueError(f"{declaration.campaign_id}/{declaration.family_id}: repetitions {extra} are not declared "
                             f"(the declaration holds {list(declaration.repetitions)})")
        reps = list(repetitions)
    cells = set()
    for m in declaration.models:
        if reference_single and m == declaration.reference_model:
            cells.update((m, None, v) for v in declaration.variants)
        else:
            cells.update((m, r, v) for r in reps for v in declaration.variants)
    return cells


def coverage(declaration, rows, reference_single=None, repetitions=None) -> None:
    """The coverage gate over the one shape: the rows of a (campaign, family) are exactly the declared
    cells, once each; missing, undeclared and duplicate cells refuse by name. `repetitions`: the cohort's
    repetitions of this campaign (expected_cells), None for the whole declared grid."""
    if reference_single is None:
        reference_single = reference_runs_once(declaration)
    expected = expected_cells(declaration, reference_single, repetitions)
    seen, duplicates = set(), set()
    for r in rows:
        c = cell(r)
        if c in seen:
            duplicates.add(c)
        seen.add(c)
    missing, undeclared = expected - seen, seen - expected
    if missing or undeclared or duplicates:
        def named(cells):
            cells = sorted(cells, key=lambda c: (c[0], -1 if c[1] is None else c[1], c[2]))
            shown = ", ".join(f"{m}/{'reference' if r is None else r}/{v}" for m, r, v in cells[:8])
            return shown + (f" (+{len(cells) - 8} more)" if len(cells) > 8 else "")
        parts = []
        if missing:
            parts.append(f"missing cells: {named(missing)}")
        if undeclared:
            parts.append(f"undeclared cells: {named(undeclared)}")
        if duplicates:
            parts.append(f"duplicate cells: {named(duplicates)}")
        raise ValueError(f"{declaration.campaign_id}/{declaration.family_id}: coverage against the declared grid "
                         f"({len(declaration.models)} models x {len(declaration.repetitions)} repetitions x "
                         f"{len(declaration.variants)} variants"
                         f"{', the reference model once per variant' if reference_single else ''} = "
                         f"{len(expected)} cells) - " + "; ".join(parts))


# --- the stamp ---------------------------------------------------------------

def inputs_digest(lines) -> str:
    """The digest of a sorted set of `<input> <sha256>` lines: one identity for a scored cohort (K12)."""
    return hashlib.sha256("\n".join(sorted(set(lines))).encode()).hexdigest()


def pin(path, root, role=None) -> dict:
    """{role?, path, sha256} of an in-repository file (principle 4: portable, pinned)."""
    out = {"path": portable_path(Path(path), root), "sha256": sha256_file(path)}
    return dict(role=role, **out) if role is not None else out


def write_stamp(out_dir, *, scorer, root, vocabulary, instruments, family_manifest_sha256,
                input_files_sha256, n_rows, canon_inputs=None, note="", extra=None) -> dict:
    """The one scoring stamp: the scorer by path and hash (self-describing, so check_stamp needs no
    caller-side name), the vocabulary pinned, every instrument pinned by role, the family manifest
    the rows were scored against, the inputs' digest, the row count, the canon inputs the stale verb
    checks, and the chain's extra facts under `extra` (never a timestamp)."""
    doc = {"provenance_kind": STAMP_KIND,
           "scorer": pin(scorer, root),
           "vocabulary": pin(vocabulary, root),
           "instruments": [pin(p, root, role) for role, p in instruments],
           "family_manifest_sha256": family_manifest_sha256,
           "input_files_sha256": input_files_sha256,
           "n_rows": n_rows,
           "canon_inputs_sha256": dict(sorted((canon_inputs or {}).items())),
           "extra": dict(extra or {}),
           "note": note or ("content identity only, no timestamps; every reader refuses rows whose scorer "
                            "or vocabulary moved (scl_core.run_rows.check_stamp)")}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / Path(STAMP_MEMBER).name).write_text(render(doc))
    return doc


def read_stamp(scoring_dir) -> dict:
    path = Path(scoring_dir) / Path(STAMP_MEMBER).name
    if not path.is_file():
        raise ValueError(f"{scoring_dir}: no {path.name} (unstamped rows)")
    doc = json.loads(path.read_text())
    if doc.get("provenance_kind") != STAMP_KIND:
        raise ValueError(f"{path}: provenance_kind {doc.get('provenance_kind')!r}; this reader reads {STAMP_KIND}")
    return doc


def check_stamp(scoring_dir, root) -> dict:
    """The one admission check for rows of any chain (K9): the stamp's scorer and vocabulary must hash
    to the files at their recorded paths under `root` now; a moved one refuses with both hashes (a
    stale table never silently carries a retired instrument's outcomes). Returns the stamp."""
    return check_stamp_document(read_stamp(scoring_dir), root, scoring_dir)


def check_stamp_document(doc, root, where="the stamp") -> dict:
    """check_stamp over a stamp already read (a certifier reads the member out of the derived zip)."""
    if doc.get("provenance_kind") != STAMP_KIND:
        raise ValueError(f"{where}: provenance_kind {doc.get('provenance_kind')!r}; this reader reads {STAMP_KIND}")
    scoring_dir = where
    for role in ("scorer", "vocabulary"):
        p = doc[role]
        target = Path(root) / p["path"]
        now = sha256_file(target) if target.is_file() else "absent"
        if now != p["sha256"]:
            raise ValueError(f"{scoring_dir}: {role} {p['path']} hashes {now[:12]} in this tree, the rows were "
                             f"produced under {p['sha256'][:12]}; re-score the raw runs under the current chain")
    return doc
