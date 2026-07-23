from enum import StrEnum
from typing import Protocol

class DecisionEngineType(StrEnum):
    # Removed from Premium X Dark - the legacy ensemble-based engine now
    # lives only in the standalone QuantX Classic repository. Kept as an
    # enum member (with no corresponding registered engine) purely so a
    # historical UserBotSetting/ActiveDriveDecision row still parses instead
    # of raising - it can never be selected or become the active engine.
    ACTIVE_DRIVE_V1 = "active_drive_v1"
    ACTIVE_DRIVE_V2 = "active_drive_v2"

class DecisionEngine(Protocol):
    name: DecisionEngineType
    version: str
    def evaluate(self, context: dict) -> dict: ...
    def health(self) -> dict: ...
    def capabilities(self) -> list[str]: ...
