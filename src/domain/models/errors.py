"""Typed error values returned across ports (paired with `Result`)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CadErrorKind(StrEnum):
    CONTRACT = "contract"  # code did not bind `part`
    EXECUTION = "execution"  # code raised
    TIMEOUT = "timeout"  # exceeded wall-clock budget
    EMPTY = "empty"  # produced no/zero-volume solid
    EXPORT = "export"  # failed to write STEP/STL
    INTERNAL = "internal"  # sandbox harness failure


@dataclass(frozen=True, slots=True)
class CadError:
    kind: CadErrorKind
    message: str
    traceback: str = ""

    def as_feedback(self) -> str:
        """Human/model-readable feedback used to drive self-correction."""
        body = self.message if not self.traceback else f"{self.message}\n\n{self.traceback}"
        return f"[{self.kind.value}] {body}"
