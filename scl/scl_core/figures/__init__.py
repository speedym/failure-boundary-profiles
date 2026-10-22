"""The figure tools' one data layer (F82, principle 15, 2026-10-11): how a document's curve becomes
what a figure may draw, and how a figure tool reads the pool tool's output.

Until 2026-10-11 the two paper-figure tools (adapters/fail2drive/.../paper_figures.py and
adapters/isaac/.../paper_figures.py) each held a curve renderer over its own input shape (stats rows
on CARLA, v0.2 documents on Isaac, the store's documents for the flow and warehouse rows of the CARLA
atlas), re-derived the codings from outcome labels, and found documents by scanning the tree. Now
every chain writes one row shape, one fit stage and one document kind (scl_core.run_rows,
scl_core.stats.fit_rows, scl_core.stats.emit_findings), and the pool tool lays them out one way
(adapters/fail2drive/scripts/multi_model_eval/pool_campaigns.py). This package is what both figure
tools import to read that layout and to turn a curve into a render spec:

  curves.py     the ONE curve renderer over the document's curve shape ({points, fit, bootstrap}), the
                adapter from a stats row to that shape (the single-cohort form), the boundary quoting
                rules the tables print
  documents.py  the ONE reader of a pool folder (its record, its documents, its rows and stats when
                present), the admission checks (the document kind, the emitter/fit/estimator pins and
                the embedded scoring stamp against this tree), the lineage block every figure_data
                JSON embeds, the single-cohort form built through the one emitter

Stdlib plus numpy only (through scl_core.stats): no matplotlib at import. Presentation (matplotlib)
stays in each tool; what is drawn is decided here.
"""
