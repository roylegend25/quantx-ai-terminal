from enum import StrEnum
from typing import Protocol

class DecisionEngineType(StrEnum):
    ACTIVE_DRIVE_V1 = "active_drive_v1"
    ACTIVE_DRIVE_V2 = "active_drive_v2"

class DecisionEngine(Protocol):
    name: DecisionEngineType
    version: str
    def evaluate(self, context: dict) -> dict: ...
    def health(self) -> dict: ...
    def capabilities(self) -> list[str]: ...
