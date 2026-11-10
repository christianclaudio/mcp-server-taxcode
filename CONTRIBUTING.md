# Contributing Guidelines

Thank you for your interest in contributing!

1. Fork or branch from `main`: `git checkout -b feat/your-feature`.
2. Ensure strict static typing: `mypy --strict src/`.
3. Ensure the test suite passes: `pytest` (coverage threshold configured in `pyproject.toml`).
4. Validate tool contracts: `python scripts/check_tool_contract.py`.
5. Open a Pull Request. Merges are performed via **Squash Merge** with Conventional Commit titles (`feat:`, `fix:`, `docs:`, `chore:`).
6. Changing statute or publication rows? Follow "Adding or changing a row" below and run `uv run python scripts/build_catalog.py --check`.
7. Do not edit version numbers or `CHANGELOG.md`: the git tag is the version, and GitHub Releases are the changelog. The squash commit message is the PR body, so a breaking (`feat!:` / `fix!:`) PR ends its body with one `BREAKING CHANGE:` final paragraph that lists the breaking items, with no migration steps.
8. Do not edit `docker/build-backend.txt` by hand.

## Adding or changing a row

Statute and publication rows live only in the data files under `src/taxcode_mcp/data/`. The server seeds its database from them, and `scripts/ingest_statutes.py` and the docs read them too. Never add rows as Python literals.

| File | Holds |
| --- | --- |
| `jurisdictions.json` | Jurisdiction codes in catalog order. Rows load in this order, then in file order. |
| `statutes/<code>.json` | One jurisdiction's statute rows (`us.json`, `pr.json`, `can.json`, `ga.json`, ...). `text_en`, `text_es` and optional `text_fr` (required on `CAN` rows, stored in `statutes_fr`) are lists of lines, joined with a newline when loaded. |
| `publications/<code>.json` | Publications whose `id` starts with `<code>-`. |
| `manifest/<code>.json` | One source record per statute and publication row and per rate table of that jurisdiction. |
| `rates/<code>.json` | The rate table the compliance tools compute a state's or country's tax from (`RATE_TABLE_MODELS` and `COUNTRY_RATE_TABLES` in `src/taxcode_mcp/catalog.py`). See "Changing a rate table" below. |

Every manifest entry has every field below. A value we don't have is the explicit string `"not_recorded"`, never a missing key and never a guess.

| Field | Meaning |
| --- | --- |
| `kind`, `id` | `statute` or `publication` and the row's `id`, or `rate_table` and `<code>-rates`. |
| `url` | Must equal the row's (or rate or federal table's) `source_url`. |
| `edition` | Which text the source at `url` is: `as_enacted` (the law as passed), `amended_compilation` (a compilation with later amendments folded in, such as an OGP "según enmendada" text), or `"not_recorded"`. |
| `official` | `true` when the URL is on the issuing government body's own site, `false` for a third-party site, or `"not_recorded"`. It says nothing about whether the stored text is official. |
| `licence` | Reuse terms of the source, or `"not_recorded"`. |
| `retrieved` | Date the source was fetched (`YYYY-MM-DD`), or `"not_recorded"`. |
| `content_sha256` | SHA-256 of the fetched source artifact, or `"not_recorded"`. |
| `enacting_law` | Law that enacted or last amended the text, or `"not_recorded"`. |
| `effective_date` | Date the text took effect (`YYYY-MM-DD`), or `"not_recorded"`. |
| `verification` | `verified_verbatim` (checked word for word against the fetched source; needs `retrieved` and `content_sha256`), `summary` (the stored text is a summary, not a copy), or `"not_recorded"`. |
| `french_source` | Optional, `statute` entries only, after `verification`, and required when the row has `text_fr`. An object with `url` (https) and `content_sha256` (64 lowercase hex digits) for the fetched French source artifact. |
| `verification_note` | Optional, and only on a `rate_table` whose `verification` is `not_recorded`, after `verification` and before `year_sources`. A non-empty string saying why that table could not be confirmed. A checked table does not carry it. |
| `year_sources` | Optional, `rate_table` and `federal_table` entries only, as the last field. A list of the sources for figures of other tax years or corroborating second sources on `url`'s year, `tax_year` ascending. Each has exactly `tax_year`, `values` (the table values it confirms, keyed by their path in `params`, such as `rates[0].rate`, `deduction_mfj_2025` or `years[0].brackets[4].rate`, or for the federal table in its document, such as `years[6].single`; each must equal the table's value), `url` (https), `official` (`true`), `retrieved` and `content_sha256` (`"not_recorded"` when the bytes could not be kept). |

