"""Hash-pinned server authority for the authored Wayline campaign order."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping


CAMPAIGN_CATALOG_V1_SHA256 = (
    "5509097676eccc6c3848bfb64295ac931c73621a1120b9431af0ccc8e793d513"
)
_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}")
_BATTLE_SUFFIXES = ("route_1", "route_2", "route_3", "elite", "boss")
_LEAD_IN_TIERS = ("route_1", "route_2", "route_3", "elite")
_BATTLE_ITEM_COUNTS = MappingProxyType(
    {
        "route_1": 3,
        "route_2": 4,
        "route_3": 4,
        "elite": 5,
        "world_boss": 8,
        "campaign_finale": 10,
    }
)


class CampaignCatalogError(ValueError):
    """Raised when campaign authority is missing, modified, or malformed."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CampaignCatalogError(f"duplicate campaign catalog key: {key}")
        result[key] = value
    return result


def _identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise CampaignCatalogError(f"{name} is not a valid campaign identifier")
    return value


@dataclass(frozen=True, slots=True)
class CampaignBattle:
    sequence: int
    battle_id: str
    tier: str
    item_count: int
    is_lead_in: bool
    is_boss: bool


@dataclass(frozen=True, slots=True)
class CampaignWorld:
    sequence: int
    world_id: str
    core_subskill_ids: tuple[str, ...]
    battles: tuple[CampaignBattle, ...]


