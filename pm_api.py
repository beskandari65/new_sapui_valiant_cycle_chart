"""Flask endpoints for the Project Management database module."""

from __future__ import annotations

import sqlite3
from typing import Callable

from flask import Blueprint, jsonify, request

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

