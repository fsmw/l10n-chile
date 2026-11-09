# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import hashlib
import json
import re
from functools import lru_cache

from odoo.tools import file_open

PROFILE = "sii_receiver_activity_v1"


@lru_cache(maxsize=1)
def _catalogue():
    """Read the versioned public catalogue locally; never fetch during a review."""
    with file_open(
        "l10n_cl_dte_certification/data/sii_economic_activities.json"
    ) as data:
        catalogue = json.load(data)
    activities = catalogue["activities"]
    canonical = json.dumps(
        activities, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if (
        catalogue["schema"] != 1
        or hashlib.sha256(canonical).hexdigest() != catalogue["activities_sha256"]
        or len({item["code"] for item in activities}) != len(activities)
        or any(not re.fullmatch(r"[0-9]{6}", item["code"]) for item in activities)
    ):
        raise ValueError("Invalid SII activity catalogue.")
    return catalogue


class SiiActivityCatalogue:
    def provenance(self):
        catalogue = _catalogue()
        return {
            "catalogue_version": catalogue["version"],
            "catalogue_digest": catalogue["activities_sha256"],
            "source_url": catalogue["source_url"],
        }

    def activities(self):
        return [dict(item) for item in _catalogue()["activities"]]

    def eligible_activities(self):
        # GiroRecep is limited to 40 characters. Keep complete SII descriptions,
        # avoiding truncation or invented abbreviations in generated DTEs.
        return [
            item
            for item in self.activities()
            if item["vat_affected"] == "SI"
            and item["tax_category"] == "1"
            and item["internet_available"] == "SI"
            and len(item["name"]) <= 40
            and self._is_latin1(item["name"])
        ]

    @staticmethod
    def _is_latin1(value):
        try:
            value.encode("ISO-8859-1")
        except UnicodeEncodeError:
            return False
        return True

    def select(self, seed, excluded_code=None):
        choices = sorted(
            (
                item
                for item in self.eligible_activities()
                if item["code"] != excluded_code
            ),
            key=lambda item: item["code"],
        )
        if not choices:
            raise ValueError("The SII catalogue has no eligible receiver activities.")
        index = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest(), "big")
        return choices[index % len(choices)]

    def matches(self, code, name):
        return any(
            item["code"] == code and item["name"] == name
            for item in self.eligible_activities()
        )
