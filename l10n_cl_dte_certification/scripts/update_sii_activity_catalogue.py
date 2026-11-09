#!/usr/bin/env python3
# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Refresh the bundled public data explicitly, outside the Odoo runtime."""

import argparse
import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from urllib.request import urlopen

from lxml import html

SOURCE_URL = "https://www.sii.cl/ayudas/ayudas_por_servicios/1956-codigos-1959.html"
_logger = logging.getLogger(__name__)


def build_catalogue(source, retrieved_on):
    root = html.fromstring(source)
    activities = []
    for row in root.xpath("//tr"):
        cells = [" ".join(cell.text_content().split()) for cell in row.xpath("./td")]
        if not cells or not re.fullmatch(r"[0-9]{6}", cells[0]):
            continue
        if len(cells) != 5:
            raise ValueError("The SII activity table layout changed.")
        activities.append(
            dict(
                zip(
                    (
                        "code",
                        "name",
                        "vat_affected",
                        "tax_category",
                        "internet_available",
                    ),
                    cells,
                    strict=True,
                )
            )
        )
    activities.sort(key=lambda item: item["code"])
    if len(activities) < 2 or len({item["code"] for item in activities}) != len(
        activities
    ):
        raise ValueError("The SII activity table is empty or has duplicate codes.")
    canonical = json.dumps(
        activities, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schema": 1,
        "version": "sii_activities_" + retrieved_on.replace("-", ""),
        "source_url": SOURCE_URL,
        "retrieved_on": retrieved_on,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "activities_sha256": hashlib.sha256(canonical).hexdigest(),
        "activities": activities,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-html", type=Path)
    parser.add_argument("--retrieved-on", default=date.today().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1] / "data/sii_economic_activities.json"
        ),
    )
    args = parser.parse_args()
    if args.source_html:
        source = args.source_html.read_bytes()
    else:
        with urlopen(SOURCE_URL, timeout=45) as response:  # noqa: S310
            source = response.read()
    catalogue = build_catalogue(source, args.retrieved_on)
    args.output.write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _logger.info(
        "Saved %s SII activities to %s", len(catalogue["activities"]), args.output
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
