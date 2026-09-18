import csv
import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.scrape import (
    ScrapeError,
    build_documents,
    load_history,
    merge_history,
    parse_html,
    write_outputs,
)


FIXTURE = Path(__file__).parent / "fixtures" / "bmwet-kosten.html"
FIXTURE_HTML = FIXTURE.read_text(encoding="utf-8")


class ScraperTests(unittest.TestCase):
    def test_parses_all_price_columns_and_sorts_history(self):
        data = parse_html(FIXTURE_HTML, today=date(2026, 9, 18))

        self.assertEqual(data.current.date, date(2026, 9, 14))
        self.assertEqual(data.current.prices["diesel"], Decimal("2.197"))
        self.assertEqual(data.current.prices["heating_oil_bulk"], Decimal("1.804"))
        self.assertEqual(len(data.history), 3)
        self.assertEqual(data.history[-1].date, date(2026, 8, 31))

    def test_accepts_the_dotted_bulk_quantity_header(self):
        html = FIXTURE_HTML.replace("ab 2000 Liter", "ab 2.000 Liter")

        data = parse_html(html, today=date(2026, 9, 18))

        self.assertEqual(data.current.prices["heating_oil_bulk"], Decimal("1.804"))

    def test_rejects_missing_relevant_table(self):
        html = "<html><body><table><tr><th>Name</th></tr></table></body></html>"

        with self.assertRaises(ScrapeError):
            parse_html(html, today=date(2026, 9, 18))

    def test_rejects_missing_price_column(self):
        html = FIXTURE_HTML.replace("<th scope=\"col\">Super Plus</th>", "")

        with self.assertRaises(ScrapeError):
            parse_html(html, today=date(2026, 9, 18))

    def test_rejects_invalid_price(self):
        html = FIXTURE_HTML.replace("1,804", "not-a-price")

        with self.assertRaises(ScrapeError):
            parse_html(html, today=date(2026, 9, 18))

    def test_rejects_invalid_date(self):
        html = FIXTURE_HTML.replace("14.09.2026", "not-a-date")

        with self.assertRaises(ScrapeError):
            parse_html(html, today=date(2026, 9, 18))

    def test_rejects_future_date(self):
        with self.assertRaises(ScrapeError):
            parse_html(FIXTURE_HTML, today=date(2026, 9, 13))

    def test_merges_old_history_and_corrects_same_date(self):
        data = parse_html(FIXTURE_HTML, today=date(2026, 9, 18))
        existing = load_history({
            "history": [
                {
                    "date": "2025-12-29",
                    "prices": {
                        "diesel": 1,
                        "eurosuper": 1,
                        "super_plus": 1,
                        "heating_oil_bulk": 1,
                        "heating_oil_station": 1,
                    },
                },
                {
                    "date": "2026-09-14",
                    "prices": {
                        "diesel": 9,
                        "eurosuper": 9,
                        "super_plus": 9,
                        "heating_oil_bulk": 9,
                        "heating_oil_station": 9,
                    },
                },
            ]
        })

        merged = merge_history(existing, data)

        self.assertEqual(len(merged.history), 4)
        self.assertEqual(merged.history[0].prices["diesel"], Decimal("2.197"))
        self.assertEqual(merged.history[-1].date, date(2025, 12, 29))

    def test_merging_same_data_is_idempotent(self):
        data = parse_html(FIXTURE_HTML, today=date(2026, 9, 18))

        once = merge_history((), data)
        twice = merge_history(once.history, data)

        self.assertEqual(once.history, twice.history)

    def test_writes_versioned_documents(self):
        data = parse_html(FIXTURE_HTML, today=date(2026, 9, 18))

        with TemporaryDirectory() as directory:
            output_dir = Path(directory) / "api" / "v1"
            write_outputs(data, output_dir, "2026-09-18T10:00:00+02:00")

            latest = json.loads((output_dir / "latest.json").read_text())
            prices = json.loads((output_dir / "prices.json").read_text())
            with (output_dir / "prices.csv").open(newline="") as file:
                rows = list(csv.reader(file))

        self.assertEqual(latest["current"]["prices"]["super_plus"], 2.132)
        self.assertNotIn("history", latest)
        self.assertEqual(len(prices["history"]), 3)
        self.assertEqual(rows[0], [
            "date",
            "diesel",
            "eurosuper",
            "super_plus",
            "heating_oil_bulk",
            "heating_oil_station",
        ])
        self.assertEqual(rows[1][0], "2026-09-14")


if __name__ == "__main__":
    unittest.main()
