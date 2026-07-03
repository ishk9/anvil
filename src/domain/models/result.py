"""A small `Result[T, E]` type for modelling expected failures explicitly.

Used across port boundaries so callers must handle failure (bad CAD code, timeouts)
without try/except control flow. Unexpected programmer errors should still raise.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, NoReturn, TypeVar

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, _default: T) -> T:
        return self.value

    def map(self, fn: Callable[[T], U]) -> Result[U, E]:
        return Ok(fn(self.value))


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> NoReturn:
        raise ValueError(f"Called unwrap() on an Err: {self.error!r}")

    def unwrap_or(self, default: T) -> T:
        return default

    def map(self, _fn: Callable[[T], U]) -> Result[U, E]:
        return self


Result = Ok[T] | Err[E]
