"""
ValiantTMS Cycle Chart - Demo Flask Server
Serves sample cycle chart data from an embedded dataset.
To use a real SQLite DB, set DB_PATH and TABLE_NAME below.
"""

try:
    from flask import Flask, jsonify, send_file, send_from_directory, request
except ImportError:
    print("Flask not installed. Run:  pip install flask")
    raise SystemExit(1)

import os
import io
import time
import re
import datetime
import sqlite3

try:
    import xlsxwriter as _xlw
    _HAS_XLSXWRITER = True
except ImportError:
    _HAS_XLSXWRITER = False

app = Flask(__name__)

# ── Excel export constants ────────────────────────────────────────────────────
_THEME_COLOR = '#09528A'
_CT_COLORS   = {
    'Machine':       '#05DF72',
    'Robot':         '#EBDD6C',
    'Manual':        '#EB1E29',
    'Operator':      '#FFA500',
    'Station':       '#09528A',
    'Process group': '#1A7FC4',
    'Undefined':     '#C0C0C0',
}

def _xls_annotate_steps(items, prefix=None):
    for k, item in enumerate(items or []):
        child_pfx = str(k + 1) if prefix is None else f'{prefix}_{k + 1}'
        item['step'] = f'S{k + 1}' if prefix is None else f'P{child_pfx}'
        _xls_annotate_steps(item.get('sub_process_items', []), child_pfx)

def _xls_flatten(items, out=None):
    if out is None:
        out = []
    for it in (items or []):
        if it.get('title'):
            out.append(it)
        _xls_flatten(it.get('sub_process_items', []), out)
    return out

def _xls_wrap(text, w=50):
    t = str(text or '')
    return '\n'.join(t[i:i + w] for i in range(0, len(t), w)) if t else ''