To add or change a row:

1. Edit the row in `statutes/<code>.json` or `publications/<code>.json`. A new prior-year version is a new row with its own `id` and `effective_year`; set `superseded_year` on the version it replaces. Never copy current text into an earlier year.
2. Add or update its entry in `manifest/<code>.json`.
3. Run `uv run python scripts/build_catalog.py`. It rewrites the data files in canonical form and regenerates `docs/<code>/*.md`, `docs/<code>/llms-full.txt` (that jurisdiction's full text), `docs/llms.txt` and `docs/llms-full.txt` (an index of the per-jurisdiction files), so a content PR only changes the full-text file of the jurisdictions it touches.
4. Run `uv run python scripts/build_catalog.py --check` and the test suite. CI runs the same check and fails if a data file, a manifest entry or a generated docs file is out of step.

A new jurisdiction also needs its code in `jurisdictions.json` and, for its name to resolve, in `JURISDICTION_NAMES` (`src/taxcode_mcp/db.py`). Content-only PRs touch just the data files, the manifest and the generated docs; keep code changes in their own PR.

## Changing a rate table

Every deduction, exemption, bracket, rate and per-year value the compliance tools use lives in `src/taxcode_mcp/data/rates/<code>.json`, one file per state that has one plus country tables in `COUNTRY_RATE_TABLES` (`RATE_TABLE_MODELS` in `src/taxcode_mcp/catalog.py`). FL and TX have none. Never add a rate as a Python literal.

- `model` names the shape of `params` and which formula reads it. It must match `RATE_TABLE_MODELS`.
- Amounts and rates are floats (`1000.0`, not `1000`), because tool output prints them as stored.
- `brackets` and `rates` are lists tried in order: the first step whose `over` the taxable income exceeds (or whose `from_year` the tax year has reached) applies, and the last step's `over` or `from_year` is `null`. Filing-status values are `{"single": ..., "mfj": ...}`.
- Each `rates` step also carries its prose: a `flat_by_year` step (CO, NC) its whole `rate_desc`, and a `georgia` step the `law` that set that year's rate, which both Georgia answers cite. Georgia's flat rate starts with tax year 2024, so an earlier tax year gets the oldest step and its answers say they are an estimate.
- Each table has a `source_url` and a `rate_table` entry in `manifest/<code>.json` with the same `url`. A table whose numbers have not been checked against official text keeps `"not_recorded"` in both. Record a source only once it has been checked, and only an official one: `url` is the document the brackets were read from, `retrieved` the day it was fetched and `content_sha256` the hash of the fetched bytes (`"not_recorded"` when the bytes could not be kept).
- `registry` tables keep their original keys. A `_YYYY` suffix holds the value from that tax year on, and the unsuffixed key covers the years before. Never copy a current value into an earlier year. A year with no sourced amount is a `not_recorded` entry on `params`, `{"param", "tax_year", "reason"}`, omitted when empty, and the rate table's manifest entry repeats that list after `year_sources`. `param` is the base series (`deduction`, `brackets`, `rates`, `credit_base`, ...); one entry covers both filing statuses. The lookup stops on that exact year. A later year with no marker still uses the last real `_YYYY` value. The catalog rejects a marker on a year the tools read for a different year (Georgia's 2024 and 2026 standard deduction, and Georgia's 2026 rate step) and a Georgia `standard_deduction` marker for a year before 2018, which reads the 2024 amount. A table with no `_YYYY` key and no `rates` step cannot mark a year. The federal standard deduction table has no such list.
- The `flat_with_surtax` table's `surtax`, the `capital_gains_excise` table's `capital_gains_deduction` and the `brackets_cumulative` table's `brackets` and `exemption` follow the same convention: `surtax_2024`, `capital_gains_deduction_2024`, `brackets_2025` or `exemption_2025`, after the model's own fields with years ascending for each name, holds the value from that year on, and the unsuffixed key keeps the earlier years. The `capital_gains_excise` table's `tier` (`{"over": ..., "rate": ...}`, an additional rate on the taxable gains over `over`) takes only `tier_YYYY` keys and doesn't apply before the first one. The `georgia` table's `standard_deduction` and `retirement_exclusion` take `_YYYY` keys the same way (`standard_deduction_2026`, `retirement_exclusion_2027`), after `rates`, years ascending. A tax year after `GA_LAST_FIXED_YEAR` (the last year whose deduction and rate HB 463 of 2026 fixes) uses that year's figures and is labeled an estimate, because HB 463's later increases and rate cuts depend on revenue tests.
- Registry deductions take `_2024`, `_2025` and `_2026`. Registry `brackets` and `rate_desc` take `brackets_YYYY` and `rate_desc_YYYY` for 2024 to 2030, and the four `credit_base_*` / `credit_phaseout_*` amounts take `_2025`. Each needs its unsuffixed key and comes after it, years ascending; `brackets_YYYY` is validated like `brackets` and doesn't count as one of the three graduated keys below. A tax year after `MS_LAST_ENACTED_RATE_YEAR`, `UT_LAST_CREDIT_AMOUNT_YEAR`, `NE_LAST_PUBLISHED_SCHEDULE_YEAR` or `OH_LAST_FIXED_SCHEDULE_YEAR` (tied by tests to the last year each table holds) carries that table's figures forward and is labeled an estimate in its rate prose.
- A graduated `registry` table says how it taxes income below its top bracket with exactly one of two keys. `brackets` is the state's official marginal schedule (`over` and `rate` only, the lowest step's `over` is `null`), and replaces the `top_*`, `zero_*` and `middle_*` keys. `middle_rate` is a single statutory rate for the middle band. Every `evaluate_state_residency` answer carries `middle_rate_is_estimate` (always `false`; the former 65%-of-top stand-in is gone). Never invent a middle-band rate where an official schedule exists.
- A `registry` table may take a nonrefundable credit off the computed tax, as Utah's taxpayer tax credit does, with all six `credit_*` keys: `credit_rate` times the filing status's `credit_base`, less `credit_phaseout_rate` times the taxable income over its `credit_phaseout` amount, never below zero. Model such a credit as a credit, not as a deduction.