@dataclass(frozen=True, slots=True)
class CampaignCatalog:
    schema_version: str
    catalog_id: str
    initial_world_id: str
    worlds: tuple[CampaignWorld, ...]
    _by_id: Mapping[str, CampaignWorld]

    @classmethod
    def load(cls, path: Path) -> "CampaignCatalog":
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CampaignCatalogError(
                f"cannot load campaign catalog: {path}"
            ) from exc
        return cls._from_bytes(raw, source=path)

    @classmethod
    def _from_bytes(
        cls,
        raw: bytes,
        *,
        source: Path,
    ) -> "CampaignCatalog":
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except CampaignCatalogError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CampaignCatalogError(
                f"cannot load campaign catalog: {source}"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "catalog_id",
            "initial_world_id",
            "worlds",
        }:
            raise CampaignCatalogError(
                "campaign catalog fields do not match the v1 contract"
            )
        if payload["schema_version"] != "wayline.campaign-catalog.v1":
            raise CampaignCatalogError("unsupported campaign catalog schema")
        catalog_id = payload["catalog_id"]
        if catalog_id != "wayline-campaign-v1":
            raise CampaignCatalogError("unsupported campaign catalog identity")
        raw_worlds = payload["worlds"]
        if not isinstance(raw_worlds, list):
            raise CampaignCatalogError("campaign worlds must be a list")

        worlds: list[CampaignWorld] = []
        world_ids: set[str] = set()
        for raw in raw_worlds:
            if not isinstance(raw, dict) or set(raw) != {
                "sequence",
                "world_id",
                "core_subskill_ids",
                "battles",
            }:
                raise CampaignCatalogError(
                    "campaign world fields do not match the v1 contract"
                )
            sequence = raw["sequence"]
            if (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 1
            ):
                raise CampaignCatalogError("world sequence must be a positive integer")
            world_id = _identifier("world_id", raw["world_id"])
            if world_id in world_ids:
                raise CampaignCatalogError(f"duplicate campaign world: {world_id}")
            raw_skills = raw["core_subskill_ids"]
            if not isinstance(raw_skills, list) or not raw_skills:
                raise CampaignCatalogError("core_subskill_ids must be a nonempty list")
            skills = tuple(
                _identifier("core_subskill_id", value) for value in raw_skills
            )
            if len(skills) != len(set(skills)):
                raise CampaignCatalogError(
                    f"duplicate core subskill in campaign world: {world_id}"
                )
            raw_battles = raw["battles"]
            if not isinstance(raw_battles, list) or len(raw_battles) != 5:
                raise CampaignCatalogError(
                    f"campaign world must contain exactly five battles: {world_id}"
                )
            battles: list[CampaignBattle] = []
            for expected_sequence, raw_battle in enumerate(raw_battles, start=1):
                if not isinstance(raw_battle, dict) or set(raw_battle) != {
                    "sequence",
                    "battle_id",
                    "tier",
                }:
                    raise CampaignCatalogError(
                        "campaign battle fields do not match the v1 contract"
                    )
                battle_sequence = raw_battle["sequence"]
                if (
                    not isinstance(battle_sequence, int)
                    or isinstance(battle_sequence, bool)
                    or battle_sequence != expected_sequence
                ):
                    raise CampaignCatalogError(
                        f"battle order is not canonical for world: {world_id}"
                    )
                expected_id = (
                    f"{world_id}_{_BATTLE_SUFFIXES[expected_sequence - 1]}"
                )
                battle_id = _identifier("battle_id", raw_battle["battle_id"])
                if battle_id != expected_id:
                    raise CampaignCatalogError(
                        f"battle identity is not canonical for world: {world_id}"
                    )
                if expected_sequence <= 4:
                    expected_tier = _LEAD_IN_TIERS[expected_sequence - 1]
                else:
                    expected_tier = (
                        "campaign_finale"
                        if world_id == "order_spire"
                        else "world_boss"
                    )
                tier = _identifier("battle tier", raw_battle["tier"])
                if tier != expected_tier:
                    raise CampaignCatalogError(
                        f"battle tier is not canonical for world: {world_id}"
                    )
                battles.append(
                    CampaignBattle(
                        sequence=battle_sequence,
                        battle_id=battle_id,
                        tier=tier,
                        item_count=_BATTLE_ITEM_COUNTS[tier],
                        is_lead_in=expected_sequence <= 4,
                        is_boss=expected_sequence == 5,
                    )
                )
            worlds.append(
                CampaignWorld(sequence, world_id, skills, tuple(battles))
            )
            world_ids.add(world_id)

        if len(worlds) != 9:
            raise CampaignCatalogError("v1 campaign must contain exactly nine worlds")
        if tuple(world.sequence for world in worlds) != tuple(range(1, 10)):
            raise CampaignCatalogError(
                "campaign worlds must use contiguous authored sequence order"
            )
        initial_world_id = _identifier(
            "initial_world_id", payload["initial_world_id"]
        )
        by_id = {world.world_id: world for world in worlds}
        if initial_world_id not in by_id:
            raise CampaignCatalogError("initial world is absent from the campaign")
        if initial_world_id != "valuehold" or by_id[
            initial_world_id
        ].core_subskill_ids != ("place_value", "mental_add_sub"):
            raise CampaignCatalogError(
                "v1 campaign must start with the audited Valuehold curriculum"
            )
        return cls(
            schema_version=payload["schema_version"],
            catalog_id=catalog_id,
            initial_world_id=initial_world_id,
            worlds=tuple(worlds),
            _by_id=MappingProxyType(by_id),
        )

    @classmethod
    def packaged_v1(
        cls,
        *,
        resource_path: Path | None = None,
    ) -> "CampaignCatalog":
        path = resource_path or (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "campaign_catalog_v1.json"
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CampaignCatalogError(
                f"cannot read packaged campaign catalog: {path}"
            ) from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != CAMPAIGN_CATALOG_V1_SHA256:
            raise CampaignCatalogError("packaged campaign catalog digest mismatch")
        return cls._from_bytes(raw, source=path)

    @property
    def initial_world(self) -> CampaignWorld:
        return self._by_id[self.initial_world_id]

    @property
    def curriculum_receipt(self) -> str:
        return f"{self.catalog_id}:{CAMPAIGN_CATALOG_V1_SHA256}"

    def require_battle(
        self,
        *,
        world_id: str,
        battle_id: str,
        battle_tier: str,
        expected_world_sequence: int,
        expected_battle_sequence: int,
    ) -> CampaignBattle:
        """Return only the battle authored at the server-owned campaign position."""

        requested_world = _identifier("world_id", world_id)
        requested_battle = _identifier("battle_id", battle_id)
        requested_tier = _identifier("battle_tier", battle_tier)
        if (
            not isinstance(expected_world_sequence, int)
            or isinstance(expected_world_sequence, bool)
            or not 1 <= expected_world_sequence <= len(self.worlds)
        ):
            raise CampaignCatalogError("expected world sequence is out of range")
        expected_world = self.worlds[expected_world_sequence - 1]
        if requested_world != expected_world.world_id:
            raise CampaignCatalogError(
                "requested world is unknown or out of authored order"
            )
        if (
            not isinstance(expected_battle_sequence, int)
            or isinstance(expected_battle_sequence, bool)
            or not 1 <= expected_battle_sequence <= len(expected_world.battles)
        ):
            raise CampaignCatalogError("expected battle sequence is out of range")
        expected_battle = expected_world.battles[expected_battle_sequence - 1]
        if requested_battle != expected_battle.battle_id:
            raise CampaignCatalogError(
                "requested battle is unknown or out of authored order"
            )
        if requested_tier != expected_battle.tier:
            raise CampaignCatalogError(
                "requested battle tier differs from server campaign authority"
            )
        return expected_battle