def _make_excel_report(proj_num, proj_title, target_ct, ops_data):
    """Generate in-memory .xlsx; returns a BytesIO seeked to 0."""
    out = io.BytesIO()
    wb  = _xlw.Workbook(out, {'in_memory': True})
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')

    # ── Cover sheet ──────────────────────────────────────────────────────────
    ws = wb.add_worksheet('Cover Page')
    ws.set_paper(1); ws.set_portrait(); ws.fit_to_pages(1, 0)
    ws.set_margins(0.2, 0.3, 0.8, 0.6)
    f_rpt  = wb.add_format({'align':'center','valign':'vcenter','font_size':15,'font_name':'Arial','font_color':_THEME_COLOR,'italic':True,'border':0})
    f_date = wb.add_format({'align':'center','valign':'vcenter','font_size':12,'font_name':'Arial','font_color':_THEME_COLOR,'italic':True,'border':0})
    f_ttl  = wb.add_format({'bold':True,'align':'center','valign':'vcenter','font_size':26,'font_name':'Arial','font_color':_THEME_COLOR,'text_wrap':True,'border':0})
    f_ct   = wb.add_format({'bold':True,'align':'center','valign':'vcenter','font_size':14,'font_name':'Arial','font_color':_THEME_COLOR,'border':0})
    ws.set_row(0, 30); ws.merge_range('A1:P1', 'Cycle Chart Report', f_rpt)
    ws.set_row(1, 25); ws.merge_range('A2:P2', date_str, f_date)
    ws.set_row(2, 80); ws.merge_range('A3:P3', f'Program #{proj_num} - {proj_title}', f_ttl)
    ws.set_row(3, 40); ws.merge_range('A4:P4', f'Target Cycle Time: {target_ct}s', f_ct)

    # ── Per-op sheets ─────────────────────────────────────────────────────────
    for opd in ops_data:
        op_num   = opd['op_number']
        op_title = opd['op_title']
        used_ct  = opd['used_ct']
        rows     = opd['flat_items']
        if not rows:
            continue

        sn = str(op_num)[:31]
        ws = wb.add_worksheet(sn)
        ws.set_paper(1); ws.set_portrait(); ws.fit_to_pages(1, 0)
        ws.set_margins(0.2, 0.3, 0.8, 0.6)
        ws.set_header(
            f'&R&"Arial,Italic"&10&K{_THEME_COLOR.lstrip("#")}'
            f'Program #{proj_num} - {proj_title}\n'
            f'{op_num} - {op_title}\n'
            f'Target Cycle Time: {target_ct}s - Actual Calculated Time: {used_ct}s'
        )

        f_mrg  = wb.add_format({'bold':True,'align':'center','valign':'vcenter','border':0,'bg_color':_THEME_COLOR,'font_color':'white','font_size':12,'font_name':'Arial'})
        f_chdr = wb.add_format({'bold':True,'bg_color':'#D9D9D9','border':1,'font_name':'Arial','font_size':10,'align':'center','valign':'vcenter'})
        f_cell = wb.add_format({'border':1,'font_name':'Arial','font_size':9,'text_wrap':True,'valign':'vcenter'})
        f_num  = wb.add_format({'border':1,'font_name':'Arial','font_size':9,'num_format':'0.00','valign':'vcenter','align':'center'})

        ws.set_column(0, 0, 35); ws.set_column(1, 4, 13)
        ws.set_row(0, 18); ws.merge_range('A1:P1', f'Program #{proj_num} - {proj_title}', f_mrg)
        ws.set_row(1, 18); ws.merge_range('A2:P2', f'{op_num} - {op_title}', f_mrg)
        ws.set_row(2, 18); ws.merge_range('A3:P3', f'Target Cycle Time: {target_ct}s - Actual Calculated time: {used_ct}s', f_mrg)

        HDR = 3   # 0-indexed → Excel row 4 ("A4")
        DST = HDR + 1
        n   = len(rows)

        for c, h in enumerate(['Step / Title', 'Cycle Start', 'Cycle Time', 'Cycle End', 'Cycle Type']):
            ws.write(HDR, c, h, f_chdr)

        for ri, it in enumerate(rows):
            r   = DST + ri
            lbl = _xls_wrap(f"{it.get('step','')}> {it.get('title','')}")
            ct  = it.get('cycle_type') or 'Undefined'
            ws.set_row(r, 30)
            ws.write(r, 0, lbl, f_cell)
            ws.write(r, 1, it.get('cycle_start') or 0.0, f_num)
            ws.write(r, 2, it.get('cycle_time')  or 0.0, f_num)
            ws.write(r, 3, it.get('cycle_end')   or 0.0, f_num)
            ws.write(r, 4, ct, f_cell)

        chart = wb.add_chart({'type': 'bar', 'subtype': 'stacked'})
        chart.add_series({
            'name': '', 'show_in_legend': False,
            'categories': [sn, DST, 0, DST + n - 1, 0],
            'values':     [sn, DST, 1, DST + n - 1, 1],
            'fill': {'none': True}, 'border': {'none': True}
        })

        seen, uq = set(), []
        for it in rows:
            ct = it.get('cycle_type') or 'Undefined'
            if ct not in seen: seen.add(ct); uq.append(ct)

        for i, ct in enumerate(uq):
            ci = 5 + i
            ws.write(HDR, ci, ct, f_chdr)
            for j, it in enumerate(rows):
                v = (it.get('cycle_time') or 0.0) if (it.get('cycle_type') or 'Undefined') == ct else None
                if v is not None:
                    ws.write(DST + j, ci, v)
            chart.add_series({
                'name': ct,
                'categories': [sn, DST, 0, DST + n - 1, 0],
                'values':     [sn, DST, ci, DST + n - 1, ci],
                'fill':   {'color': _CT_COLORS.get(ct, '#888888')},
                'border': {'width': 1.3, 'color': 'black'}
            })

        chart.set_x_axis({'min': 0, 'name': 'Cycle Time (s)',
                          'major_gridlines': {'visible': True, 'line': {'color': '#E3E3E3', 'width': 0.5}}})
        chart.set_y_axis({'reverse': True, 'num_font': {'name': 'Calibri', 'size': 10},
                          'major_gridlines': {'visible': True, 'line': {'color': '#E3E3E3', 'width': 0.5}}})
        chart.set_legend({'position': 'top', 'font': {'size': 12}})
        chart.set_plotarea({'border': {'none': True}})
        chart.set_size({'width': 1020, 'height': 200 + n * 45})
        ws.insert_chart('A4', chart)

    wb.close()
    out.seek(0)
    return out

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
        # Ensure dependant_items column exists (schema migration for older DBs)
        cur.execute(f"PRAGMA table_info({TABLE_NAME})")
        existing_cols = {row[1] for row in cur.fetchall()}
        if "dependant_items" not in existing_cols:
            cur.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN dependant_items TEXT DEFAULT '{{}}'")
            conn.commit()
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
    global SAMPLE_DATA
    data       = request.get_json() or {}
    import json as _json
    print("[updateDB] full payload:\n" + _json.dumps(data, indent=2))
    updated_db = data.get("cycle_general_structure", data.get("updated_db", []))
    op_number  = data.get("op_number", "")
    item_ids_to_delete = [str(item_id) for item_id in data.get("item_ids_to_delete", []) if item_id]
    ops_to_delete = [str(op) for op in data.get("ops_to_delete", []) if op]
    projects_to_delete = [str(project) for project in data.get("projects_to_delete", []) if project]

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
    project_number = str((proj_meta or {}).get("project_no") or "")
    flat = []
    def flatten(items):
        for item in (items or []):
            rec = {k: v for k, v in item.items()
                   if k not in ("sub_process_items", "nodes", "_ancestorEnds")}
            if project_number:
                rec["project_number"] = project_number
            flat.append(rec)
            flatten(item.get("sub_process_items") or item.get("nodes") or [])
    flatten(updated_db)

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        _ensure_project_number_col(conn)
        _ensure_items_details_table(conn)

        # Deletions are submitted with Save instead of being applied at edit
        # time. This lets the browser restore the item from its undo history.
        if item_ids_to_delete:
            ph = ",".join("?" for _ in item_ids_to_delete)
            cur.execute(f"DELETE FROM {TABLE_NAME} WHERE item_id IN ({ph})", item_ids_to_delete)
            cur.execute(f"DELETE FROM items_details WHERE item_id IN ({ph})", item_ids_to_delete)
        for deleted_op in ops_to_delete:
            cur.execute(f"DELETE FROM {TABLE_NAME} WHERE op_number = ?", (deleted_op,))
            cur.execute("DELETE FROM items_details WHERE op_number = ?", (deleted_op,))
            OP_NUMBERS.discard(deleted_op)
            OP_METADATA.pop(deleted_op, None)
        for deleted_project in projects_to_delete:
            cur.execute(f"DELETE FROM {TABLE_NAME} WHERE project_number = ?", (deleted_project,))
            cur.execute("DELETE FROM project_metadata WHERE project_no = ?", (deleted_project,))

        # Discover existing columns; auto-add any new ones from the payload
        cur.execute(f"PRAGMA table_info({TABLE_NAME})")
        existing_cols = {row[1] for row in cur.fetchall()}
        all_payload_cols = {c for rec in flat for c in rec if c not in ("item_id", "sub_process_items", "nodes", "_ancestorEnds")}
        for new_col in all_payload_cols - existing_cols:
            try:
                cur.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {new_col} TEXT DEFAULT '{{}}'")
                existing_cols.add(new_col)
            except Exception:
                pass

        for record in flat:
            item_id = record.get("item_id")
            if not item_id:
                continue
            cols = [c for c in record if c != "item_id" and c in existing_cols]
            if not cols:
                continue
            set_clause = ", ".join(c + " = ?" for c in cols)
            values     = [record[c] for c in cols] + [item_id]
            try:
                cur.execute(
                    "UPDATE " + TABLE_NAME + " SET " + set_clause + " WHERE item_id = ?",
                    values
                )
            except sqlite3.IntegrityError as e:
                print(f"[updateDB] IntegrityError on item_id={item_id}: {e}")
                print(f"[updateDB] cols={cols}")
                print(f"[updateDB] values={values}")
                raise
        conn.commit()
        conn.close()
    else:
        # Update in-memory sample data so changes survive the session
        by_id = {r["item_id"]: r for r in SAMPLE_DATA}
        for record in flat:
            item_id = record.get("item_id")
            if item_id and item_id in by_id:
                by_id[item_id].update(record)
        if item_ids_to_delete:
            SAMPLE_DATA = [r for r in SAMPLE_DATA if str(r.get("item_id")) not in item_ids_to_delete]
        for deleted_op in ops_to_delete:
            SAMPLE_DATA = [r for r in SAMPLE_DATA if str(r.get("op_number")) != deleted_op]
            OP_NUMBERS.discard(deleted_op)
            OP_METADATA.pop(deleted_op, None)

    return jsonify({
        "request_title": "updated_db",
        "status":        "success",
        "data":          [],
        "message":       "Updated {} record(s) for {}".format(len(flat), op_number)
    })