- The `canada_federal` model (`rates/can.json`, `COUNTRY_RATE_TABLES`) holds Canada's federal Part I schedule and `ITA s. 128.1` departure-tax parameters: consecutive ascending `years` (each with `tax_year`, graduated `brackets`, `credit_rate`, and `basic_personal_amount` carrying `maximum`, `minimum`, `phaseout_start` and `phaseout_end` for the `ITA s. 118(1.1)` phaseout between the fourth and fifth brackets) plus `capital_gains_inclusion_rate`, `sojourner_days`, `t1161_threshold`, `personal_use_reporting_threshold`, `personal_use_minimum` and `short_term_resident_months`, backed by `can-rates` and `year_sources` in `manifest/can.json`.
- The federal standard deduction by tax year lives in `src/taxcode_mcp/data/federal/standard_deduction.json` (`years`, consecutive and ascending, each with `tax_year`, `single` and `mfj`), with a `federal_table` entry `us-standard-deduction` in `manifest/us.json` and a `year_sources` item for each year. A `flat_by_year` table whose `deduction` is the string `"federal_standard_deduction"` (CO) uses that table's figure for the tax year, or the nearest year it holds, with an estimate label. Other tables keep their own sourced amounts: a state's conformity to a federal figure is its own law.

The files load and are validated when the server starts. `tests/test_compliance_golden.py` pins the exact output of every compliance tool over a wide input grid, so a rate change also updates those files (`uv run python tests/golden_compliance.py --write`) and the diff shows which answers moved.
