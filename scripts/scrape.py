"""Fetch, validate, and publish BMWET fuel-price data."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.bmwet.gv.at/Themen/Energie/kosten.html"
USER_AGENT = "bmwet-fuel-prices/1.0"
PRICE_KEYS = (
    "diesel",
    "eurosuper",
    "super_plus",
    "heating_oil_bulk",
    "heating_oil_station",
)
PRICE_HEADERS = {
    "diesel": {"diesel"},
    "eurosuper": {"eurosuper"},
    "super_plus": {"super plus"},
    "heating_oil_bulk": {
        "heizöl extraleicht: ab 2000 liter",
        "heizöl extraleicht: ab 2.000 liter",
    },
    "heating_oil_station": {"heizöl extraleicht: tankstelle"},
}
DATE_HEADERS = {"stichtag"}


class ScrapeError(ValueError):
    """Raised when the source cannot be parsed safely."""


@dataclass(frozen=True)
class PriceRow:
    date: date
    prices: dict[str, Decimal]


@dataclass(frozen=True)
class ParsedPrices:
    current: PriceRow
    history: tuple[PriceRow, ...]


def normalize_text(value: str) -> str:
    """Normalize HTML whitespace without changing the source wording."""

    return " ".join(value.replace("\xa0", " ").split()).strip().lower()


def parse_price(value: str) -> Decimal:
    """Parse a positive BMWET decimal-comma price."""

    text = "".join(value.replace("\xa0", " ").split())
    if not text:
        raise ScrapeError("empty price")
    if "," in text and "." in text:
        raise ScrapeError(f"ambiguous price: {value!r}")

    try:
        price = Decimal(text.replace(",", "."))
    except InvalidOperation as error:
        raise ScrapeError(f"invalid price: {value!r}") from error

    if not price.is_finite() or not 0 < price < 10:
        raise ScrapeError(f"price outside expected range: {value!r}")
    return price


def parse_date(value: str) -> date:
    """Parse the German date format used by the BMWET table."""

    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except ValueError as error:
        raise ScrapeError(f"invalid date: {value!r}") from error


def _header_cells(table) -> list:
    head = table.find("thead")
    if head is not None:
        row = head.find("tr")
        return [] if row is None else row.find_all("th", recursive=False)

    row = table.find("tr")
    return [] if row is None else row.find_all(["th", "td"], recursive=False)


def _find_relevant_table(soup: BeautifulSoup):
    matches = []
    for table in soup.find_all("table"):
        headers = [
            normalize_text(cell.get_text(" ", strip=True))
            for cell in _header_cells(table)
        ]
        indexes = {}
        for index, header in enumerate(headers):
            for key, accepted_headers in PRICE_HEADERS.items():
                if header in accepted_headers:
                    indexes[key] = index
                    break
            if header in DATE_HEADERS:
                indexes["date"] = index

        if set(indexes) == {"date", *PRICE_KEYS}:
            matches.append((table, indexes))

    if len(matches) != 1:
        raise ScrapeError(f"expected one relevant table, found {len(matches)}")
    return matches[0]


def parse_html(html: str, *, today: date | None = None) -> ParsedPrices:
    """Parse the current BMWET table and return newest-first records."""

    soup = BeautifulSoup(html, "html.parser")
    table, indexes = _find_relevant_table(soup)
    body = table.find("tbody")
    rows = (
        body.find_all("tr", recursive=False)
        if body is not None
        else table.find_all("tr")[1:]
    )
    records = []
    today = today or date.today()
    last_index = max(indexes.values())

    for row in rows:
        cells = row.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        if len(cells) <= last_index:
            raise ScrapeError("data row has fewer cells than the table header")

        record_date = parse_date(cells[indexes["date"]].get_text(" ", strip=True))
        if record_date > today:
            raise ScrapeError(f"date is in the future: {record_date.isoformat()}")

        prices = {
            key: parse_price(cells[indexes[key]].get_text(" ", strip=True))
            for key in PRICE_KEYS
        }
        records.append(PriceRow(record_date, prices))

    if not records:
        raise ScrapeError("relevant table contains no data rows")

    records.sort(key=lambda record: record.date, reverse=True)
    if len({record.date for record in records}) != len(records):
        raise ScrapeError("relevant table contains duplicate dates")
    return ParsedPrices(records[0], tuple(records))


def _stored_row(value: object) -> PriceRow:
    if not isinstance(value, dict):
        raise ScrapeError("stored history row is not an object")

    try:
        record_date = date.fromisoformat(value["date"])
        stored_prices = value["prices"]
        prices = {
            key: parse_price(str(stored_prices[key]))
            for key in PRICE_KEYS
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ScrapeError("invalid stored history row") from error
    return PriceRow(record_date, prices)


def load_existing_document(path: Path) -> dict | None:
    """Load the canonical document, if it exists."""

    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScrapeError(f"cannot read stored data: {path}") from error
    if not isinstance(document, dict) or not isinstance(document.get("history"), list):
        raise ScrapeError("stored data has no valid history")
    return document


def load_history(document: dict | None) -> tuple[PriceRow, ...]:
    """Convert stored history into validated rows."""

    if document is None:
        return ()
    rows = tuple(_stored_row(value) for value in document["history"])
    if len({row.date for row in rows}) != len(rows):
        raise ScrapeError("stored history contains duplicate dates")
    return tuple(sorted(rows, key=lambda row: row.date, reverse=True))


def merge_history(existing: tuple[PriceRow, ...], scraped: ParsedPrices) -> ParsedPrices:
    """Merge scraped rows into history, replacing corrected dates."""

    records = {row.date: row for row in existing}
    records.update({row.date: row for row in scraped.history})
    history = tuple(sorted(records.values(), key=lambda row: row.date, reverse=True))
    return ParsedPrices(scraped.current, history)


def fetch_html(url: str = SOURCE_URL) -> str:
    """Fetch the source page with a bounded request."""

    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
        raise ScrapeError("source response is not HTML")
    return response.text


def _price_value(price: Decimal) -> float | int:
    number = float(price)
    return int(number) if price == price.to_integral_value() else number


def _row_json(row: PriceRow) -> dict:
    return {
        "date": row.date.isoformat(),
        "prices": {key: _price_value(row.prices[key]) for key in PRICE_KEYS},
    }


def _without_retrieved_at(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != "retrieved_at"}


def build_documents(data: ParsedPrices, retrieved_at: str) -> tuple[dict, dict]:
    """Build the small latest document and the full history document."""

    metadata = {
        "schema_version": 1,
        "source": {"name": "BMWET", "url": SOURCE_URL},
        "retrieved_at": retrieved_at,
    }
    current = _row_json(data.current)
    latest = {**metadata, "current": current}
    prices = {
        **metadata,
        "current": current,
        "history": [_row_json(row) for row in data.history],
    }
    return latest, prices


def _format_price(price: Decimal) -> str:
    text = format(price, "f").rstrip("0").rstrip(".")
    return text or "0"


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_outputs(data: ParsedPrices, output_dir: Path, retrieved_at: str) -> None:
    """Write versioned JSON and CSV output files."""

    latest, prices = build_documents(data, retrieved_at)
    _write_json(output_dir / "latest.json", latest)
    _write_json(output_dir / "prices.json", prices)

    with (output_dir / "prices.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(["date", *PRICE_KEYS])
        for row in data.history:
            writer.writerow(
                [row.date.isoformat(), *(_format_price(row.prices[key]) for key in PRICE_KEYS)]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("public/api/v1"))
    parser.add_argument("--data", type=Path, default=Path("data/prices.json"))
    parser.add_argument("--url", default=SOURCE_URL)
    args = parser.parse_args()

    try:
        existing_document = load_existing_document(args.data)
        existing_history = load_history(existing_document)
        data = parse_html(
            fetch_html(args.url),
            today=datetime.now(ZoneInfo("Europe/Vienna")).date(),
        )
        data = merge_history(existing_history, data)
        now = datetime.now(ZoneInfo("Europe/Vienna"))
        retrieved_at = now.isoformat(timespec="seconds")
        if existing_document is not None:
            candidate = build_documents(data, retrieved_at)[1]
            if _without_retrieved_at(existing_document) == _without_retrieved_at(candidate):
                retrieved_at = existing_document.get("retrieved_at", retrieved_at)
        _write_json(args.data, build_documents(data, retrieved_at)[1])
        write_outputs(data, args.output, retrieved_at)
    except (requests.RequestException, ScrapeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Wrote BMWET data to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
