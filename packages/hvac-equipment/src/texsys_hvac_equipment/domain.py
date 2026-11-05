"""HVAC input validation and resolution against versioned equipment families."""

from dataclasses import dataclass
from importlib import resources
import json
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Equipment:
  identity: str
  family: str
  model: str
  capacity_kw: float
  heat_rejection_kw: float
  origin: tuple[float, float, float]
  size: tuple[float, float, float]

  def as_dict(self) -> dict[str, Any]:
    return {
      "identity": self.identity,
      "family": self.family,
      "model": self.model,
      "capacity_kw": self.capacity_kw,
      "heat_rejection_kw": self.heat_rejection_kw,
      "origin": list(self.origin),
      "size": list(self.size),
    }


@dataclass(frozen=True)
class HvacSystem:
  equipment: tuple[Equipment, ...]
  installation_order: tuple[str, ...]
  clearance_m: float
  conditions: dict[str, float]
  requirements: dict[str, float]


def domain_path(root: Path) -> Path:
  return root / "src" / "hvac.json"


def has_domain(root: Path) -> bool:
  return domain_path(root).is_file()


def equipment_families() -> dict[str, Any]:
  """Return versioned family data shared by domain targets without copying it."""
  asset = resources.files("texsys_hvac_equipment").joinpath("assets/families.json")
  return json.loads(asset.read_text(encoding="utf-8"))["families"]


def _families() -> dict[str, Any]:
  return equipment_families()


def _number(value: Any, label: str, *, positive: bool = False) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
    raise ValueError(f"{label} must be a finite number.")
  result = float(value)
  if positive and result <= 0:
    raise ValueError(f"{label} must be positive.")
  return result


def _vector(value: Any, label: str, *, positive: bool = False) -> tuple[float, float, float]:
  if not isinstance(value, list) or len(value) != 3:
    raise ValueError(f"{label} must contain three coordinates.")
  return tuple(_number(item, label, positive=positive) for item in value)  # type: ignore[return-value]


def _mapping(value: Any, fields: set[str], label: str) -> dict[str, float]:
  if not isinstance(value, dict) or set(value) != fields:
    raise ValueError(f"{label} must contain exactly: {', '.join(sorted(fields))}.")
  return {name: _number(value[name], f"{label}.{name}") for name in sorted(fields)}


def load_system(root: Path) -> HvacSystem | None:
  path = domain_path(root)
  if not path.exists():
    return None
  try:
    document = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise ValueError(f"Cannot read src/hvac.json: {error}") from error
  if not isinstance(document, dict) or document.get("schema_version") != 1:
    raise ValueError("src/hvac.json requires schema_version = 1.")
  if set(document) != {
    "schema_version",
    "equipment",
    "installation_order",
    "clearance_m",
    "conditions",
    "requirements",
  }:
    raise ValueError("src/hvac.json contains missing or unknown top-level fields.")

  raw_equipment = document["equipment"]
  if not isinstance(raw_equipment, dict) or not raw_equipment:
    raise ValueError("equipment must be a non-empty object keyed by SDL instance identity.")
  families = _families()
  equipment: list[Equipment] = []
  for identity in sorted(raw_equipment):
    item = raw_equipment[identity]
    if not isinstance(item, dict) or set(item) != {"family", "model", "origin", "size"}:
      raise ValueError(f"Equipment {identity} has missing or unknown fields.")
    family = item["family"]
    model = item["model"]
    if family not in families or model not in families[family]["models"]:
      raise ValueError(f"Unsupported equipment family or model: {identity} ({family}/{model}).")
    performance = families[family]["models"][model]
    equipment.append(
      Equipment(
        identity=identity,
        family=family,
        model=model,
        capacity_kw=float(performance["capacity_kw"]),
        heat_rejection_kw=float(performance["heat_rejection_kw"]),
        origin=_vector(item["origin"], f"equipment.{identity}.origin"),
        size=_vector(item["size"], f"equipment.{identity}.size", positive=True),
      )
    )

  order = document["installation_order"]
  identities = {item.identity for item in equipment}
  if (
    not isinstance(order, list)
    or not all(isinstance(item, str) for item in order)
    or len(order) != len(set(order))
    or set(order) != identities
  ):
    raise ValueError("installation_order must contain every equipment identity exactly once.")

  sdl_path = root / "src" / "system.sdl.json"
  try:
    sdl = json.loads(sdl_path.read_text(encoding="utf-8"))
    instances = set(sdl["components"][sdl["root"]]["instances"])
  except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
    raise ValueError("Cannot resolve HVAC identities from src/system.sdl.json.") from error
  if instances != identities:
    raise ValueError(
      "HVAC equipment identities must exactly match SDL root instances; "
      f"missing={sorted(instances - identities)}, unknown={sorted(identities - instances)}."
    )

  conditions = _mapping(
    document["conditions"],
    {
      "cooling_load_kw",
      "ambient_temperature_c",
      "relative_humidity",
      "surface_temperature_c",
      "ventilation_m3_s",
      "air_density_kg_m3",
      "air_cp_kj_kgk",
    },
    "conditions",
  )
  if not 0 < conditions["relative_humidity"] <= 1:
    raise ValueError("conditions.relative_humidity must be in (0, 1].")
  for name in ("cooling_load_kw", "ventilation_m3_s", "air_density_kg_m3", "air_cp_kj_kgk"):
    if conditions[name] <= 0:
      raise ValueError(f"conditions.{name} must be positive.")
  requirements = _mapping(
    document["requirements"],
    {"min_capacity_margin_kw", "max_room_temperature_c", "min_dew_point_margin_c"},
    "requirements",
  )
  clearance = _number(document["clearance_m"], "clearance_m")
  if clearance < 0:
    raise ValueError("clearance_m cannot be negative.")
  return HvacSystem(
    tuple(equipment), tuple(order), clearance, conditions, requirements
  )