@app.route("/updateOpNumbers", methods=["POST"])
def api_update_op_numbers():
    return jsonify({"data": get_op_numbers()})


@app.route("/updateProjectNumbers", methods=["POST"])
def api_update_project_numbers():
    """Return op lists and project metadata keyed by project_number."""
    op_numbers_map   = {}   # { project_number: [op1, op2, ...] }
    project_meta_map = {}   # { project_number: { project_no, project_title, target_cycle_time, ... } }

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _ensure_project_number_col(conn)
        _ensure_project_metadata_table(conn)

        rows = conn.execute(
            f"SELECT DISTINCT project_number, op_number FROM {TABLE_NAME} "
            f"WHERE project_number IS NOT NULL AND project_number != '' "
            f"ORDER BY project_number, op_number"
        ).fetchall()
        for row in rows:
            pn = row["project_number"]
            op = row["op_number"]
            op_numbers_map.setdefault(pn, [])
            if op and op not in op_numbers_map[pn]:
                op_numbers_map[pn].append(op)

        if op_numbers_map:
            proj_nos = list(op_numbers_map.keys())
            ph = ",".join("?" * len(proj_nos))
            for row in conn.execute(
                f"SELECT * FROM project_metadata WHERE project_no IN ({ph})", proj_nos
            ).fetchall():
                project_meta_map[row["project_no"]] = dict(row)

        conn.close()
    else:
        seen = set()
        for r in SAMPLE_DATA:
            op = r.get("op_number")
            pn = r.get("project_number") or "default"
            if op and op not in seen:
                seen.add(op)
                op_numbers_map.setdefault(pn, [])
                op_numbers_map[pn].append(op)
        for op in OP_NUMBERS:
            if op not in seen:
                op_numbers_map.setdefault("default", []).append(op)
        for pn in op_numbers_map:
            project_meta_map[pn] = dict(JOB_METADATA, project_no=pn)

    return jsonify({
        "data": {
            "op_numbers":       op_numbers_map,
            "project_metadata": project_meta_map,
        }
    })


