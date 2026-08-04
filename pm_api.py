"""Flask endpoints for the Project Management database module."""

from __future__ import annotations

import sqlite3
from typing import Callable

from flask import Blueprint, jsonify, request, send_file

from pm_database import (
    PMDatabase,
    PMNotFoundError,
    PMValidationError,
)


def create_pm_blueprint(
    get_db_path: Callable[[], str | None],
) -> Blueprint:
    blueprint = Blueprint("pm_api", __name__, url_prefix="/api/pm")

    def database() -> PMDatabase:
        db_path = get_db_path()
        if not db_path:
            raise RuntimeError(
                "Project Management endpoints require DB_PATH to be configured"
            )
        db = PMDatabase(db_path)
        db.create_or_migrate()
        return db

    @blueprint.errorhandler(PMValidationError)
    def validation_error(exc):
        return jsonify({"status": "error", "error": str(exc)}), 400

    @blueprint.errorhandler(PMNotFoundError)
    def not_found(exc):
        return jsonify({"status": "error", "error": str(exc)}), 404

    @blueprint.errorhandler(sqlite3.IntegrityError)
    def integrity_error(exc):
        return jsonify({
            "status": "error",
            "error": "The request violates a PM database relationship",
            "detail": str(exc),
        }), 409

    @blueprint.errorhandler(RuntimeError)
    def configuration_error(exc):
        return jsonify({"status": "error", "error": str(exc)}), 503

    @blueprint.get("/projects")
    def list_projects():
        return jsonify(database().list_projects())

    @blueprint.post("/projects")
    def create_project():
        project = database().create_project(request.get_json(silent=True) or {})
        return jsonify(project), 201

    @blueprint.get("/projects/<project_id>/chart")
    def get_project_chart(project_id):
        return jsonify(database().get_chart(project_id))

    @blueprint.put("/projects/<project_id>/chart")
    def replace_project_chart(project_id):
        payload = request.get_json(silent=True) or {}
        return jsonify(database().replace_chart(project_id, payload))

    @blueprint.get("/projects/<project_id>/resource-conflicts")
    def get_resource_conflicts(project_id):
        warnings = database().resource_conflicts(project_id)
        return jsonify({
            "project_id": project_id,
            "blocking": False,
            "warnings": warnings,
        })

    @blueprint.get("/projects/<project_id>/activity")
    def get_project_activity(project_id):
        return jsonify(database().list_activity(project_id))

    @blueprint.get("/projects/<project_id>/issues")
    def get_project_issues(project_id):
        task_id = request.args.get("task_id")
        open_only = request.args.get("open_only", "").lower() in {"1", "true", "yes"}
        return jsonify(database().list_issues(project_id, task_id, open_only))

    @blueprint.post("/projects/<project_id>/issues")
    def create_project_issue(project_id):
        issue = database().save_issue(
            project_id, request.get_json(silent=True) or {}
        )
        return jsonify(issue), 201

    @blueprint.put("/projects/<project_id>/issues/<issue_id>")
    def update_project_issue(project_id, issue_id):
        return jsonify(database().save_issue(
            project_id, request.get_json(silent=True) or {}, issue_id
        ))

    @blueprint.post("/projects/<project_id>/report")
    def generate_project_report(project_id):
        from pm_report import build_project_report

        payload = request.get_json(silent=True) or {}
        db = database()
        chart = db.get_chart(project_id)
        resources = {
            resource["resource_id"]: resource
            for resource in db.list_resources()
        }
        for assignment in chart.get("assignments") or []:
            resource = resources.get(assignment.get("resource_id"))
            assignment["resource_name"] = (
                resource.get("name") if resource else assignment.get("resource_id")
            )
        scope = str(payload.get("scope") or "project")
        task_id = str(payload.get("task_id") or "")
        if scope == "task" and not any(
            task.get("task_id") == task_id for task in chart.get("tasks") or []
        ):
            raise PMValidationError("select a valid task for a task report")
        report = build_project_report(
            chart,
            db.list_issues(project_id),
            db.list_activity(project_id),
            payload,
        )
        project_number = str(chart["project"].get("project_number") or "project")
        suffix = "task_report" if scope == "task" else "project_report"
        safe_number = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in project_number
        )
        return send_file(
            report,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{safe_number}_{suffix}.pdf",
        )

    @blueprint.get("/resources")
    def list_resources():
        return jsonify(database().list_resources())

    @blueprint.post("/resources")
    def save_resource():
        resource = database().save_resource(
            request.get_json(silent=True) or {}
        )
        return jsonify(resource), 201

    @blueprint.put("/resources/<resource_id>")
    def update_resource(resource_id):
        payload = request.get_json(silent=True) or {}
        payload["resource_id"] = resource_id
        return jsonify(database().save_resource(payload))

    return blueprint

