"""Compatibility adapter for HVAC-validated external BOM snapshots."""

from texsys_bom.snapshots import ExternalProjection, project_external as project_neutral_external
from texsys_hvac_bom.rules import validate_external_item


def project_external(*args, **kwargs) -> ExternalProjection:
  return project_neutral_external(*args, item_validator=validate_external_item, **kwargs)


__all__ = ["ExternalProjection", "project_external"]
