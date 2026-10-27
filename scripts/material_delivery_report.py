#!/usr/bin/env python3
"""Read-only Material Visibility CSV analysis; Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

DATE_FIELDS = ("ArrivalAtSiteScheduled", "ArrivalAtSiteForecasted", "ArrivalAtSiteActual", "ROSDate")
REQUIRED = ("Vendor", "ContractNumber", "ExpeditingContractId", "Description",
            "ExpLineItemId", "ExpeditingContractLink", "ArrivalAtSiteStatus", *DATE_FIELDS)
NOTICE = ("Historical CSV snapshot analysis. Forecasts and recorded actual dates have not been "
          "verified against live Material Visibility. Missing actual dates do not prove non-delivery. "
          "Calendar-day variances do not establish P6 critical-path or project completion impacts.")


def parse_date(value: str) -> date | None:
    """Accept ISO dates/timestamps; never guess locale-dependent numeric dates."""
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def difference(end, start):
    return (end - start).days if end is not None and start is not None else None


def analyze(row: dict, number: int, snapshot: date, threshold: int) -> dict:
    issues, dates = [], {}
    for key in DATE_FIELDS:
        try:
            dates[key] = parse_date(row[key])
        except ValueError:
            dates[key] = None
            issues.append("Invalid " + key)
    s, f, a, ros = (dates[k] for k in DATE_FIELDS)
    actual_present = bool(row["ArrivalAtSiteActual"].strip())
    forecast_days, actual_days = difference(f, s), difference(a, s)
    forecast_ros, actual_ros = difference(f, ros), difference(a, ros)
    # A future or unparseable actual must never turn into an open forecast assumption.
    recorded_late = bool(a and a <= snapshot and actual_days is not None and actual_days > 0)
    open_delay = bool(not actual_present and forecast_days is not None and forecast_days > 0)
    past_forecast = bool(not actual_present and f and f < snapshot)
    cohort = bool(row["ArrivalAtSiteStatus"].strip().casefold() == "late"
                  and not actual_present and f and f >= snapshot)
    significant = bool(open_delay and forecast_days > threshold)
    if actual_present:
        if a is None:
            classification = "Actual date invalid; investigate"
        elif a > snapshot:
            classification = "Actual date after analysis date; investigate"
            issues.append("Actual date after analysis date")
        elif s is None:
            classification = "Recorded actual; scheduled date unavailable"
        elif recorded_late:
            classification = "Recorded actual late arrival"
        else:
            classification = "Recorded actual on/before scheduled"
    elif past_forecast:
        classification = "Past forecast; actual arrival unrecorded"
    elif open_delay:
        classification = "Forecast delay; actual arrival unrecorded"
    elif f:
        classification = "Forecast only; actual arrival unrecorded"
    else:
        classification = "No usable forecast or actual"
    for key in ("ArrivalAtSiteScheduled", "ROSDate"):
        if not row[key].strip():
            issues.append("Missing " + key)
    if not actual_present and not row["ArrivalAtSiteForecasted"].strip():
        issues.append("Missing forecast and actual arrival dates")
    for key in ("Vendor", "ContractNumber", "Description", "ExpLineItemId"):
        if not row[key].strip():
            issues.append("Missing " + key)
    late_status = row["ArrivalAtSiteStatus"].strip().casefold() == "late"
    basis = actual_days if actual_present else forecast_days
    if basis is not None and (late_status != (basis > 0)):
        issues.append("Status disagrees with date variance")
    reasons = list(issues)
    if open_delay:
        reasons.append(f"Forecast exceeds scheduled arrival by {forecast_days} calendar days")
    if past_forecast:
        reasons.append("Past forecast with no actual recorded; confirm delivery or refresh forecast")
    if not actual_present and forecast_ros is not None and forecast_ros > 0:
        reasons.append(f"Forecast exceeds ROS by {forecast_ros} calendar days; confirm required date")
    if recorded_late:
        reasons.append(f"Recorded actual exceeds scheduled arrival by {actual_days} calendar days")
    if a and a <= snapshot and actual_ros is not None and actual_ros > 0:
        reasons.append(f"Recorded actual exceeds ROS by {actual_ros} calendar days")
    if late_status and basis is None:
        reasons.append("Late source status without calculable arrival variance")
    if significant or (not actual_present and forecast_ros is not None and forecast_ros > 0):
        priority = "1 - Forecast review"
    elif past_forecast or issues:
        priority = "2 - Data/status review"
    elif reasons:
        priority = "3 - Other exception"
    else:
        priority = "4 - No calculated exception"
    return {
        "report_source_record": number, "report_snapshot_date": snapshot.isoformat(),
        "report_classification": classification, "report_priority": priority,
        "report_forecast_variance_days": forecast_days,
        "report_actual_variance_days": actual_days,
        "report_forecast_vs_ros_days": forecast_ros, "report_actual_vs_ros_days": actual_ros,
        "report_open_forecast_delay": open_delay, "report_significant_forecast_delay": significant,
        "report_late_future_no_actual_cohort": cohort,
        "report_recorded_actual_late": recorded_late, "report_past_forecast_no_actual": past_forecast,
        "report_date_issues": "; ".join(issues),
        "report_investigation_reasons": "; ".join(reasons),
        "report_requires_investigation": bool(reasons),
    }


def ranking(row):
    return (row["report_priority"], -(row["report_forecast_variance_days"] or 0),
            -(row["report_actual_variance_days"] or 0), row["Vendor"],
            row["ContractNumber"], row["Description"], row["report_source_record"])


def summarize_groups(records, keys):
    groups = defaultdict(list)
    for row in records:
        groups[tuple(row[k] for k in keys)].append(row)
    result = []
    for values, rows in sorted(groups.items()):
        forecast = [r["report_forecast_variance_days"] for r in rows if r["report_open_forecast_delay"]]
        actual = [r["report_actual_variance_days"] for r in rows if r["report_recorded_actual_late"]]
        result.append(dict(zip(keys, values), records=len(rows),
                           exceptions=sum(r["report_requires_investigation"] for r in rows),
                           open_forecast_delays=len(forecast),
                           significant_forecast_delays=sum(r["report_significant_forecast_delay"] for r in rows),
                           recorded_actual_late=len(actual),
                           past_forecast_no_actual=sum(r["report_past_forecast_no_actual"] for r in rows),
                           maximum_open_forecast_delay_days=max(forecast, default=None),
                           maximum_recorded_actual_delay_days=max(actual, default=None)))
    return sorted(result, key=lambda r: (-r["significant_forecast_delays"], -r["open_forecast_delays"],
                                        tuple(r[k] for k in keys)))


def write_csv(path, records, fields):
    # Neutralize spreadsheet formula prefixes in output only; the input remains untouched.
    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: safe(r.get(k)) for k in fields} for r in records)


DISPLAY = ["report_source_record", "Vendor", "ContractNumber", "ExpeditingContractId", "Description",
           "ArrivalAtSiteScheduled", "ArrivalAtSiteForecasted", "ArrivalAtSiteActual", "ROSDate",
           "report_forecast_variance_days", "report_actual_variance_days", "report_forecast_vs_ros_days",
           "ArrivalAtSiteStatus", "report_classification", "report_investigation_reasons", "ExpeditingContractLink"]


def table(rows, fields):
    def cell(key, value):
        text = html.escape(str(value if value is not None else ""))
        if key == "ExpeditingContractLink" and str(value).startswith(("https://", "http://")):
            return f'<a href="{html.escape(str(value), quote=True)}">Contract</a>'
        return text
    head = "".join(f"<th>{html.escape(k.replace('report_', '').replace('_', ' '))}</th>" for k in fields)
    body = "".join("<tr>" + "".join(f"<td>{cell(k,r.get(k))}</td>" for k in fields) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_report(meta, rows, groups):
    esc = html.escape
    cohort = sorted((r for r in rows if r["report_late_future_no_actual_cohort"]
                     and r["report_significant_forecast_delay"]), key=ranking)
    open_delays = sorted((r for r in rows if r["report_open_forecast_delay"]), key=ranking)
    actual = sorted((r for r in rows if r["report_recorded_actual_late"]),
                    key=lambda r: (-r["report_actual_variance_days"], r["report_source_record"]))
    review = sorted((r for r in rows if r["report_requires_investigation"]), key=ranking)
    counts = "".join(f"<li>{esc(k.replace('_',' '))}: <strong>{v}</strong></li>" for k,v in meta["counts"].items())
    sections = [("Significant future forecasts in source Late cohort", cohort, DISPLAY),
                ("All open forecast delays — ranked by review priority, then days late", open_delays, DISPLAY),
                ("Recorded actual late arrivals — ranked by actual variance", actual, DISPLAY)]
    for name, data in groups.items():
        sections.append(("Grouped by " + name, data, list(data[0]) if data else []))
    sections.append(("All investigation records — data gaps included", review, DISPLAY))
    parts = "".join(f"<h2>{esc(name)} ({len(data)} records/groups)</h2>" + table(data, fields)
                    for name,data,fields in sections)
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Material delivery exceptions — {meta['snapshot_date']}</title>
<style>body{{font:15px system-ui,sans-serif;color:#163147;margin:32px;line-height:1.5}}
h1{{margin-bottom:8px}}h2{{margin-top:36px}}.notice{{padding:18px;background:#fff2ce;border-left:5px solid #b87500}}
.scroll{{overflow:auto;max-height:650px;border:1px solid #cad6df}}table{{border-collapse:collapse;font-size:13px}}
th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid #dce4ea;min-width:110px;vertical-align:top}}
th{{position:sticky;top:0;background:#183c56;color:white}}tr:nth-child(even){{background:#f1f5f8}}
code{{overflow-wrap:anywhere}}a{{color:#006c9b}}li{{margin:4px 0}}</style>
<h1>{esc(meta['project'])} — material delivery exceptions</h1>
<p><strong>Analysis snapshot/reference date: {meta['snapshot_date']}</strong> · Export effective date: unknown.</p>
<p class="notice">{NOTICE}</p>
<p>Source: <a href="{esc(meta['source_url'], quote=True)}">{esc(meta['source_file'])}</a><br>
Source revision: <code>{esc(meta['source_revision'])}</code><br>
Input SHA-256: <code>{meta['source_sha256']}</code></p>
<ul>{counts}</ul>
<p>Positive variance means later. Variance = arrival date − scheduled arrival; ROS variance = arrival date − ROS.
All differences use calendar days. Significant means strictly greater than {meta['significant_days']} days.
Open means no actual date recorded in this file; it is not a verified delivery condition.
Recorded actual late counts require an actual date on/before the analysis date.
All source rows remain in scope, including inactive/completed, parent/subcomponent and missing-line records.
Counts represent export records, not unique shipments or quantities; group totals are separate views and must not be added together.</p>
<p>Review sequence: confirm status/receipt with expediting and the vendor; confirm scheduled/forecast/ROS intent;
then, where needed, map to the current P6 schedule and assess logic, calendars and float. No impact is inferred here.</p>
<p><a href="exceptions.csv">Exception CSV</a> · <a href="all_records.csv">All-record audit CSV</a> ·
<a href="summary.json">Summary and provenance</a></p>{parts}</html>'''


def generate(input_path, output_dir, snapshot, threshold=30, source_url="", source_revision="unknown",
             project="Material delivery"):
    input_path, output_dir = Path(input_path), Path(output_dir)
    if threshold < 0:
        raise ValueError("Significant-days threshold must be nonnegative")
    names = ("all_records.csv", "exceptions.csv", "by_vendor.csv", "by_contract.csv", "by_material.csv",
             "summary.json", "report.html")
    if input_path.resolve() in {(output_dir/n).resolve() for n in names}:
        raise ValueError("Output would overwrite the input CSV")
    raw = input_path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    fields = reader.fieldnames or []
    missing = sorted(set(REQUIRED) - set(fields))
    if missing or len(set(fields)) != len(fields) or any(k.startswith("report_") for k in fields):
        raise ValueError(f"Invalid CSV header; missing={missing}; duplicate/reserved columns are not allowed")
    rows = []
    for n, row in enumerate(reader, 1):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"Malformed CSV record {n}: column count differs from header")
        rows.append(dict(row, **analyze(row, n, snapshot, threshold)))
    counts = {
        "source_records": len(rows), "source_columns": len(fields),
        "source_status_late": sum(r["ArrivalAtSiteStatus"].strip().casefold() == "late" for r in rows),
        "late_future_no_actual": sum(r["report_late_future_no_actual_cohort"] for r in rows),
        "late_future_no_actual_significant": sum(r["report_late_future_no_actual_cohort"] and
                                                 r["report_significant_forecast_delay"] for r in rows),
        "open_forecast_delays": sum(r["report_open_forecast_delay"] for r in rows),
        "significant_open_forecast_delays": sum(r["report_significant_forecast_delay"] for r in rows),
        "recorded_actual_late": sum(r["report_recorded_actual_late"] for r in rows),
        "past_forecast_no_actual": sum(r["report_past_forecast_no_actual"] for r in rows),
        "investigation_records": sum(r["report_requires_investigation"] for r in rows),
    }
    meta = dict(snapshot_date=snapshot.isoformat(), export_effective_date="unknown", project=project,
                source_file=input_path.name, source_url=source_url, source_revision=source_revision,
                source_sha256=hashlib.sha256(raw).hexdigest(), significant_days=threshold,
                variance_unit="calendar days", notice=NOTICE, counts=counts,
                classifications=dict(Counter(r["report_classification"] for r in rows)))
    group_keys = {"vendor": ["Vendor"],
                  "contract": ["Vendor", "ContractNumber", "ExpeditingContractId", "ContractDescription"],
                  "material": ["Vendor", "ContractNumber", "ExpeditingContractId", "Description"]}
    # ContractDescription is optional for future exports.
    for row in rows:
        row.setdefault("ContractDescription", "")
    groups = {name: summarize_groups(rows, keys) for name, keys in group_keys.items()}
    output_dir.mkdir(parents=True, exist_ok=True)
    outfields = fields + list(analyze({k: "" for k in REQUIRED}, 1, snapshot, threshold))
    write_csv(output_dir/"all_records.csv", rows, outfields)
    write_csv(output_dir/"exceptions.csv", sorted((r for r in rows if r["report_requires_investigation"]),
                                                key=ranking), outfields)
    for name, records in groups.items():
        write_csv(output_dir/f"by_{name}.csv", records, list(records[0]) if records else group_keys[name] + ["records"])
    (output_dir/"summary.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (output_dir/"report.html").write_text(render_report(meta, rows, groups), encoding="utf-8")
    return meta, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True,
                        help="Explicit analysis/reference date, YYYY-MM-DD; not the export effective date")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--significant-days", type=int, default=30)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--source-revision", default="unknown")
    parser.add_argument("--project", default="Material delivery")
    args = parser.parse_args()
    try:
        meta, _ = generate(args.input, args.output_dir, args.snapshot_date, args.significant_days,
                           args.source_url, args.source_revision, args.project)
    except (ValueError, OSError, UnicodeError, csv.Error) as error:
        parser.exit(2, f"Error: {error}\n")
    print(json.dumps(meta["counts"], indent=2))


if __name__ == "__main__":
    main()
