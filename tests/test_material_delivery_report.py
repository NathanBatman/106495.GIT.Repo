import csv
import hashlib
import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("report", ROOT / "scripts/material_delivery_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
SNAPSHOT = date(2026, 10, 27)
HISTORICAL_SHA256 = "bf901a470f772a5a8f44ccfcb27cfd654fa47db5ac27924c2b4919508982d697"


def sample(**changes):
    row = dict.fromkeys(report.REQUIRED, "")
    row.update(Vendor="Example vendor", ContractNumber="00123", ExpeditingContractId="42",
               ExpLineItemId="7", Description="Pump", ArrivalAtSiteScheduled="2026-10-01",
               ArrivalAtSiteForecasted="2026-11-01", ROSDate="2026-11-01", ArrivalAtSiteStatus="Late")
    row.update(changes)
    return row


class DateSemantics(unittest.TestCase):
    def check_row(self, **changes):
        return report.analyze(sample(**changes), 1, SNAPSHOT, 30)

    def test_forecast_and_ros_are_independent(self):
        row = self.check_row()
        self.assertEqual(row["report_forecast_variance_days"], 31)
        self.assertEqual(row["report_forecast_vs_ros_days"], 0)
        self.assertTrue(row["report_significant_forecast_delay"])
        self.assertFalse(row["report_recorded_actual_late"])

    def test_threshold_and_reference_boundaries(self):
        self.assertFalse(self.check_row(ArrivalAtSiteForecasted="2026-10-31")["report_significant_forecast_delay"])
        row = self.check_row(ArrivalAtSiteForecasted="2026-10-27")
        self.assertTrue(row["report_late_future_no_actual_cohort"])
        self.assertFalse(row["report_past_forecast_no_actual"])

    def test_actual_supersedes_forecast(self):
        row = self.check_row(ArrivalAtSiteActual="2026-10-15")
        self.assertEqual(row["report_actual_variance_days"], 14)
        self.assertTrue(row["report_recorded_actual_late"])
        self.assertFalse(row["report_open_forecast_delay"])
        self.assertFalse(row["report_late_future_no_actual_cohort"])

    def test_future_actual_requires_review(self):
        row = self.check_row(ArrivalAtSiteActual="2026-11-02")
        self.assertFalse(row["report_recorded_actual_late"])
        self.assertFalse(row["report_open_forecast_delay"])
        self.assertIn("after analysis date", row["report_date_issues"])

    def test_past_forecast_does_not_prove_delivery(self):
        row = self.check_row(ArrivalAtSiteForecasted="2026-10-20")
        self.assertTrue(row["report_past_forecast_no_actual"])
        self.assertFalse(row["report_recorded_actual_late"])

    def test_invalid_and_missing_dates(self):
        row = self.check_row(ArrivalAtSiteScheduled="", ArrivalAtSiteActual="garbage")
        self.assertIsNone(row["report_actual_variance_days"])
        self.assertFalse(row["report_open_forecast_delay"])
        self.assertTrue(row["report_requires_investigation"])
        self.assertIn("Invalid ArrivalAtSiteActual", row["report_date_issues"])
        self.assertEqual(report.parse_date("2026-10-27T13:00:00Z"), SNAPSHOT)
        with self.assertRaises(ValueError):
            report.parse_date("10/11/2026")

    def test_early_arrival_and_status_conflict(self):
        row = self.check_row(ArrivalAtSiteForecasted="2026-09-30")
        self.assertEqual(row["report_forecast_variance_days"], -1)
        self.assertFalse(row["report_open_forecast_delay"])
        self.assertIn("Status disagrees", row["report_date_issues"])


class ReportIntegration(unittest.TestCase):
    def test_round_trip_and_input_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            with source.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=report.REQUIRED)
                writer.writeheader()
                writer.writerow(sample(Vendor='=HYPERLINK("bad")', Description='<script>alert(1)</script>'))
            before = source.read_bytes()
            meta, _ = report.generate(source, root/"out", SNAPSHOT)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(meta["counts"]["source_records"], 1)
            with (root/"out/all_records.csv").open(newline="") as stream:
                output = list(csv.DictReader(stream))[0]
            self.assertEqual(output["ContractNumber"], "00123")
            self.assertTrue(output["Vendor"].startswith("'="))
            rendered = (root/"out/report.html").read_text()
            self.assertNotIn("<script>", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            protected = root/"all_records.csv"
            protected.write_bytes(before)
            with self.assertRaises(ValueError):
                report.generate(protected, root, SNAPSHOT)
            self.assertEqual(protected.read_bytes(), before)

    def test_missing_schema_and_bad_row_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"bad.csv"
            source.write_text("Vendor\nA\n")
            with self.assertRaises(ValueError):
                report.generate(source, Path(directory)/"out", SNAPSHOT)
            source.write_text(",".join(report.REQUIRED) + "\nA\n")
            with self.assertRaises(ValueError):
                report.generate(source, Path(directory)/"out", SNAPSHOT)

    def test_historical_snapshot(self):
        source = ROOT / "expediting_export.csv"
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != HISTORICAL_SHA256:
            self.skipTest("Historical input changed; use the pinned source revision in README for this regression")
        with tempfile.TemporaryDirectory() as directory:
            before = source.read_bytes()
            meta, rows = report.generate(source, directory, SNAPSHOT)
            self.assertEqual(meta["counts"], dict(source_records=867, source_columns=96,
                source_status_late=120, late_future_no_actual=66, late_future_no_actual_significant=10,
                open_forecast_delays=104, significant_open_forecast_delays=21, recorded_actual_late=16,
                past_forecast_no_actual=69, investigation_records=427))
            firewater = [r for r in rows if r["Description"] in ("Firewater Recirc Pump", "Firewater Recirc Motor")]
            self.assertEqual(len(firewater), 2)
            for row in firewater:
                self.assertEqual(row["ArrivalAtSiteScheduled"], "2026-12-15")
                self.assertEqual(row["ArrivalAtSiteForecasted"], "2027-06-10")
                self.assertEqual(row["report_forecast_variance_days"], 177)
                self.assertEqual(row["report_forecast_vs_ros_days"], 0)
            # Independent date arithmetic reconciles every row, not only the headline cohort.
            for row in rows:
                for end, start, key in [
                    ("ArrivalAtSiteForecasted", "ArrivalAtSiteScheduled", "report_forecast_variance_days"),
                    ("ArrivalAtSiteActual", "ArrivalAtSiteScheduled", "report_actual_variance_days"),
                    ("ArrivalAtSiteForecasted", "ROSDate", "report_forecast_vs_ros_days"),
                    ("ArrivalAtSiteActual", "ROSDate", "report_actual_vs_ros_days")]:
                    expected = ((date.fromisoformat(row[end]) - date.fromisoformat(row[start])).days
                                if row[end] and row[start] else None)
                    self.assertEqual(row[key], expected)
            for keys in [["Vendor"], ["Vendor", "ContractNumber", "ExpeditingContractId", "Description"]]:
                groups = report.summarize_groups(rows, keys)
                self.assertEqual(sum(g["records"] for g in groups), 867)
                self.assertEqual(sum(g["exceptions"] for g in groups), 427)
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
