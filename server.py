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
    """Return sorted list of distinct op_number values."""
    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT op_number FROM {TABLE_NAME} ORDER BY op_number")
        ops = [r[0] for r in cur.fetchall()]
        conn.close()
        return ops
    seen = {}
    result = []
    for r in SAMPLE_DATA:
        op = r["op_number"]
        if op not in seen:
            seen[op] = True
            result.append(op)
    return sorted(result)


def build_tree(records):
    """Convert flat record list into nested tree via parent_id / subprocess ordering."""
    by_id = {r["item_id"]: dict(r, nodes=[]) for r in records}
    roots = []

    for r in records:
        pid = r.get("parent_id") or ""
        if pid and pid in by_id:
            by_id[pid]["nodes"].append(by_id[r["item_id"]])
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
            node["nodes"].sort(key=rank)
        else:
            node["nodes"].sort(key=lambda c: c.get("tree_index") or 0)
        for child in node["nodes"]:
            sort_node(child)

    roots.sort(key=lambda r: r.get("tree_index") or 0)
    for root in roots:
        sort_node(root)

    return roots


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
    updated_db = data.get("updated_db", [])
    op_number  = data.get("op_number", "")

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


if __name__ == "__main__":
    print("ValiantTMS Cycle Chart server starting at http://localhost:5000")
    print("Open http://localhost:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=True)