@app.route("/updateTree", methods=["POST"])
def api_update_tree():
    data           = request.get_json() or {}
    op             = data.get("op_number", "")
    project_number = data.get("project_number", "")

    # ── Project-level load (no op_number supplied) ────────────────────────────
    if not op and (project_number is not None):
        ops_data      = {}
        items_details = []
        proj_meta     = {}

        if DB_PATH and os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            _ensure_project_number_col(conn)
            _ensure_items_details_table(conn)
            _ensure_project_metadata_table(conn)

            if not project_number:
                row = conn.execute("SELECT project_no FROM project_metadata LIMIT 1").fetchone()
                if row:
                    project_number = row["project_no"]

            if project_number:
                records = [dict(r) for r in conn.execute(
                    f"SELECT * FROM {TABLE_NAME} WHERE project_number=?", (project_number,)
                )]
            else:
                records = [dict(r) for r in conn.execute(f"SELECT * FROM {TABLE_NAME}")]

            for rec in records:
                ops_data.setdefault(rec.get("op_number") or "", []).append(rec)

            item_ids = [r["item_id"] for r in records if r.get("item_id")]
            if item_ids:
                ph = ",".join("?" * len(item_ids))
                items_details = [dict(r) for r in conn.execute(
                    f"SELECT * FROM items_details WHERE item_id IN ({ph})", item_ids
                )]

            row = conn.execute(
                "SELECT * FROM project_metadata WHERE project_no=?", (project_number,)
            ).fetchone() if project_number else \
                conn.execute("SELECT * FROM project_metadata LIMIT 1").fetchone()
            proj_meta = dict(row) if row else JOB_METADATA
            conn.close()
        else:
            for rec in SAMPLE_DATA:
                ops_data.setdefault(rec.get("op_number") or "", []).append(rec)
            for op_list in ITEMS_DETAILS.values():
                items_details.extend(op_list)
            proj_meta = JOB_METADATA

        ops_trees   = {op: build_tree(recs) for op, recs in ops_data.items()}
        op_meta_out = {
            op: {
                "op_title":       OP_METADATA.get(op, {}).get("op_title", ""),
                "op_description": OP_METADATA.get(op, {}).get("op_description", ""),
                "num_of_parts":   OP_METADATA.get(op, {}).get("num_of_parts", 1),
            }
            for op in ops_data
        }
        return jsonify({
            "data": {
                "op_numbers":       sorted(ops_data.keys()),
                "ops":              ops_trees,
                "op_metadata":      op_meta_out,
                "items_details":    items_details,
                "project_metadata": [proj_meta] if proj_meta else [],
            }
        })

    # ── Single-op load (existing behaviour) ───────────────────────────────────
    records = get_records(op)
    tree    = build_tree(records)
    max_end = max((r.get("cycle_end") or 0.0 for r in records), default=0.0)

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

    op_meta = OP_METADATA.get(op, {})
    op_meta_rows = [{
        "op_number":       op,
        "op_title":        op_meta.get("op_title", ""),
        "job_description": op_meta.get("op_description", ""),
        "num_of_parts":    op_meta.get("num_of_parts", 1),
    }] if op else []

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _ensure_project_metadata_table(conn)
        _ensure_project_number_col(conn)
        cur = conn.cursor()
        # Look up which project this op belongs to, then fetch that project's metadata
        op_row = cur.execute(
            f"SELECT project_number FROM {TABLE_NAME} WHERE op_number=? LIMIT 1", (op,)
        ).fetchone()
        proj_no = op_row["project_number"] if op_row and op_row["project_number"] else None
        if proj_no:
            proj_row = cur.execute(
                "SELECT * FROM project_metadata WHERE project_no=?", (proj_no,)
            ).fetchone()
        else:
            proj_row = cur.execute("SELECT * FROM project_metadata LIMIT 1").fetchone()
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
        control_comments          TEXT,
        control_resources_links   TEXT,
        control_picture_links     TEXT,
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


