from dataclasses import dataclass
from typing import Protocol

from aling.core.permissions import PermissionContext


@dataclass(frozen=True)
class MemeResult:
    resource_id: str
    description: str


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str


class MemeProvider(Protocol):
    """Return opaque validated resource IDs; never let a user choose a file path."""

    async def search(self, query: str, *, limit: int = 3) -> list[MemeResult]: ...


class WebParser(Protocol):
    async def parse(self, url: str) -> WebPage: ...


class GroupAction(Protocol):
    """Implementations MUST call require_group_admin using a trusted event context."""

    async def execute(self, context: PermissionContext) -> str: ...
