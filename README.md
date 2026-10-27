# Material delivery exception reporting

Reusable, read-only reporting for Homer City Redevelopment EPC using a Material Visibility CSV export. Requires Python 3.10+ and only the standard library. No credentials, live service calls, third-party packages, or modifications to the export are required.

## Historical report

Open [`reports/2026-10-27/report.html`](reports/2026-10-27/report.html) after downloading the repository to view the report in a browser. GitHub displays HTML source rather than hosting it. Use the linked CSV files directly in GitHub or a spreadsheet application.

**October 27, 2026 is the analysis/reference date, not a verified export effective date.** The source CSV was committed September 28, 2026; its underlying extraction/effective date is unknown. The pinned source is [`expediting_export.csv` at `02b837b842f51978c519ba0482b544f20831ab6e`](https://github.com/NathanBatman/106495.GIT.Repo/blob/02b837b842f51978c519ba0482b544f20831ab6e/expediting_export.csv).

Input SHA-256: `bf901a470f772a5a8f44ccfcb27cfd654fa47db5ac27924c2b4919508982d697`.

| Historical check | Export records |
| --- | ---: |
| All source rows / columns | 867 / 96 |
| Source `ArrivalAtSiteStatus` = `Late` | 120 |
| Of those: forecast on/after 2026-10-27; actual blank | 66 |
| Of those: forecast more than 30 days after scheduled | 10 |
| All rows: positive forecast variance with actual blank | 104 |
| All rows: open forecast variance more than 30 days | 21 |
| Recorded actual on/before analysis date, later than scheduled | 16 |
| Forecast before analysis date, actual blank | 69 |
| Any investigation reason, including date/identity gaps | 427 |

The 66 and 10 counts reproduce the supplied investigation cohort, while the full report considers all rows regardless of source status. These metrics overlap; do not add them. The two Blackbird Purchaser Inc firewater recirculation pump/motor records each have scheduled arrival 2026-12-15 and forecast arrival 2027-06-10: **177 calendar days** of slippage. Their ROS dates are also 2027-06-10, so their forecast-to-ROS variances are zero. This warrants confirming the intended scheduled and ROS dates, rather than inferring a ROS miss.

## Regenerate from an export

Run from the repository root:

```bash
python scripts/material_delivery_report.py expediting_export.csv \
  --snapshot-date 2026-10-27 \
  --output-dir reports/2026-10-27 \
  --project "Homer City Redevelopment EPC" \
  --source-revision 02b837b842f51978c519ba0482b544f20831ab6e \
  --source-url https://github.com/NathanBatman/106495.GIT.Repo/blob/02b837b842f51978c519ba0482b544f20831ab6e/expediting_export.csv
```

For a new export, supply its path, an explicit analysis date, and a new output directory. Set `--source-revision` and `--source-url` to that input's provenance (never reuse this historical revision for different bytes). The input hash is calculated automatically. The threshold can be changed using `--significant-days`; its default is 30 and the comparison is strictly greater than the threshold. The script never uses the computer's current date implicitly. Reusing an output directory replaces only the named generated files. Output/input collisions are rejected.

## Outputs and interpretation

- `report.html`: self-contained readable report with significant future forecasts, ranked open forecast delays, recorded late actuals, group summaries, and all investigation records. Dates, ROS comparisons, source status and contract links appear together.
- `all_records.csv`: every input row and all original columns, plus `report_` calculations and flags. Source record numbering starts at 1 after the header; this is a CSV record ordinal, not a physical text line number or shipment ID.
- `exceptions.csv`: all records requiring investigation, sorted by review priority, descending forecast variance, descending actual variance, vendor, contract, description and source record. Recorded late arrivals are also shown separately in HTML, ranked by actual variance.
- `by_vendor.csv`, `by_contract.csv`, `by_material.csv`: record/exception counts and maximum delays. Contracts are grouped by vendor, contract number, expediting contract ID and description; materials by vendor, contract number, expediting contract ID and material description. Each view independently reconciles to the source record count. Group dates are not invented; detailed dates remain in the record files.
- `summary.json`: counts, classifications, explicit reference date, source revision, input hash, threshold and limitations.

Variance definitions (signed calendar days):

| Calculation | Formula |
| --- | --- |
| Forecast arrival variance | `ArrivalAtSiteForecasted - ArrivalAtSiteScheduled` |
| Recorded actual arrival variance | `ArrivalAtSiteActual - ArrivalAtSiteScheduled` |
| Forecast versus ROS | `ArrivalAtSiteForecasted - ROSDate` |
| Actual versus ROS | `ArrivalAtSiteActual - ROSDate` |

Positive means later; zero means same date; negative means earlier. Missing or invalid dates yield a blank variance, never zero. ISO dates and ISO timestamps are accepted; the timestamp's written date is used without timezone conversion. Locale-dependent dates such as `10/11/2026` are rejected as invalid and flagged for review.

An actual date takes precedence when classifying delivery evidence. An actual after the analysis date or an invalid actual requires review and is excluded from both the recorded-actual-late and open-forecast cohorts. Past forecasts without actuals are unresolved export records, not proof of late actual deliveries. Forecast variances remain available for arrived records but do not count as open forecast delays.

Review priority 1 covers open forecasts over the significance threshold or after ROS. Priority 2 covers past forecasts without actuals and data/status issues. Priority 3 covers remaining exceptions, including recorded actual lateness. Priority 4 means no calculated exception. These are reporting rules, not agreed project escalation commitments or P6 criticality scores. Missing dates/identifiers and disagreements between the source `Late` label and the sign of the relevant date variance are explicit investigation reasons. A missing scheduled date or ROS is a data gap even when other dates are available.

All source records remain in scope, including inactive/completed records, parents, subcomponents and records without line IDs. Original flags, quantities, tags, `P6ActivityId`, descriptions and contract links remain in the audit CSV; the report does not deduplicate, filter by activity, sum quantities, or infer shipment counts. The seven records without line IDs remain visible. CSV output neutralizes formula-like text with a leading apostrophe for spreadsheet safety; input bytes remain unchanged. HTML escapes source text and permits only HTTP(S) contract hyperlinks.

## Validation

```bash
python -m unittest discover -s tests -v
```

Tests cover the supplied historical counts, both 177-day firewater variances, independent recomputation of all four date differences on all 867 rows, grouped reconciliation, threshold and date boundaries, actual precedence, stale forecasts, malformed/invalid inputs, HTML escaping, CSV formula handling, and source preservation. The historical regression explicitly skips if the input hash changes; restore the pinned historical CSV to run it. General tests still run for future exports. No live Material Visibility or P6 integration is included.

Before treating a record as a current delivery issue, confirm receipt and dates with expediting/vendor records. Assess P6 effects only after mapping to the current schedule and checking activity logic, calendars and float. Every `P6ActivityId` in this historical CSV is blank, so this report establishes no schedule linkage or critical-path impact.
