"""SQLite persistence for the Project Management (PM) domain.

This module is intentionally independent from PyQt and from the Cycle Chart
tables.  Both applications may live in the same SQLite database, but PM tasks
and internal PM resources have different meanings from Cycle Chart items and
product requirements.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, Iterable


DEFAULT_WEEK = {
    0: ("08:00", "17:00"),
    1: ("08:00", "17:00"),
    2: ("08:00", "17:00"),
    3: ("08:00", "17:00"),
    4: ("08:00", "17:00"),
}


class PMValidationError(ValueError):
    """Raised when an API payload cannot be stored safely."""


class PMNotFoundError(LookupError):
    """Raised when a requested PM record does not exist."""


class PMDatabase:
    """Create, migrate, read, and write the PM portion of a SQLite database."""

    def __init__(self, db_path: str):
        if not db_path:
            raise ValueError("db_path is required")
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def read_connection(self):
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def create_or_migrate(self) -> None:
        """Idempotently create the PM schema without changing CC tables."""
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pm_schema_version (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pm_calendars (
                    calendar_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'America/New_York',
                    hours_per_day REAL NOT NULL DEFAULT 8.0
                        CHECK (hours_per_day > 0 AND hours_per_day <= 24),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pm_calendar_weekdays (
                    calendar_id TEXT NOT NULL,
                    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                    is_working INTEGER NOT NULL DEFAULT 1 CHECK (is_working IN (0, 1)),
                    start_time TEXT,
                    end_time TEXT,
                    PRIMARY KEY (calendar_id, weekday),
                    FOREIGN KEY (calendar_id) REFERENCES pm_calendars(calendar_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pm_calendar_exceptions (
                    exception_id TEXT PRIMARY KEY,
                    calendar_id TEXT NOT NULL,
                    exception_date TEXT NOT NULL,
                    is_working INTEGER NOT NULL DEFAULT 0 CHECK (is_working IN (0, 1)),
                    start_time TEXT,
                    end_time TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    UNIQUE (calendar_id, exception_date),
                    FOREIGN KEY (calendar_id) REFERENCES pm_calendars(calendar_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pm_projects (
                    project_id TEXT PRIMARY KEY,
                    project_number TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planning',
                    start_date TEXT,
                    target_end_date TEXT,
                    calendar_id TEXT,
                    cc_project_number TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (calendar_id) REFERENCES pm_calendars(calendar_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS pm_tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    parent_id TEXT,
                    tree_index INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    start_date TEXT,
                    end_date TEXT,
                    duration_value REAL NOT NULL DEFAULT 1
                        CHECK (duration_value >= 0),
                    duration_unit TEXT NOT NULL DEFAULT 'working_days'
                        CHECK (duration_unit IN (
                            'working_days', 'calendar_days', 'hours', 'minutes'
                        )),
                    progress_percent REAL NOT NULL DEFAULT 0
                        CHECK (progress_percent BETWEEN 0 AND 100),
                    task_type TEXT NOT NULL DEFAULT 'task'
                        CHECK (task_type IN ('task', 'summary', 'milestone')),
                    color TEXT,
                    calendar_id TEXT,
                    cc_item_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES pm_projects(project_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES pm_tasks(task_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (calendar_id) REFERENCES pm_calendars(calendar_id)
                        ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pm_tasks_project
                    ON pm_tasks(project_id, parent_id, tree_index);
                CREATE INDEX IF NOT EXISTS idx_pm_tasks_cc_item
                    ON pm_tasks(cc_item_id);

                CREATE TABLE IF NOT EXISTS pm_task_dependencies (
                    predecessor_id TEXT NOT NULL,
                    successor_id TEXT NOT NULL,
                    dependency_type TEXT NOT NULL DEFAULT 'FS'
                        CHECK (dependency_type IN ('FS', 'SS', 'FF', 'SF')),
                    lag_value REAL NOT NULL DEFAULT 0,
                    lag_unit TEXT NOT NULL DEFAULT 'working_days'
                        CHECK (lag_unit IN (
                            'working_days', 'calendar_days', 'hours', 'minutes'
                        )),
                    PRIMARY KEY (predecessor_id, successor_id),
                    CHECK (predecessor_id <> successor_id),
                    FOREIGN KEY (predecessor_id) REFERENCES pm_tasks(task_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (successor_id) REFERENCES pm_tasks(task_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pm_internal_resources (
                    resource_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    resource_type TEXT NOT NULL
                        CHECK (resource_type IN (
                            'person', 'team', 'internal_machine',
                            'contractor', 'other'
                        )),
                    capacity REAL NOT NULL DEFAULT 1 CHECK (capacity > 0),
                    calendar_id TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (calendar_id) REFERENCES pm_calendars(calendar_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS pm_task_assignments (
                    task_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    allocation REAL NOT NULL DEFAULT 1 CHECK (allocation > 0),
                    role TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (task_id, resource_id),
                    FOREIGN KEY (task_id) REFERENCES pm_tasks(task_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (resource_id)
                        REFERENCES pm_internal_resources(resource_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_pm_assignments_resource
                    ON pm_task_assignments(resource_id);

                CREATE TABLE IF NOT EXISTS pm_deliverables (
                    deliverable_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planned',
                    quantity REAL NOT NULL DEFAULT 1 CHECK (quantity >= 0),
                    unit TEXT NOT NULL DEFAULT 'each',
                    cc_item_id TEXT,
                    cc_requirement_type TEXT,
                    due_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES pm_projects(project_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_pm_deliverables_project
                    ON pm_deliverables(project_id);
                """
            )
            current = conn.execute(
                "SELECT version FROM pm_schema_version LIMIT 1"
            ).fetchone()
            if current is None:
                conn.execute("INSERT INTO pm_schema_version(version) VALUES (1)")
            self._ensure_default_calendar(conn)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _validate_iso_date(value: Any, field: str) -> str | None:
        if value in (None, ""):
            return None
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise PMValidationError(f"{field} must use YYYY-MM-DD") from exc

    def _ensure_default_calendar(self, conn: sqlite3.Connection) -> None:
        now = self._now()
        conn.execute(
            """
            INSERT OR IGNORE INTO pm_calendars
                (calendar_id, name, timezone, hours_per_day, created_at, updated_at)
            VALUES ('pm_default', 'Standard Monday-Friday', 'America/New_York',
                    8.0, ?, ?)
            """,
            (now, now),
        )
        for weekday in range(7):
            times = DEFAULT_WEEK.get(weekday)
            conn.execute(
                """
                INSERT OR IGNORE INTO pm_calendar_weekdays
                    (calendar_id, weekday, is_working, start_time, end_time)
                VALUES ('pm_default', ?, ?, ?, ?)
                """,
                (weekday, int(times is not None),
                 times[0] if times else None, times[1] if times else None),
            )

    @staticmethod
    def _rows(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def list_projects(self) -> list[dict[str, Any]]:
        self.create_or_migrate()
        with self.read_connection() as conn:
            return self._rows(
                conn.execute(
                    "SELECT * FROM pm_projects ORDER BY updated_at DESC"
                ).fetchall()
            )

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.create_or_migrate()
        number = str(payload.get("project_number") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not number:
            raise PMValidationError("project_number is required")
        if not title:
            raise PMValidationError("title is required")
        project_id = str(payload.get("project_id") or self._id("pmproj"))
        now = self._now()
        values = (
            project_id,
            number,
            title,
            str(payload.get("description") or ""),
            str(payload.get("status") or "planning"),
            self._validate_iso_date(payload.get("start_date"), "start_date"),
            self._validate_iso_date(
                payload.get("target_end_date"), "target_end_date"
            ),
            str(payload.get("calendar_id") or "pm_default"),
            payload.get("cc_project_number"),
            now,
            now,
        )
        try:
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO pm_projects (
                        project_id, project_number, title, description, status,
                        start_date, target_end_date, calendar_id,
                        cc_project_number, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise PMValidationError(
                "project_number must be unique and calendar_id must exist"
            ) from exc
        return self.get_chart(project_id)["project"]

    def get_chart(self, project_id: str) -> dict[str, Any]:
        self.create_or_migrate()
        with self.read_connection() as conn:
            project = conn.execute(
                "SELECT * FROM pm_projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise PMNotFoundError(f"PM project {project_id!r} was not found")
            tasks = self._rows(
                conn.execute(
                    """
                    SELECT * FROM pm_tasks
                    WHERE project_id = ?
                    ORDER BY parent_id, tree_index, task_id
                    """,
                    (project_id,),
                ).fetchall()
            )
            task_ids = [task["task_id"] for task in tasks]
            dependencies: list[dict[str, Any]] = []
            assignments: list[dict[str, Any]] = []
            if task_ids:
                marks = ",".join("?" for _ in task_ids)
                dependencies = self._rows(
                    conn.execute(
                        f"""
                        SELECT * FROM pm_task_dependencies
                        WHERE predecessor_id IN ({marks})
                           OR successor_id IN ({marks})
                        """,
                        (*task_ids, *task_ids),
                    ).fetchall()
                )
                assignments = self._rows(
                    conn.execute(
                        f"""
                        SELECT * FROM pm_task_assignments
                        WHERE task_id IN ({marks})
                        """,
                        task_ids,
                    ).fetchall()
                )
            deliverables = self._rows(
                conn.execute(
                    """
                    SELECT * FROM pm_deliverables
                    WHERE project_id = ? ORDER BY due_date, title
                    """,
                    (project_id,),
                ).fetchall()
            )
            return {
                "mode": "project_management",
                "project": dict(project),
                "tasks": tasks,
                "dependencies": dependencies,
                "assignments": assignments,
                "deliverables": deliverables,
                "ui": {
                    "itemTitleField": "title",
                    "showStep": False,
                    "durationLabel": "Task Duration",
                    "defaultDurationUnit": "working_days",
                },
            }

    def replace_chart(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Atomically replace a PM chart aggregate.

        Existing task IDs supplied by the client remain stable. Missing task,
        dependency, assignment, and deliverable records are removed.
        """
        self.create_or_migrate()
        project = payload.get("project") or {}
        tasks = payload.get("tasks") or []
        dependencies = payload.get("dependencies") or []
        assignments = payload.get("assignments") or []
        deliverables = payload.get("deliverables") or []
        if not isinstance(tasks, list):
            raise PMValidationError("tasks must be an array")

        now = self._now()
        normalized_tasks: list[dict[str, Any]] = []
        task_ids: set[str] = set()
        for index, raw in enumerate(tasks):
            if not isinstance(raw, dict):
                raise PMValidationError("each task must be an object")
            task_id = str(raw.get("task_id") or self._id("pmtask"))
            if task_id in task_ids:
                raise PMValidationError(f"duplicate task_id: {task_id}")
            title = str(raw.get("title") or "").strip()
            if not title:
                raise PMValidationError(f"task {task_id} requires title")
            task_ids.add(task_id)
            normalized_tasks.append(
                {
                    **raw,
                    "task_id": task_id,
                    "title": title,
                    "tree_index": int(raw.get("tree_index", index)),
                }
            )
        for task in normalized_tasks:
            parent_id = task.get("parent_id") or None
            if parent_id and parent_id not in task_ids:
                raise PMValidationError(
                    f"parent task {parent_id!r} is not in this project"
                )

        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT project_id FROM pm_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if existing is None:
                raise PMNotFoundError(f"PM project {project_id!r} was not found")

            editable = {
                "title": project.get("title"),
                "description": project.get("description"),
                "status": project.get("status"),
                "start_date": self._validate_iso_date(
                    project.get("start_date"), "start_date"
                ),
                "target_end_date": self._validate_iso_date(
                    project.get("target_end_date"), "target_end_date"
                ),
                "calendar_id": project.get("calendar_id"),
                "cc_project_number": project.get("cc_project_number"),
            }
            updates = {key: value for key, value in editable.items()
                       if value is not None}
            if updates:
                updates["updated_at"] = now
                clause = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(
                    f"UPDATE pm_projects SET {clause} WHERE project_id = ?",
                    (*updates.values(), project_id),
                )

            conn.execute(
                "DELETE FROM pm_tasks WHERE project_id = ?", (project_id,)
            )
            conn.execute(
                "DELETE FROM pm_deliverables WHERE project_id = ?", (project_id,)
            )
            pending = list(normalized_tasks)
            inserted: set[str] = set()
            while pending:
                progressed = False
                for task in pending[:]:
                    parent_id = task.get("parent_id") or None
                    if parent_id and parent_id not in inserted:
                        continue
                    conn.execute(
                        """
                        INSERT INTO pm_tasks (
                            task_id, project_id, parent_id, tree_index, title,
                            description, start_date, end_date, duration_value,
                            duration_unit, progress_percent, task_type, color,
                            calendar_id, cc_item_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task["task_id"], project_id, parent_id,
                            task["tree_index"], task["title"],
                            str(task.get("description") or ""),
                            self._validate_iso_date(
                                task.get("start_date"), "task.start_date"
                            ),
                            self._validate_iso_date(
                                task.get("end_date"), "task.end_date"
                            ),
                            float(task.get("duration_value", 1)),
                            str(task.get("duration_unit") or "working_days"),
                            float(task.get("progress_percent", 0)),
                            str(task.get("task_type") or "task"),
                            task.get("color"), task.get("calendar_id"),
                            task.get("cc_item_id"), now, now,
                        ),
                    )
                    inserted.add(task["task_id"])
                    pending.remove(task)
                    progressed = True
                if not progressed:
                    raise PMValidationError("task hierarchy contains a cycle")

            for dep in dependencies:
                predecessor = str(dep.get("predecessor_id") or "")
                successor = str(dep.get("successor_id") or "")
                if predecessor not in task_ids or successor not in task_ids:
                    raise PMValidationError(
                        "dependency tasks must belong to this project"
                    )
                conn.execute(
                    """
                    INSERT INTO pm_task_dependencies (
                        predecessor_id, successor_id, dependency_type,
                        lag_value, lag_unit
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        predecessor, successor,
                        str(dep.get("dependency_type") or "FS"),
                        float(dep.get("lag_value", 0)),
                        str(dep.get("lag_unit") or "working_days"),
                    ),
                )

            for assignment in assignments:
                task_id = str(assignment.get("task_id") or "")
                if task_id not in task_ids:
                    raise PMValidationError(
                        "assignment task must belong to this project"
                    )
                conn.execute(
                    """
                    INSERT INTO pm_task_assignments (
                        task_id, resource_id, allocation, role
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        task_id, assignment.get("resource_id"),
                        float(assignment.get("allocation", 1)),
                        str(assignment.get("role") or ""),
                    ),
                )

            for raw in deliverables:
                deliverable_title = str(raw.get("title") or "").strip()
                if not deliverable_title:
                    raise PMValidationError("each deliverable requires title")
                conn.execute(
                    """
                    INSERT INTO pm_deliverables (
                        deliverable_id, project_id, title, description, status,
                        quantity, unit, cc_item_id, cc_requirement_type,
                        due_date, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(raw.get("deliverable_id")
                            or self._id("pmdeliverable")),
                        project_id, deliverable_title,
                        str(raw.get("description") or ""),
                        str(raw.get("status") or "planned"),
                        float(raw.get("quantity", 1)),
                        str(raw.get("unit") or "each"),
                        raw.get("cc_item_id"),
                        raw.get("cc_requirement_type"),
                        self._validate_iso_date(
                            raw.get("due_date"), "deliverable.due_date"
                        ),
                        now, now,
                    ),
                )
        return self.get_chart(project_id)

    def list_resources(self) -> list[dict[str, Any]]:
        self.create_or_migrate()
        with self.read_connection() as conn:
            return self._rows(
                conn.execute(
                    """
                    SELECT * FROM pm_internal_resources
                    ORDER BY is_active DESC, name
                    """
                ).fetchall()
            )

    def save_resource(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.create_or_migrate()
        name = str(payload.get("name") or "").strip()
        if not name:
            raise PMValidationError("resource name is required")
        resource_id = str(payload.get("resource_id") or self._id("pmresource"))
        now = self._now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO pm_internal_resources (
                    resource_id, name, resource_type, capacity, calendar_id,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id) DO UPDATE SET
                    name = excluded.name,
                    resource_type = excluded.resource_type,
                    capacity = excluded.capacity,
                    calendar_id = excluded.calendar_id,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
                """,
                (
                    resource_id, name,
                    str(payload.get("resource_type") or "person"),
                    float(payload.get("capacity", 1)),
                    payload.get("calendar_id"),
                    int(bool(payload.get("is_active", True))),
                    now, now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM pm_internal_resources WHERE resource_id = ?
                """,
                (resource_id,),
            ).fetchone()
            return dict(row)

    def resource_conflicts(self, project_id: str) -> list[dict[str, Any]]:
        """Return simple date-range capacity warnings for a PM project.

        This is intentionally a warning engine. It does not prevent saving and
        can later be upgraded to account for partial-day calendar availability.
        """
        chart = self.get_chart(project_id)
        tasks = {task["task_id"]: task for task in chart["tasks"]}
        resources = {r["resource_id"]: r for r in self.list_resources()}
        by_resource: dict[str, list[dict[str, Any]]] = {}
        for assignment in chart["assignments"]:
            task = tasks.get(assignment["task_id"])
            if not task or not task.get("start_date") or not task.get("end_date"):
                continue
            by_resource.setdefault(assignment["resource_id"], []).append(
                {**assignment, **task}
            )

        warnings: list[dict[str, Any]] = []
        for resource_id, allocated_tasks in by_resource.items():
            resource = resources.get(resource_id)
            if not resource:
                continue
            boundaries = sorted(
                {
                    day
                    for task in allocated_tasks
                    for day in (task["start_date"], task["end_date"])
                }
            )
            for day in boundaries:
                active = [
                    task for task in allocated_tasks
                    if task["start_date"] <= day <= task["end_date"]
                ]
                demand = sum(float(task["allocation"]) for task in active)
                if demand > float(resource["capacity"]):
                    warnings.append(
                        {
                            "resource_id": resource_id,
                            "resource_name": resource["name"],
                            "date": day,
                            "capacity": resource["capacity"],
                            "demand": demand,
                            "task_ids": [task["task_id"] for task in active],
                            "severity": "warning",
                        }
                    )
        return warnings