def _ensure_project_number_col(conn):
    """Migrate cycle_general_structure to add project_number column if missing."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
    if "project_number" not in existing:
        conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN project_number TEXT DEFAULT ''")
        conn.commit()
    # Migrate: add columns introduced after the initial schema
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(items_details)")}
    for col, defn in [
        ("control_comments",        "TEXT"),
        ("control_resources_links", "TEXT"),
        ("control_picture_links",   "TEXT"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE items_details ADD COLUMN {col} {defn}")


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
            vals_update = (
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
            try:
                cur.execute(
                    "UPDATE items_details SET "
                    "op_number=?, machine_cycle_time_summary=?, "
                    "total_machine_cycle=?, total_manual_cycle=?, "
                    "total_robot_cycle=?, total_operator_cycle=?, "
                    "used_cycle_time=?, num_of_parts=? "
                    "WHERE item_id=?",
                    vals_update
                )
            except sqlite3.IntegrityError as e:
                print(f"[items_details] IntegrityError UPDATE item_id={item_id}: {e}")
                print(f"[items_details] values={vals_update}")
                raise
            if cur.rowcount == 0:
                vals_insert = (
                    item_id, op_number,
                    rec.get("machine_cycle_time_summary", "{}"),
                    rec.get("total_machine_cycle", 0.0),
                    rec.get("total_manual_cycle",  0.0),
                    rec.get("total_robot_cycle",   0.0),
                    rec.get("total_operator_cycle",0.0),
                    rec.get("used_cycle_time",     0.0),
                    rec.get("num_of_parts",        1),
                )
                try:
                    cur.execute(
                        "INSERT INTO items_details "
                        "(item_id, op_number, machine_cycle_time_summary, "
                        "total_machine_cycle, total_manual_cycle, total_robot_cycle, "
                        "total_operator_cycle, used_cycle_time, num_of_parts) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        vals_insert
                    )
                except sqlite3.IntegrityError as e:
                    print(f"[items_details] IntegrityError INSERT item_id={item_id}: {e}")
                    print(f"[items_details] values={vals_insert}")
                    raise
        conn.commit()
        conn.close()
    else:
        ITEMS_DETAILS[op_number] = records

    return jsonify({"status": "success", "saved": len(records)})


@app.route('/api/export/excel', methods=['POST'])
def api_export_excel():
    if not _HAS_XLSXWRITER:
        return jsonify({'error': 'xlsxwriter not installed. Run: pip install xlsxwriter'}), 500

    data       = request.get_json() or {}
    op_nums    = data.get('op_numbers') or get_op_numbers()
    proj       = JOB_METADATA
    proj_num   = str(proj.get('job_number') or proj.get('project_no') or '')
    proj_title = str(proj.get('job_title')  or proj.get('project_title') or '')
    target_ct  = proj.get('target_cycle_time', 60)

    ops_data = []
    for op in op_nums:
        records = get_records(op)
        if not records:
            continue
        tree = build_tree(records)
        _xls_annotate_steps(tree, None)
        flat = _xls_flatten(tree)
        meta = OP_METADATA.get(op, {})
        used = round(max((r.get('cycle_end') or 0.0 for r in records), default=0.0), 2)
        ops_data.append({'op_number': op, 'op_title': meta.get('op_title', ''), 'used_ct': used, 'flat_items': flat})

    date_str   = datetime.datetime.now().strftime('%Y-%m-%d')
    safe_num   = proj_num.replace('/', '_').replace('\\', '_')
    safe_title = proj_title.replace('/', '_').replace('\\', '_')
    fname      = f'#{safe_num}_{safe_title}_{date_str}.xlsx'

    xls = _make_excel_report(proj_num, proj_title, target_ct, ops_data)
    return send_file(
        xls,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=fname
    )


def _parse_sheet_to_records(job_no, df):
    """Parse one Excel sheet (already renamed columns) into flat DB records."""
    import math, uuid

    def _isnan(v):
        try:
            return math.isnan(float(v))
        except (TypeError, ValueError):
            return True

    records        = []
    station_step   = 0
    station_index  = -1
    process_step   = 1
    process_index  = 0
    current_st_id  = None
    pending_st     = None
    pending_procs  = []
    max_id = max((r.get("id", 0) for r in SAMPLE_DATA), default=0) + 1

    def _flush_station():
        nonlocal max_id
        if pending_st is None:
            return
        if pending_procs:
            max_end = max(p["cycle_end"] for p in pending_procs)
            pending_st["cycle_end"]  = max_end
            pending_st["cycle_time"] = max_end
        pending_st["subprocess"] = ",".join(p["item_id"] for p in pending_procs)
        records.append(pending_st)
        records.extend(pending_procs)

    for _, row in df.iterrows():
        title = row.get("title", "")
        if not isinstance(title, str) or not title.strip():
            continue
        title = title.strip()

        ct_raw = row.get("cycle_time")
        ce_raw = row.get("cycle_end")

        if _isnan(ct_raw) and _isnan(ce_raw):
            # ── Station row ──────────────────────────────────────────────────
            _flush_station()
            station_step  += 1
            station_index += 1
            process_step   = 1
            process_index  = 0
            sid = str(uuid.uuid4())
            pending_st = {
                "id": max_id, "item_id": sid, "op_number": job_no,
                "title": title, "parent_id": "", "subprocess": "",
                "cycle_type": "Station", "step": f"S{station_step}",
                "tree_index": station_index,
                "cycle_start": 0.0, "cycle_end": 0.0, "cycle_time": 0.0,
                "highlight": "", "color": "#09528A",
                "dependant_items": "{}", "run_cond_config": "{}",
            }
            max_id      += 1
            pending_procs = []
            current_st_id = sid

        else:
            # ── Process row ──────────────────────────────────────────────────
            if current_st_id is None:
                continue
            try:
                ct = round(float(ct_raw), 2) if not _isnan(ct_raw) else 0.0
                ce = round(float(ce_raw), 2) if not _isnan(ce_raw) else 0.0
            except (TypeError, ValueError):
                ct = ce = 0.0
            cs = round(ce - ct, 2)
            pid = str(uuid.uuid4())
            pending_procs.append({
                "id": max_id, "item_id": pid, "op_number": job_no,
                "title": title, "parent_id": current_st_id, "subprocess": "",
                "cycle_type": "Undefined", "step": f"P{station_step}_{process_step}",
                "tree_index": process_index,
                "cycle_start": cs, "cycle_end": ce, "cycle_time": ct,
                "highlight": "", "color": "#C0C0C0",
                "dependant_items": "{}", "run_cond_config": "{}",
            })
            max_id       += 1
            process_step += 1
            process_index += 1

    _flush_station()
    return records


def _insert_records_to_db(records):
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute(f"PRAGMA table_info({TABLE_NAME})")
    existing_cols = {row[1] for row in cur.fetchall()}
    for rec in records:
        iid  = rec.get("item_id")
        cols = [c for c in rec if c in existing_cols and c != "item_id"]
        if not iid or not cols:
            continue
        all_cols = ["item_id"] + cols
        ph       = ", ".join("?" for _ in all_cols)
        vals     = [rec.get(c) for c in all_cols]
        try:
            cur.execute(
                f"INSERT OR REPLACE INTO {TABLE_NAME} ({', '.join(all_cols)}) VALUES ({ph})",
                vals,
            )
        except Exception as e:
            print(f"[import_excel] DB error: {e}")
    conn.commit()
    conn.close()


@app.route("/api/import_excel", methods=["POST"])
def api_import_excel():
    global SAMPLE_DATA

    try:
        import pandas as pd
    except ImportError:
        return jsonify({"status": "error",
                        "message": "pandas not installed — run: pip install pandas openpyxl"}), 500

    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith((".xlsx", ".xls")):
        return jsonify({"status": "error", "message": "File must be .xlsx or .xls"}), 400

    try:
        sheets = pd.read_excel(f, sheet_name=None, header=9)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not read Excel: {e}"}), 400

    REQUIRED_COLS = {"DESCRIPTION", "SEC", "SEC.1"}
    imported, skipped, errors = [], [], []

    for sheet_name, df in sheets.items():
        if not REQUIRED_COLS.issubset(df.columns):
            skipped.append(f"{sheet_name} (missing columns: DESCRIPTION / SEC / SEC.1)")
            continue
        if sheet_name in get_op_numbers():
            errors.append(f"{sheet_name} already exists")
            continue

        df = df.rename(columns={"DESCRIPTION": "title", "SEC": "cycle_time", "SEC.1": "cycle_end"})
        records = _parse_sheet_to_records(sheet_name, df)
        if not records:
            skipped.append(f"{sheet_name} (no valid data)")
            continue

        if DB_PATH and os.path.exists(DB_PATH):
            _insert_records_to_db(records)
        else:
            SAMPLE_DATA.extend(records)

        OP_NUMBERS.add(sheet_name)
        OP_METADATA[sheet_name] = {"op_title": "", "op_description": "", "num_of_parts": 1}
        imported.append(sheet_name)

    return jsonify({"status": "success", "imported": imported,
                    "skipped": skipped, "errors": errors})


def _pic_folder():
    base = os.path.dirname(os.path.abspath(DB_PATH)) if DB_PATH else os.getcwd()
    folder = os.path.join(base, "pic")
    os.makedirs(folder, exist_ok=True)
    return folder


def _safe_filename(name):
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "file"


@app.route("/api/more_spec", methods=["POST"])
def api_save_more_spec():
    data    = request.get_json() or {}
    item_id = data.get("item_id")
    op      = data.get("op_number", "")
    if not item_id:
        return jsonify({"error": "item_id required"}), 400

    allowed = {
        "designer_comments", "designer_resources_links", "designer_picture_links",
        "control_comments",  "control_resources_links",  "control_picture_links",
    }
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return jsonify({"ok": True})

    if DB_PATH and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        _ensure_items_details_table(conn)
        cur  = conn.cursor()
        set_clause   = ", ".join(f"{k}=?" for k in fields)
        update_vals  = list(fields.values()) + [item_id]
        cur.execute(f"UPDATE items_details SET {set_clause} WHERE item_id=?", update_vals)
        if cur.rowcount == 0:
            cols = ["item_id", "op_number"] + list(fields.keys())
            vals = [item_id, op] + list(fields.values())
            cur.execute(
                f"INSERT INTO items_details ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                vals,
            )
        conn.commit()
        conn.close()
    else:
        cache = ITEMS_DETAILS.setdefault(op, [])
        row   = next((r for r in cache if r.get("item_id") == item_id), None)
        if row:
            row.update(fields)
        else:
            cache.append({"item_id": item_id, "op_number": op, **fields})

    return jsonify({"ok": True})


@app.route("/api/upload_picture", methods=["POST"])
def api_upload_picture():
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400
    safe  = f"{int(time.time() * 1000)}_{_safe_filename(f.filename)}"
    dest  = os.path.join(_pic_folder(), safe)
    f.save(dest)
    return jsonify({"filename": safe, "url": f"/api/pic/{safe}"})


@app.route("/api/pic/<filename>", methods=["GET"])
def api_get_picture(filename):
    return send_from_directory(_pic_folder(), filename)


if __name__ == "__main__":
    print("ValiantTMS Cycle Chart server starting at http://localhost:5000")
    print("Open http://localhost:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=True)
