"""Server-owned scenario catalogue and dashboard lifecycle contracts."""

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from boundaryline.contracts import Scenario, StrictModel


class Readiness(StrEnum):
    READY = "ready"
    PLANNED = "planned"
    NOT_READY = "not_ready"


class RunState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class ScenarioCapability(StrEnum):
    AUTONOMOUS_RUN = "autonomous_run"
    DETERMINISTIC_PROBE = "deterministic_probe"
    DIRECT_PATH_TEST = "direct_path_test"
    EVIDENCE_EXPORT = "evidence_export"


class ScenarioDefinition(StrictModel):
    scenario: Scenario
    label: str = Field(min_length=1, max_length=120)
    short_label: str = Field(min_length=1, max_length=48)
    readiness: Readiness
    summary: str = Field(min_length=1, max_length=300)
    readiness_reason: str = Field(min_length=1, max_length=300)
    required_services: tuple[str, ...]
    capabilities: tuple[ScenarioCapability, ...]

    @model_validator(mode="after")
    def unavailable_scenarios_have_no_capabilities(self) -> Self:
        if self.readiness is not Readiness.READY and self.capabilities:
            raise ValueError("only ready scenarios may expose executable capabilities")
        return self


class ScenarioManifest(StrictModel):
    manifest_version: str = Field(pattern=r"^\d+\.\d+$")
    scenarios: tuple[ScenarioDefinition, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def contains_exact_catalogue(self) -> Self:
        scenario_ids = [item.scenario for item in self.scenarios]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("scenario IDs must be unique")
        if set(scenario_ids) != set(Scenario):
            raise ValueError("scenario manifest must define A, B, C and D")
        return self


def load_scenario_manifest(path: Path) -> ScenarioManifest:
    return ScenarioManifest.model_validate_json(path.read_text(encoding="utf-8"))


def scenario_manifest_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
