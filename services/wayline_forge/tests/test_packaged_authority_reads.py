from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from services.wayline_forge.app.campaign_catalog import CampaignCatalog
from services.wayline_forge.app.curriculum import Curriculum
from services.wayline_forge.app.procedure_registry import ProcedureRegistry


SERVICE_ROOT = Path(__file__).resolve().parents[1]


class PackagedAuthorityReadTests(unittest.TestCase):
    def test_each_packaged_authority_hashes_and_parses_one_exact_read(self) -> None:
        cases = (
            (
                CampaignCatalog.packaged_v1,
                SERVICE_ROOT / "resources/campaign_catalog_v1.json",
            ),
            (
                Curriculum.packaged_v1,
                SERVICE_ROOT / "resources/curriculum_v1.json",
            ),
            (
                ProcedureRegistry.packaged_v1,
                SERVICE_ROOT / "resources/procedure_registry_v1.json",
            ),
        )
        real_read_bytes = Path.read_bytes

        for loader, resource_path in cases:
            reads: list[Path] = []

            def read_once(path: Path) -> bytes:
                reads.append(path)
                return real_read_bytes(path)

            with (
                self.subTest(loader=loader.__qualname__),
                patch.object(Path, "read_bytes", read_once),
                patch.object(
                    Path,
                    "read_text",
                    side_effect=AssertionError("second pathname read"),
                ),
            ):
                loader(resource_path=resource_path)
                self.assertEqual(reads, [resource_path])


if __name__ == "__main__":
    unittest.main()
