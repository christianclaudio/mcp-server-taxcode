# Contributing Guidelines

Thank you for your interest in contributing!

1. Fork or branch from `main`: `git checkout -b feat/your-feature`.
2. Ensure strict static typing: `mypy --strict src/`.
3. Ensure the test suite passes: `pytest` (coverage threshold configured in `pyproject.toml`).
4. Validate tool contracts: `python scripts/check_tool_contract.py`.
5. Open a Pull Request. Merges are performed via **Squash Merge** with Conventional Commit titles (`feat:`, `fix:`, `docs:`, `chore:`).
6. Changing statute or publication rows? Follow "Adding or changing a row" below and run `uv run python scripts/build_catalog.py --check`.
7. Do not edit version numbers or `CHANGELOG.md`: the git tag is the version, and GitHub Releases are the changelog. The squash commit message is the PR body, so a breaking (`feat!:` / `fix!:`) PR ends its body with one `BREAKING CHANGE:` final paragraph that lists the breaking items, with no migration steps.

## Adding or changing a row

Statute and publication rows live only in the data files under `src/taxcode_mcp/data/`. The server seeds its database from them, and `scripts/ingest_statutes.py` and the docs read them too. Never add rows as Python literals.

| File | Holds |
| --- | --- |
| `jurisdictions.json` | Jurisdiction codes in catalog order. Rows load in this order, then in file order. |
| `statutes/<code>.json` | One jurisdiction's statute rows (`us.json`, `pr.json`, `ga.json`, ...). `text_en` and `text_es` are lists of lines, joined with a newline when loaded. |
| `publications/<code>.json` | Publications whose `id` starts with `<code>-`. |
| `manifest/<code>.json` | One source record per statute and publication row of that jurisdiction. |
| `rates/<code>.json` | The rate table the compliance tools compute a state's tax from. See "Changing a rate table" below. |

Every manifest entry has every field below. A value we don't have is the explicit string `"not_recorded"`, never a missing key and never a guess.

| Field | Meaning |
| --- | --- |
| `kind`, `id` | `statute` or `publication`, and the row's `id`. |
| `url` | Must equal the row's `source_url`. |
| `edition` | Which text the source at `url` is: `as_enacted` (the law as passed), `amended_compilation` (a compilation with later amendments folded in, such as an OGP "según enmendada" text), or `"not_recorded"`. |
| `official` | `true` when the URL is on the issuing government body's own site, `false` for a third-party site, or `"not_recorded"`. It says nothing about whether the stored text is official. |
| `licence` | Reuse terms of the source, or `"not_recorded"`. |
| `retrieved` | Date the source was fetched (`YYYY-MM-DD`), or `"not_recorded"`. |
| `content_sha256` | SHA-256 of the fetched source artifact, or `"not_recorded"`. |
| `enacting_law` | Law that enacted or last amended the text, or `"not_recorded"`. |
| `effective_date` | Date the text took effect (`YYYY-MM-DD`), or `"not_recorded"`. |
| `verification` | `verified_verbatim` (checked word for word against the fetched source; needs `retrieved` and `content_sha256`), `summary` (the stored text is a summary, not a copy), or `"not_recorded"`. |

To add or change a row:

1. Edit the row in `statutes/<code>.json` or `publications/<code>.json`. A new prior-year version is a new row with its own `id` and `effective_year`; set `superseded_year` on the version it replaces. Never copy current text into an earlier year.
2. Add or update its entry in `manifest/<code>.json`.
3. Run `uv run python scripts/build_catalog.py`. It rewrites the data files in canonical form and regenerates `docs/<code>/*.md`, `docs/<code>/llms-full.txt` (that jurisdiction's full text), `docs/llms.txt` and `docs/llms-full.txt` (an index of the per-jurisdiction files), so a content PR only changes the full-text file of the jurisdictions it touches.
4. Run `uv run python scripts/build_catalog.py --check` and the test suite. CI runs the same check and fails if a data file, a manifest entry or a generated docs file is out of step.

A new jurisdiction also needs its code in `jurisdictions.json` and, for its name to resolve, in `JURISDICTION_NAMES` (`src/taxcode_mcp/db.py`). Content-only PRs touch just the data files, the manifest and the generated docs; keep code changes in their own PR.

## Changing a rate table

Every deduction, exemption, bracket, rate and per-year value the compliance tools use lives in `src/taxcode_mcp/data/rates/<code>.json`, one file per state that has one (`RATE_TABLE_MODELS` in `src/taxcode_mcp/catalog.py`). FL and TX have none. Never add a rate as a Python literal.

- `model` names the shape of `params` and which formula reads it. It must match `RATE_TABLE_MODELS`.
- Amounts and rates are floats (`1000.0`, not `1000`), because tool output prints them as stored.
- `brackets` and `rates` are lists tried in order: the first step whose `over` the taxable income exceeds (or whose `from_year` the tax year has reached) applies, and the last step's `over` or `from_year` is `null`. Filing-status values are `{"single": ..., "mfj": ...}`.
- `registry` tables keep their original keys. A `_YYYY` suffix holds the value from that tax year on, and the unsuffixed key covers the years before. Never copy a current value into an earlier year.

The files load and are validated when the server starts. `tests/test_compliance_golden.py` pins the exact output of every compliance tool over a wide input grid, so a rate change also updates those files (`uv run python tests/golden_compliance.py --write`) and the diff shows which answers moved.
