# Context review record v1

This is a human-authored review trail gate. A successful receipt means required
review declarations and references are present and fresh. It does not mean the
selected categories, alternatives, or formulas are semantically right.

## Unit review rows

`unit_reviews` has exactly one row for every analysis unit. Each row contains:

- `unit_id`, `reading: "read_with_context"`, and `context_unit_ids` containing
the unit plus at least one adjacent unit. Self-reference alone does not count.
- `reading_rationale`, `review_decision` (`retain`, `change`, `exclude`, or
  `uncertain`), `unit_text_sha256`, and `phrase_runs_sha256` for the current
  source text and full current run array.
- A cross-section reference needs `cross_section_reason`. When the entire
  analysis contains only one unit, use `single_unit_context_reason` to explain
  why no adjacent unit exists.
- An `observation_note` additionally needs
  `observation_note_exclusion: {"excluded_from_source": true, "reason": "..."}`.

The validator rejects a short finite set of generic rationales such as `checked`,
`pass`, `已检查`, `已审核`, `同上`, `无误`, `保留`, and `保持不变`. This only
rejects an insufficient record; it does not judge whether a more detailed
rationale is sound.

## Decisions and formula rows

`decisions` may be empty when no specific disputed or explicit no-change item
needs a separate record; every unit and formula still needs its own
`review_decision` and hashes. A `dispute` binds `unit_id`, whole-unit SHA-256,
exact source offsets and phrase, phrase SHA-256, selected family, a distinct
alternative family, rationale, and context references. The selected family
must match the cited current run span. A `nochange` binds the unit text and the
canonical `phrase_runs_sha256` of the entire run list, plus rationale and
context. This supports units with multiple run families without collapsing the
annotations into one category.

Every `source_formula` step, `reusable_formula` step, and section-level
`formula_runs` formula has one `formula_reviews` row. Each row repeats its
`group`, `index` (the section ID for `section_formula`), `section_id`, and exact
`source_unit_ids`. It also requires `reading_rationale`, `review_decision`,
`label_sha256` for the exact formula label, and `phrase_runs_sha256` for the
exact current formula runs. If a formula introduces a family absent from its
source units' annotation families, it also needs `context_rationale`.

`uncertain` is allowed as a review decision. The returned `unresolved_count`
counts unit and formula rows marked uncertain. A publishing wrapper must block
publication when that count is greater than zero. The count is not a semantic
verdict.

## Analysis binding and use

Pass the SHA-256 of the exact `analysis.json` bytes, not a reserialized object.
The outer file hash binds the receipt to the analysis artifact; per-unit and
per-formula hashes also prevent an updated outer hash from masking stale review
rows.

```python
import hashlib
import json
from context_review import validate_context_review

with open('analysis.json', 'rb') as f:
    raw = f.read()
analysis = json.loads(raw)
with open('context-review.json', encoding='utf-8') as f:
    review = json.load(f)
receipt = validate_context_review(review, analysis, hashlib.sha256(raw).hexdigest())
```

The returned `result: context_record_validated` always carries
`semantic_truth: not_assessed`. A supervisor still has to read each unit and
judge whether its contextual interpretation is sound.
