"""Catalog entries for integrations that are designed but not built yet, so
the dashboard can show what's coming, what each will ask for - and, via
network discovery, that the device is already there waiting."""
from __future__ import annotations

from typing import Any

from .base import Field, PlannedIntegration


class RoborockPlanned(PlannedIntegration):
    type = "roborock"
    name = "Roborock"
    category = "cleaning"
    description = "Uses your local Roborock server so the vacuum never talks to the cloud."
    fields = [Field("host", "Local server address")]


class MatterPlanned(PlannedIntegration):
    type = "matter"
    name = "Matter"
    category = "other"
    description = "The cross-brand standard most new plugs, sensors and lights speak."
    fields = []
