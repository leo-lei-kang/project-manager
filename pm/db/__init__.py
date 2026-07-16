"""SQLite persistence layer.

`Database` owns the raw connection and pragmas; `Store` is the typed repository
that every other subsystem uses. No SQL leaks past `Store`.
"""

from pm.db.database import Database
from pm.db.store import Store

__all__ = ["Database", "Store"]
