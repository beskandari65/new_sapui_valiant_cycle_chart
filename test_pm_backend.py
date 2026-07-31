import os
import tempfile
import unittest

from flask import Flask

from pm_api import create_pm_blueprint
from pm_database import PMDatabase


class PMBackendTests(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        app = Flask(__name__)
        app.register_blueprint(create_pm_blueprint(lambda: self.db_path))
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.remove(self.db_path)

    def test_schema_does_not_create_cycle_chart_tables(self):
        db = PMDatabase(self.db_path)
        db.create_or_migrate()
        with db.read_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("pm_projects", tables)
        self.assertNotIn("cycle_general_structure", tables)

    def test_project_chart_round_trip_and_conflict_warning(self):
        created = self.client.post(
            "/api/pm/projects",
            json={"project_number": "P-100", "title": "Build customer line"},
        )
        self.assertEqual(created.status_code, 201)
        project_id = created.get_json()["project_id"]

        resource = self.client.post(
            "/api/pm/resources",
            json={
                "name": "Controls Engineering",
                "resource_type": "team",
                "capacity": 1,
            },
        ).get_json()

        payload = {
            "project": {"status": "planning"},
            "tasks": [
                {
                    "task_id": "task_a",
                    "title": "Design controls",
                    "start_date": "2026-08-03",
                    "end_date": "2026-08-05",
                    "duration_value": 3,
                },
                {
                    "task_id": "task_b",
                    "title": "Review controls",
                    "start_date": "2026-08-04",
                    "end_date": "2026-08-06",
                    "duration_value": 3,
                },
            ],
            "dependencies": [],
            "assignments": [
                {
                    "task_id": "task_a",
                    "resource_id": resource["resource_id"],
                    "allocation": 1,
                },
                {
                    "task_id": "task_b",
                    "resource_id": resource["resource_id"],
                    "allocation": 1,
                },
            ],
            "deliverables": [
                {
                    "title": "Robot installed",
                    "cc_item_id": "customer_robot_requirement_1",
                    "cc_requirement_type": "robot",
                }
            ],
        }
        saved = self.client.put(
            f"/api/pm/projects/{project_id}/chart", json=payload
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        chart = saved.get_json()
        self.assertEqual(chart["mode"], "project_management")
        self.assertFalse(chart["ui"]["showStep"])
        self.assertEqual(chart["ui"]["durationLabel"], "Task Duration")
        self.assertEqual(len(chart["tasks"]), 2)
        self.assertEqual(len(chart["deliverables"]), 1)

        warnings = self.client.get(
            f"/api/pm/projects/{project_id}/resource-conflicts"
        ).get_json()["warnings"]
        self.assertTrue(warnings)
        self.assertEqual(warnings[0]["severity"], "warning")


if __name__ == "__main__":
    unittest.main()
