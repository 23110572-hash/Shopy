"""Integer-paise money types."""

from typing import Any

from pydantic import RootModel, field_validator


class MoneyPaise(RootModel[int]):
    """A non-negative amount represented only as integer paise."""

    @field_validator("root", mode="before")
    @classmethod
    def require_integer_paise(cls, value: Any) -> int:
        if type(value) is not int:
            raise TypeError("Money must be provided as integer paise")
        if value < 0:
            raise ValueError("Money cannot be negative")
        return value

    def __int__(self) -> int:
        return self.root
