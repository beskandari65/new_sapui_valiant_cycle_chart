"""
ValiantTMS Cycle Chart - Demo Flask Server
Serves sample cycle chart data from an embedded dataset.
To use a real SQLite DB, set DB_PATH and TABLE_NAME below.
"""

try:
    from flask import Flask, jsonify, send_file, request
except ImportError:
    print("Flask not installed. Run:  pip install flask")
    raise SystemExit(1)

import os
import sqlite3

app = Flask(__name__)

# ── Real DB configuration (optional) ──────────────────────────────────────────
# Set DB_PATH to your .db or .sqlite file to use a real database.
# Leave as None to use the embedded sample data below.
DB_PATH    = None   # e.g. r"C:\path\to\your\chart.db"
TABLE_NAME = "cycle_general_structure"   # actual table name from the PyQt6 app

# ── Sample data ───────────────────────────────────────────────────────────────
SAMPLE_DATA = [
    # ── Op200 : Station A ────────────────────────────────────────────────────
    {
        "id": 1, "item_id": "s200a", "parent_id": "", "op_number": "Op200",
        "title": "Station OP200-A", "cycle_start": 0.0, "cycle_end": 200.0, "cycle_time": 200.0,
        "cycle_type": "Station", "color": "#09528A",
        "subprocess": "pg200a1,pg200a2", "tree_index": 0,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 2, "item_id": "pg200a1", "parent_id": "s200a", "op_number": "Op200",
        "title": "Process Group 1 — Loading", "cycle_start": 5.0, "cycle_end": 80.0, "cycle_time": 75.0,
        "cycle_type": "Process group", "color": "#1A7FC4",
        "subprocess": "st200a1,st200a2", "tree_index": 0,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 3, "item_id": "st200a1", "parent_id": "pg200a1", "op_number": "Op200",
        "title": "Load Part A", "cycle_start": 5.0, "cycle_end": 35.0, "cycle_time": 30.0,
        "cycle_type": "Undefined", "color": "#4BA9E0",
        "subprocess": "", "tree_index": 0,
        "step": "1", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 4, "item_id": "st200a2", "parent_id": "pg200a1", "op_number": "Op200",
        "title": "Clamp & Verify", "cycle_start": 35.0, "cycle_end": 80.0, "cycle_time": 45.0,
        "cycle_type": "Undefined", "color": "#4BA9E0",
        "subprocess": "", "tree_index": 1,
        "step": "2", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 5, "item_id": "pg200a2", "parent_id": "s200a", "op_number": "Op200",
        "title": "Process Group 2 — Welding", "cycle_start": 80.0, "cycle_end": 200.0, "cycle_time": 120.0,
        "cycle_type": "Process group", "color": "#E87722",
        "subprocess": "st200a3,st200a4,st200a5", "tree_index": 1,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 6, "item_id": "st200a3", "parent_id": "pg200a2", "op_number": "Op200",
        "title": "Robot Weld Pass 1", "cycle_start": 80.0, "cycle_end": 130.0, "cycle_time": 50.0,
        "cycle_type": "Undefined", "color": "#E87722",
        "subprocess": "", "tree_index": 0,
        "step": "3", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 7, "item_id": "st200a4", "parent_id": "pg200a2", "op_number": "Op200",
        "title": "Robot Weld Pass 2", "cycle_start": 130.0, "cycle_end": 170.0, "cycle_time": 40.0,
        "cycle_type": "Undefined", "color": "#E87722",
        "subprocess": "", "tree_index": 1,
        "step": "4", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 8, "item_id": "st200a5", "parent_id": "pg200a2", "op_number": "Op200",
        "title": "Unload & Inspect", "cycle_start": 170.0, "cycle_end": 200.0, "cycle_time": 30.0,
        "cycle_type": "Undefined", "color": "#E87722",
        "subprocess": "", "tree_index": 2,
        "step": "5", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    # ── Op200 : Station B ────────────────────────────────────────────────────
    {
        "id": 9, "item_id": "s200b", "parent_id": "", "op_number": "Op200",
        "title": "Station OP200-B", "cycle_start": 0.0, "cycle_end": 260.0, "cycle_time": 260.0,
        "cycle_type": "Station", "color": "#2B6A9E",
        "subprocess": "st200b1,st200b2,st200b3", "tree_index": 1,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 10, "item_id": "st200b1", "parent_id": "s200b", "op_number": "Op200",
        "title": "Deburr Edge", "cycle_start": 0.0, "cycle_end": 80.0, "cycle_time": 80.0,
        "cycle_type": "Undefined", "color": "#5BA4D0",
        "subprocess": "", "tree_index": 0,
        "step": "1", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 11, "item_id": "st200b2", "parent_id": "s200b", "op_number": "Op200",
        "title": "Press Bearing", "cycle_start": 80.0, "cycle_end": 180.0, "cycle_time": 100.0,
        "cycle_type": "Undefined", "color": "#5BA4D0",
        "subprocess": "", "tree_index": 1,
        "step": "2", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 12, "item_id": "st200b3", "parent_id": "s200b", "op_number": "Op200",
        "title": "Final Torque Check", "cycle_start": 180.0, "cycle_end": 260.0, "cycle_time": 80.0,
        "cycle_type": "Undefined", "color": "#5BA4D0",
        "subprocess": "", "tree_index": 2,
        "step": "3", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    # ── Op300 : Station X ────────────────────────────────────────────────────
    {
        "id": 13, "item_id": "s300x", "parent_id": "", "op_number": "Op300",
        "title": "Station OP300-X", "cycle_start": 0.0, "cycle_end": 89.1, "cycle_time": 89.1,
        "cycle_type": "Station", "color": "#27AE60",
        "subprocess": "st300x1,st300x2,st300x3", "tree_index": 0,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 14, "item_id": "st300x1", "parent_id": "s300x", "op_number": "Op300",
        "title": "Pre-clean Surface", "cycle_start": 0.0, "cycle_end": 30.0, "cycle_time": 30.0,
        "cycle_type": "Undefined", "color": "#52D68A",
        "subprocess": "", "tree_index": 0,
        "step": "1", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 15, "item_id": "st300x2", "parent_id": "s300x", "op_number": "Op300",
        "title": "Apply Sealant", "cycle_start": 30.0, "cycle_end": 60.0, "cycle_time": 30.0,
        "cycle_type": "Undefined", "color": "#52D68A",
        "subprocess": "", "tree_index": 1,
        "step": "2", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 16, "item_id": "st300x3", "parent_id": "s300x", "op_number": "Op300",
        "title": "Cure & Test", "cycle_start": 60.0, "cycle_end": 89.1, "cycle_time": 29.1,
        "cycle_type": "Undefined", "color": "#52D68A",
        "subprocess": "", "tree_index": 2,
        "step": "3", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    # ── Op400 : Station Y (deeper nesting example) ───────────────────────────
    {
        "id": 17, "item_id": "s400y", "parent_id": "", "op_number": "Op400",
        "title": "Station OP400-Y", "cycle_start": 0.0, "cycle_end": 350.0, "cycle_time": 350.0,
        "cycle_type": "Station", "color": "#8E44AD",
        "subprocess": "pg400y1", "tree_index": 0,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 18, "item_id": "pg400y1", "parent_id": "s400y", "op_number": "Op400",
        "title": "Assembly Process", "cycle_start": 10.0, "cycle_end": 350.0, "cycle_time": 340.0,
        "cycle_type": "Process group", "color": "#A569BD",
        "subprocess": "pg400y2,pg400y3", "tree_index": 0,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 19, "item_id": "pg400y2", "parent_id": "pg400y1", "op_number": "Op400",
        "title": "Sub-Assembly A", "cycle_start": 10.0, "cycle_end": 180.0, "cycle_time": 170.0,
        "cycle_type": "Process group", "color": "#C39BD3",
        "subprocess": "st400y1,st400y2", "tree_index": 0,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 20, "item_id": "st400y1", "parent_id": "pg400y2", "op_number": "Op400",
        "title": "Insert Pins", "cycle_start": 10.0, "cycle_end": 90.0, "cycle_time": 80.0,
        "cycle_type": "Undefined", "color": "#D7BDE2",
        "subprocess": "", "tree_index": 0,
        "step": "1", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 21, "item_id": "st400y2", "parent_id": "pg400y2", "op_number": "Op400",
        "title": "Press Fit Housing", "cycle_start": 90.0, "cycle_end": 180.0, "cycle_time": 90.0,
        "cycle_type": "Undefined", "color": "#D7BDE2",
        "subprocess": "", "tree_index": 1,
        "step": "2", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 22, "item_id": "pg400y3", "parent_id": "pg400y1", "op_number": "Op400",
        "title": "Sub-Assembly B", "cycle_start": 180.0, "cycle_end": 350.0, "cycle_time": 170.0,
        "cycle_type": "Process group", "color": "#C39BD3",
        "subprocess": "st400y3,st400y4", "tree_index": 1,
        "step": "", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 23, "item_id": "st400y3", "parent_id": "pg400y3", "op_number": "Op400",
        "title": "Grease Application", "cycle_start": 180.0, "cycle_end": 270.0, "cycle_time": 90.0,
        "cycle_type": "Undefined", "color": "#D7BDE2",
        "subprocess": "", "tree_index": 0,
        "step": "3", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
    {
        "id": 24, "item_id": "st400y4", "parent_id": "pg400y3", "op_number": "Op400",
        "title": "Final Seal & Cap", "cycle_start": 270.0, "cycle_end": 350.0, "cycle_time": 80.0,
        "cycle_type": "Undefined", "color": "#D7BDE2",
        "subprocess": "", "tree_index": 1,
        "step": "4", "highlight": "", "dependant_items": "{}", "run_cond_config": "{}"
    },
]


def get_records(op_number):
    """Return flat list of records for an op_number. Uses real DB if configured."""
    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {TABLE_NAME} WHERE op_number = ?", (op_number,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    return [r for r in SAMPLE_DATA if r["op_number"] == op_number]


def get_op_numbers():
    """Return sorted list of distinct op_number values (sample data + explicitly created ops)."""
    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT op_number FROM {TABLE_NAME} ORDER BY op_number")
        ops = [r[0] for r in cur.fetchall()]
        conn.close()
        return sorted(set(ops) | OP_NUMBERS)
    seen = set()
    result = []
    for r in SAMPLE_DATA:
        op = r["op_number"]
        if op not in seen:
            seen.add(op)
            result.append(op)
    for op in OP_NUMBERS:
        if op not in seen:
            result.append(op)
    return sorted(result)


def build_tree(records):
    """Convert flat list → nested tree using sub_process_items key (matches SAPUI5 frontend)."""
    by_id = {r["item_id"]: dict(r, sub_process_items=[]) for r in records}
    roots = []

    for r in records:
        pid = r.get("parent_id") or ""
        if pid and pid in by_id:
            by_id[pid]["sub_process_items"].append(by_id[r["item_id"]])
        else:
            roots.append(by_id[r["item_id"]])

    def sort_node(node):
        sub = (node.get("subprocess") or "").strip()
        if sub:
            order = [s.strip() for s in sub.split(",") if s.strip()]
            def rank(c):
                try:
                    return order.index(c["item_id"])
                except ValueError:
                    return 9999
            node["sub_process_items"].sort(key=rank)
        else:
            node["sub_process_items"].sort(key=lambda c: c.get("tree_index") or 0)
        for child in node["sub_process_items"]:
            sort_node(child)

    roots.sort(key=lambda r: r.get("tree_index") or 0)
    for root in roots:
        sort_node(root)

    return roots


# ── In-memory metadata (used when no real DB is configured) ───────────────────
JOB_METADATA = {
    "job_number": "", "job_title": "", "job_description": "",
    "target_cycle_time": 60,
    "machine_list": [], "robot_list": [], "operator_list": [],
    "predefined_processes": []
}
OP_METADATA   = {}  # op_number → { op_title, op_description, num_of_parts }
OP_NUMBERS    = set()  # op numbers created via API but not yet in SAMPLE_DATA
ITEMS_DETAILS = {}  # op_number → list of station stat dicts


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("cycle_chart.html")

@app.route("/api/op_numbers")
def api_op_numbers():
    return jsonify(get_op_numbers())

@app.route("/api/tree")
def api_tree():
    op = request.args.get("op_number", "")
    records = get_records(op)
    tree = build_tree(records)
    max_end = max((r.get("cycle_end") or 0.0 for r in records), default=0.0)
    return jsonify({"items": tree, "maxCycleEnd": max_end})


@app.route("/updateDB", methods=["POST"])
def api_update_db():
    data       = request.get_json() or {}
    updated_db = data.get("cycle_general_structure", data.get("updated_db", []))
    op_number  = data.get("op_number", "")

    # Persist job_metadata for this op if provided
    job_meta = data.get("job_metadata")
    if job_meta and op_number:
        OP_METADATA[op_number] = {
            "op_title":       job_meta.get("op_title") or "",
            "op_description": job_meta.get("job_description") or "",
            "num_of_parts":   job_meta.get("num_of_parts", 1),
        }
        OP_NUMBERS.add(op_number)

    # Persist project_metadata (includes machine_list, robot_list, operator_list)
    proj_meta = data.get("project_metadata")
    if proj_meta:
        JOB_METADATA.update(proj_meta)   # always keep in-memory copy current
        if DB_PATH and os.path.exists(DB_PATH):
            proj_no = str(proj_meta.get("project_no") or "default")
            conn = sqlite3.connect(DB_PATH)
            _ensure_project_metadata_table(conn)
            cur = conn.cursor()
            cur.execute(
                "UPDATE project_metadata SET "
                "project_title=?, project_description=?, target_cycle_time=?, "
                "machine_list=?, robot_list=?, operator_list=?, predefined_processes=? "
                "WHERE project_no=?",
                (
                    proj_meta.get("project_title", ""),
                    proj_meta.get("project_description", ""),
                    proj_meta.get("target_cycle_time", 60.0),
                    proj_meta.get("machine_list", ""),
                    proj_meta.get("robot_list", ""),
                    proj_meta.get("operator_list", ""),
                    proj_meta.get("predefined_processes", "{}"),
                    proj_no,
                )
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO project_metadata "
                    "(project_no, project_title, project_description, target_cycle_time, "
                    "machine_list, robot_list, operator_list, predefined_processes) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        proj_no,
                        proj_meta.get("project_title", ""),
                        proj_meta.get("project_description", ""),
                        proj_meta.get("target_cycle_time", 60.0),
                        proj_meta.get("machine_list", ""),
                        proj_meta.get("robot_list", ""),
                        proj_meta.get("operator_list", ""),
                        proj_meta.get("predefined_processes", "{}"),
                    )
                )
            conn.commit()
            conn.close()

    # Flatten nested tree → flat list of DB records
    flat = []
    def flatten(items):
        for item in (items or []):
            flat.append({k: v for k, v in item.items()
                         if k not in ("sub_process_items", "nodes", "_ancestorEnds")})
            flatten(item.get("sub_process_items") or item.get("nodes") or [])
    flatten(updated_db)

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        for record in flat:
            item_id = record.get("item_id")
            if not item_id:
                continue
            cols = [c for c in record if c != "item_id"]
            if not cols:
                continue
            set_clause = ", ".join(c + " = ?" for c in cols)
            values     = [record[c] for c in cols] + [item_id]
            cur.execute(
                "UPDATE " + TABLE_NAME + " SET " + set_clause + " WHERE item_id = ?",
                values
            )
        conn.commit()
        conn.close()
    else:
        # Update in-memory sample data so changes survive the session
        by_id = {r["item_id"]: r for r in SAMPLE_DATA}
        for record in flat:
            item_id = record.get("item_id")
            if item_id and item_id in by_id:
                by_id[item_id].update(record)

    return jsonify({
        "request_title": "updated_db",
        "status":        "success",
        "data":          [],
        "message":       "Updated {} record(s) for {}".format(len(flat), op_number)
    })


@app.route("/updateOpNumbers", methods=["POST"])
def api_update_op_numbers():
    return jsonify({"data": get_op_numbers()})


@app.route("/updateTree", methods=["POST"])
def api_update_tree():
    data    = request.get_json() or {}
    op      = data.get("op_number", "")
    records = get_records(op)
    tree    = build_tree(records)
    max_end = max((r.get("cycle_end") or 0.0 for r in records), default=0.0)

    # items_details for this op
    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _ensure_items_details_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM items_details WHERE op_number = ?", (op,))
        items_details = [dict(r) for r in cur.fetchall()]
        conn.commit()
        conn.close()
    else:
        items_details = list(ITEMS_DETAILS.get(op, []))

    # op metadata row (client reads op_number, op_title, job_description, num_of_parts)
    op_meta = OP_METADATA.get(op, {})
    op_meta_rows = [{
        "op_number":       op,
        "op_title":        op_meta.get("op_title", ""),
        "job_description": op_meta.get("op_description", ""),
        "num_of_parts":    op_meta.get("num_of_parts", 1),
    }] if op else []

    # project_metadata: prefer DB row, fall back to in-memory JOB_METADATA
    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _ensure_project_metadata_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT * FROM project_metadata LIMIT 1")
        proj_row = cur.fetchone()
        conn.commit()
        conn.close()
        project_meta_rows = [dict(proj_row)] if proj_row else [JOB_METADATA]
    else:
        project_meta_rows = [JOB_METADATA]

    return jsonify({
        "data": {
            "cycle_general_structure": tree,
            "items_details":           items_details,
            "op_metadata":             op_meta_rows,
            "project_metadata":        project_meta_rows,
        },
        "maxCycleEnd": max_end,
    })


@app.route("/api/job_metadata", methods=["GET"])
def api_get_job_metadata():
    return jsonify(JOB_METADATA)


@app.route("/api/job_metadata", methods=["POST"])
def api_save_job_metadata():
    data = request.get_json() or {}
    JOB_METADATA.update(data)
    return jsonify({"status": "success"})


@app.route("/api/op_metadata/<op_number>", methods=["GET"])
def api_get_op_metadata(op_number):
    meta = OP_METADATA.get(op_number, {"op_title": "", "op_description": "", "num_of_parts": 1})
    return jsonify(meta)


@app.route("/api/op_metadata/<op_number>", methods=["POST"])
def api_save_op_metadata(op_number):
    data = request.get_json() or {}
    OP_METADATA[op_number] = {
        "op_title":       data.get("op_title", ""),
        "op_description": data.get("op_description", ""),
        "num_of_parts":   data.get("num_of_parts", 1)
    }
    return jsonify({"status": "success"})


@app.route("/api/op/create", methods=["POST"])
def api_create_op():
    data      = request.get_json() or {}
    op_number = data.get("op_number", "")
    if not op_number:
        return jsonify({"status": "error", "message": "op_number is required"}), 400
    if op_number in get_op_numbers():
        return jsonify({"status": "error", "message": f"Op '{op_number}' already exists"}), 409
    OP_NUMBERS.add(op_number)
    OP_METADATA[op_number] = {
        "op_title":       data.get("op_title", ""),
        "op_description": data.get("op_description", ""),
        "num_of_parts":   data.get("num_of_parts", 1)
    }
    return jsonify({"status": "success", "op_number": op_number})


@app.route("/api/op/<op_number>", methods=["DELETE"])
def api_delete_op(op_number):
    global SAMPLE_DATA
    SAMPLE_DATA = [r for r in SAMPLE_DATA if r["op_number"] != op_number]
    OP_NUMBERS.discard(op_number)
    OP_METADATA.pop(op_number, None)
    return jsonify({"status": "success"})


@app.route("/removeProcess", methods=["POST"])
def api_remove_process():
    global SAMPLE_DATA
    data    = request.get_json() or {}
    item_id = data.get("item_id", "")
    if not item_id:
        return jsonify({"status": "error", "message": "item_id is required"}), 400

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        cur.execute(f"DELETE FROM {TABLE_NAME} WHERE item_id = ?", (item_id,))
        conn.commit()
        deleted = cur.rowcount
        conn.close()
    else:
        before = len(SAMPLE_DATA)
        SAMPLE_DATA = [r for r in SAMPLE_DATA if r.get("item_id") != item_id]
        deleted = before - len(SAMPLE_DATA)

    return jsonify({"status": "success", "deleted": deleted})


@app.route("/deleteDBItem", methods=["POST"])
def api_delete_db_item():
    global SAMPLE_DATA
    filters = request.get_json() or {}
    if not filters:
        return jsonify({"status": "error", "message": "filter dict is required"}), 400

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        where = " AND ".join(f"{k} = ?" for k in filters)
        cur.execute(f"DELETE FROM {TABLE_NAME} WHERE {where}", list(filters.values()))
        conn.commit()
        deleted = cur.rowcount
        conn.close()
    else:
        before = len(SAMPLE_DATA)
        SAMPLE_DATA = [
            r for r in SAMPLE_DATA
            if not all(str(r.get(k)) == str(v) for k, v in filters.items())
        ]
        deleted = before - len(SAMPLE_DATA)
        # clean up in-memory op tracking if op_number was the filter
        op = filters.get("op_number")
        if op:
            OP_NUMBERS.discard(str(op))
            OP_METADATA.pop(str(op), None)

    return jsonify({"status": "success", "deleted": deleted})


@app.route("/api/op/duplicate", methods=["POST"])
def api_duplicate_op():
    import uuid
    data      = request.get_json() or {}
    source_op = data.get("source_op", "")
    new_op    = data.get("new_op", "")
    if not source_op or not new_op:
        return jsonify({"status": "error", "message": "source_op and new_op are required"}), 400
    if new_op in get_op_numbers():
        return jsonify({"status": "error", "message": f"Op '{new_op}' already exists"}), 409
    source_records = [r for r in SAMPLE_DATA if r["op_number"] == source_op]
    if not source_records and source_op not in OP_NUMBERS:
        return jsonify({"status": "error", "message": f"Op '{source_op}' not found"}), 404

    id_map  = {r["item_id"]: "cp" + uuid.uuid4().hex[:6] for r in source_records}
    max_id  = max((r["id"] for r in SAMPLE_DATA), default=0)
    new_recs = []
    for i, r in enumerate(source_records):
        nr = dict(r)
        nr["op_number"] = new_op
        nr["item_id"]   = id_map[r["item_id"]]
        nr["parent_id"] = id_map.get(r.get("parent_id", ""), r.get("parent_id", ""))
        if r.get("subprocess"):
            nr["subprocess"] = ",".join(
                id_map.get(s.strip(), s.strip()) for s in r["subprocess"].split(",")
            )
        nr["id"] = max_id + i + 1
        new_recs.append(nr)

    SAMPLE_DATA.extend(new_recs)
    OP_NUMBERS.add(new_op)
    src_meta = OP_METADATA.get(source_op, {})
    OP_METADATA[new_op] = dict(src_meta)
    return jsonify({"status": "success", "op_number": new_op})


_PROJECT_METADATA_SCHEMA = """
    CREATE TABLE IF NOT EXISTS project_metadata (
        project_no           TEXT PRIMARY KEY,
        project_title        TEXT DEFAULT '',
        project_description  TEXT DEFAULT '',
        target_cycle_time    REAL DEFAULT 60.0,
        machine_list         TEXT DEFAULT '',
        robot_list           TEXT DEFAULT '',
        operator_list        TEXT DEFAULT '',
        predefined_processes TEXT DEFAULT '{}'
    )
"""

_ITEMS_DETAILS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS items_details (
        item_id                   TEXT PRIMARY KEY,
        op_number                 TEXT,
        designer_comments         TEXT,
        designer_resources_links  TEXT,
        designer_picture_links    TEXT,
        machine_cycle_time_summary TEXT,
        total_machine_cycle        REAL DEFAULT 0.0,
        total_manual_cycle         REAL DEFAULT 0.0,
        total_robot_cycle          REAL DEFAULT 0.0,
        total_operator_cycle       REAL DEFAULT 0.0,
        used_cycle_time            REAL DEFAULT 0.0,
        num_of_parts               INTEGER DEFAULT 1
    )
"""


def _ensure_project_metadata_table(conn):
    conn.execute(_PROJECT_METADATA_SCHEMA)


def _ensure_items_details_table(conn):
    conn.execute(_ITEMS_DETAILS_SCHEMA)


@app.route("/api/items_details/<op_number>", methods=["GET"])
def api_get_items_details(op_number):
    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _ensure_items_details_table(conn)
        cur  = conn.cursor()
        cur.execute("SELECT * FROM items_details WHERE op_number = ?", (op_number,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        conn.close()
        return jsonify(rows)
    return jsonify(ITEMS_DETAILS.get(op_number, []))


@app.route("/api/items_details", methods=["POST"])
def api_save_items_details():
    data      = request.get_json() or {}
    op_number = data.get("op_number", "")
    records   = data.get("records", [])

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        _ensure_items_details_table(conn)
        cur  = conn.cursor()
        for rec in records:
            item_id = rec.get("item_id")
            if not item_id:
                continue
            cur.execute(
                "UPDATE items_details SET "
                "op_number=?, machine_cycle_time_summary=?, "
                "total_machine_cycle=?, total_manual_cycle=?, "
                "total_robot_cycle=?, total_operator_cycle=?, "
                "used_cycle_time=?, num_of_parts=? "
                "WHERE item_id=?",
                (
                    op_number,
                    rec.get("machine_cycle_time_summary", "{}"),
                    rec.get("total_machine_cycle", 0.0),
                    rec.get("total_manual_cycle",  0.0),
                    rec.get("total_robot_cycle",   0.0),
                    rec.get("total_operator_cycle",0.0),
                    rec.get("used_cycle_time",     0.0),
                    rec.get("num_of_parts",        1),
                    item_id,
                )
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO items_details "
                    "(item_id, op_number, machine_cycle_time_summary, "
                    "total_machine_cycle, total_manual_cycle, total_robot_cycle, "
                    "total_operator_cycle, used_cycle_time, num_of_parts) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        item_id, op_number,
                        rec.get("machine_cycle_time_summary", "{}"),
                        rec.get("total_machine_cycle", 0.0),
                        rec.get("total_manual_cycle",  0.0),
                        rec.get("total_robot_cycle",   0.0),
                        rec.get("total_operator_cycle",0.0),
                        rec.get("used_cycle_time",     0.0),
                        rec.get("num_of_parts",        1),
                    )
                )
        conn.commit()
        conn.close()
    else:
        ITEMS_DETAILS[op_number] = records

    return jsonify({"status": "success", "saved": len(records)})


if __name__ == "__main__":
    print("ValiantTMS Cycle Chart server starting at http://localhost:5000")
    print("Open http://localhost:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=True)
