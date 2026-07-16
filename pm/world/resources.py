"""Resource abstractions attached to a simulation run.

Mirrors fleet-sdk's resource concept: a `Resource` wraps a small Pydantic
`ResourceModel` describing its identity (``name``/``type``/``mode``) and exposes a
``uri`` of the form ``type://name``. `RESOURCE_TYPES` is the type→class registry —
the extension point for future resource kinds.

`WorldResource` is the one concrete resource: it wraps a
:class:`~pm.db.store.Store` and exposes read access via `query` (a fleet-style
`QueryResult` with ``.rows``/``.columns``) plus the underlying `store` for typed
reads. Mutations do not happen here — they go through the engine/tools so the
sync/async boundary and single-writer rule hold.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel

from pm.db.store import Store


class ResourceType(str, Enum):
    world = "world"


class ResourceMode(str, Enum):
    rw = "rw"
    ro = "ro"


class ResourceModel(BaseModel):
    name: str
    type: ResourceType
    mode: ResourceMode = ResourceMode.rw


class Resource(ABC):
    def __init__(self, resource: ResourceModel) -> None:
        self.resource = resource

    @property
    def uri(self) -> str:
        return f"{self.resource.type.value}://{self.resource.name}"

    @property
    def name(self) -> str:
        return self.resource.name

    @property
    def type(self) -> ResourceType:
        return self.resource.type

    @property
    def mode(self) -> ResourceMode:
        return self.resource.mode

    def __repr__(self) -> str:
        return f"Resource(uri={self.uri}, mode={self.mode.value})"


@dataclass
class QueryResult:
    """Result of an ad-hoc SQL query (mirrors fleet's ``.rows`` access)."""

    columns: list[str]
    rows: list[tuple[Any, ...]]


class WorldResource(Resource):
    def __init__(
        self,
        store: Store,
        name: str = "current",
        mode: ResourceMode = ResourceMode.rw,
    ) -> None:
        super().__init__(ResourceModel(name=name, type=ResourceType.world, mode=mode))
        self.store = store

    def query(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        """Run a read-only SQL query and return rows as plain tuples."""
        cur = self.store.db.conn.execute(sql, params)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [tuple(r) for r in cur.fetchall()]
        return QueryResult(columns=columns, rows=rows)

    def close(self) -> None:
        self.store.close()


# Type → concrete-class registry (mirrors fleet's RESOURCE_TYPES dict).
RESOURCE_TYPES: dict[ResourceType, type[Resource]] = {ResourceType.world: WorldResource}
