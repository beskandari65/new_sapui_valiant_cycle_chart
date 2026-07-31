# Project Management backend integration

The PM domain uses its own `pm_*` tables in the same SQLite database as Cycle
Chart. It does not modify or share the Cycle Chart task/resource tables.

## Files

- `pm_database.py`: idempotent schema creation, migrations, aggregate storage,
  internal resources, and resource-capacity warnings.
- `pm_api.py`: Flask blueprint mounted at `/api/pm`.

## Combined standalone server

This project now includes `fast_api_server.py`. It mounts all existing Cycle
Chart compatibility endpoints and the PM endpoints under one FastAPI process.

Run it from this directory:

```powershell
.\run_fastapi.ps1
```

Then open:

```text
http://localhost:8000
```

The local `vccm.db` is copied from the previous FastAPI folder. Override it
when needed by setting `DB_PATH` before starting the server.

## Flask blueprint registration

The compatibility server registers the PM endpoints with:

```python
from pm_api import create_pm_blueprint

app.register_blueprint(create_pm_blueprint(lambda: DB_PATH))
```

`DB_PATH` must point to the existing SQLite database. The PM tables are created
on the first PM request.

## Main chart workflow

Create a project:

```http
POST /api/pm/projects
Content-Type: application/json

{
  "project_number": "PM-1371",
  "title": "Build customer production line",
  "cc_project_number": "1371",
  "start_date": "2026-08-03"
}
```

Load the complete PM chart:

```http
GET /api/pm/projects/{project_id}/chart
```

Atomically save the complete PM chart:

```http
PUT /api/pm/projects/{project_id}/chart
Content-Type: application/json

{
  "project": {
    "status": "planning",
    "start_date": "2026-08-03"
  },
  "tasks": [
    {
      "task_id": "design_robot",
      "parent_id": null,
      "tree_index": 0,
      "title": "Specify welding robot",
      "start_date": "2026-08-03",
      "end_date": "2026-08-05",
      "duration_value": 3,
      "duration_unit": "working_days",
      "progress_percent": 0,
      "cc_item_id": "customer_robot_requirement_1"
    }
  ],
  "dependencies": [],
  "assignments": [],
  "deliverables": [
    {
      "title": "Welding robot installed",
      "quantity": 1,
      "unit": "each",
      "cc_item_id": "customer_robot_requirement_1",
      "cc_requirement_type": "robot"
    }
  ]
}
```

The response contains UI configuration for the shared chart engine:

```json
{
  "mode": "project_management",
  "ui": {
    "itemTitleField": "title",
    "showStep": false,
    "durationLabel": "Task Duration",
    "defaultDurationUnit": "working_days"
  }
}
```

## Internal resources

```http
GET  /api/pm/resources
POST /api/pm/resources
PUT  /api/pm/resources/{resource_id}
```

Example:

```json
{
  "name": "Controls Engineering",
  "resource_type": "team",
  "capacity": 2
}
```

Resource types are `person`, `team`, `internal_machine`, `contractor`, and
`other`. These represent resources used to execute the internal project, not
machines or materials delivered to the customer.

Capacity warnings are available at:

```http
GET /api/pm/projects/{project_id}/resource-conflicts
```

Warnings do not block saving. The initial implementation compares overlapping
dated tasks and their assignment allocations against resource capacity.

## Calendars

The schema creates a `pm_default` calendar:

- Monday through Friday
- 08:00–17:00
- 8 working hours per day
- `America/New_York` timezone

Calendar weekday and exception tables are included so holidays, shutdowns, and
special working days can be managed by future endpoints.
