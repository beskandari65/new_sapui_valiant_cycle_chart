"""PDF reporting for Project Management charts."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from xml.sax.saxutils import escape as xml_escape
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


BLUE = colors.HexColor("#0854A0")
BROWN = colors.HexColor("#A52A2A")
GREEN = colors.HexColor("#107E3E")
LIGHT_BLUE = colors.HexColor("#EAF3F9")
LIGHT_GRAY = colors.HexColor("#EDF1F3")
TEXT = colors.HexColor("#203446")


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _task_numbers(tasks: list[dict[str, Any]]) -> dict[str, str]:
    known = {task["task_id"] for task in tasks}
    children: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        parent = task.get("parent_id")
        parent = parent if parent in known else ""
        children.setdefault(parent or "", []).append(task)
    numbers: dict[str, str] = {}

    def walk(parent_id: str, prefix: str) -> None:
        for index, task in enumerate(children.get(parent_id, []), 1):
            number = f"{prefix}-{index}" if prefix else f"T{index}"
            numbers[task["task_id"]] = number
            walk(task["task_id"], number)

    walk("", "")
    return numbers


def _subtree(tasks: list[dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    included = {root_id}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task.get("parent_id") in included and task["task_id"] not in included:
                included.add(task["task_id"])
                changed = True
    return [task for task in tasks if task["task_id"] in included]


def _condition_rows(
    tasks: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    numbers: dict[str, str],
) -> list[list[str]]:
    task_map = {task["task_id"]: task for task in tasks}
    labels = {"FS": "Start after", "SS": "Start with", "SF": "End before", "FF": "End with"}
    rows: list[list[str]] = []
    for task in tasks:
        conditions: list[str] = []
        for dependency in dependencies:
            if dependency.get("successor_id") != task["task_id"]:
                continue
            reference = task_map.get(dependency.get("predecessor_id"))
            reference_text = (
                f"{numbers.get(reference['task_id'], '')} {reference.get('title', '')}".strip()
                if reference
                else str(dependency.get("predecessor_id") or "")
            )
            lag = float(dependency.get("lag_value") or 0)
            lag_text = f" ({lag:+g} working days)" if lag else ""
            conditions.append(
                f"{labels.get(dependency.get('dependency_type'), dependency.get('dependency_type'))}: "
                f"{reference_text}{lag_text}"
            )
        try:
            config = json.loads(task.get("schedule_config") or "{}")
        except (TypeError, json.JSONDecodeError):
            config = task.get("schedule_config") if isinstance(task.get("schedule_config"), dict) else {}
        if config.get("start_from_parent"):
            parent = task_map.get(task.get("parent_id"))
            conditions.append(
                "Start from parent: "
                + (f"{numbers.get(parent['task_id'], '')} {parent.get('title', '')}".strip()
                   if parent else "Parent task")
            )
        if config.get("duration_equal_to"):
            reference = task_map.get(config["duration_equal_to"])
            conditions.append(
                "Duration equal to: "
                + (f"{numbers.get(reference['task_id'], '')} {reference.get('title', '')}".strip()
                   if reference else str(config["duration_equal_to"]))
            )
        if conditions:
            rows.append([
                numbers.get(task["task_id"], ""),
                str(task.get("title") or ""),
                "\n".join(conditions),
            ])
    return rows


class GanttChart(Flowable):
    """Compact task-subtree Gantt drawing with repeated scale per page chunk."""

    def __init__(
        self, tasks: list[dict[str, Any]], numbers: dict[str, str],
        width: float = 9.5 * inch, scale: str = "auto",
    ):
        self.tasks = tasks
        self.numbers = numbers
        self.width = width
        self.label_width = 2.35 * inch
        self.timeline_gutter = 12
        self.timeline_start = self.label_width + self.timeline_gutter
        self.row_height = 18
        dates = [
            parsed
            for task in tasks
            for parsed in (_parse_date(task.get("start_date")), _parse_date(task.get("end_date")))
            if parsed
        ]
        self.minimum = min(dates) if dates else date.today()
        self.maximum = max(dates) if dates else self.minimum
        self.total_days = max(1, (self.maximum - self.minimum).days + 1)
        self.scale = scale
        self.daily_labels = scale == "days" or (
            scale == "auto" and self.total_days <= 45
        )
        self.header_height = 52 if self.daily_labels else 28
        self.height = self.header_height + len(tasks) * self.row_height

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        canvas = self.canv
        timeline_width = self.width - self.timeline_start
        day_width = timeline_width / self.total_days
        canvas.setFillColor(LIGHT_BLUE)
        canvas.rect(0, self.height - self.header_height, self.width, self.header_height, fill=1, stroke=0)
        canvas.setFillColor(TEXT)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(5, self.height - 18, "Task")

        # Adaptive header labels.
        if self.daily_labels:
            step = 1
            formatter = lambda day: day.strftime("%b %d")
        elif self.scale == "weeks":
            step = 7
            formatter = lambda day: day.strftime("%b %d")
        elif self.scale == "months":
            step = 30
            formatter = lambda day: day.strftime("%b %Y")
        elif self.scale == "years":
            step = 365
            formatter = lambda day: day.strftime("%Y")
        elif self.total_days <= 400:
            step = 30
            formatter = lambda day: day.strftime("%b %Y")
        else:
            step = 365
            formatter = lambda day: day.strftime("%Y")
        offset = 0
        while offset < self.total_days:
            current = self.minimum.fromordinal(self.minimum.toordinal() + offset)
            x = self.timeline_start + offset * day_width
            canvas.setStrokeColor(colors.HexColor("#DFE7EB"))
            canvas.line(x, 0, x, self.height)
            canvas.setFillColor(TEXT)
            if self.daily_labels:
                canvas.saveState()
                canvas.setFont("Helvetica", 6)
                canvas.translate(
                    x + min(day_width * .65, 8),
                    self.height - self.header_height + 4,
                )
                canvas.rotate(90)
                canvas.drawString(0, 0, formatter(current))
                canvas.restoreState()
            else:
                canvas.setFont("Helvetica", 7)
                canvas.drawString(x + 2, self.height - 18, formatter(current))
            offset += step

        parent_ids = {task.get("parent_id") for task in self.tasks if task.get("parent_id")}
        task_map = {task["task_id"]: task for task in self.tasks}
        for index, task in enumerate(self.tasks):
            y = self.height - self.header_height - (index + 1) * self.row_height
            if index % 2:
                canvas.setFillColor(colors.HexColor("#FAFBFC"))
                canvas.rect(0, y, self.width, self.row_height, fill=1, stroke=0)
            number = self.numbers.get(task["task_id"], "")
            title = str(task.get("title") or "")
            label = f"{number} {title}".strip()
            while stringWidth(label, "Helvetica", 7) > self.label_width - 10 and len(label) > 5:
                label = label[:-4] + "..."
            canvas.setFillColor(BROWN if not task.get("parent_id") else TEXT)
            canvas.setFont("Helvetica-Bold" if task["task_id"] in parent_ids else "Helvetica", 7)
            canvas.drawString(5, y + 6, label)
            ancestor_id = task.get("parent_id")
            while ancestor_id and ancestor_id in task_map:
                ancestor = task_map[ancestor_id]
                ancestor_finish = _parse_date(ancestor.get("end_date"))
                if ancestor_finish:
                    marker = self.timeline_start + (
                        (ancestor_finish - self.minimum).days + 1
                    ) * day_width
                    canvas.setStrokeColor(colors.HexColor("#929FA8"))
                    canvas.setDash(2, 2)
                    canvas.line(marker, y, marker, y + self.row_height)
                    canvas.setDash()
                ancestor_id = ancestor.get("parent_id")
            start = _parse_date(task.get("start_date"))
            finish = _parse_date(task.get("end_date"))
            if start and finish:
                left = self.timeline_start + (start - self.minimum).days * day_width
                bar_width = max(2, ((finish - start).days + 1) * day_width)
                bar_y = y + 5
                if task["task_id"] in parent_ids:
                    canvas.setStrokeColor(BROWN if not task.get("parent_id") else BLUE)
                    canvas.setDash(3, 2)
                    # Match the application parent bar: top and sides only.
                    canvas.line(left, bar_y, left, bar_y + 8)
                    canvas.line(left, bar_y + 8, left + bar_width, bar_y + 8)
                    canvas.line(left + bar_width, bar_y + 8, left + bar_width, bar_y)
                    canvas.setDash()
                else:
                    canvas.setFillColor(
                        BROWN if not task.get("parent_id")
                        else colors.HexColor(str(task.get("color") or "#0A6ED1"))
                    )
                    canvas.rect(left, bar_y, bar_width, 8, fill=1, stroke=0)
                progress = max(0, min(100, float(task.get("progress_percent") or 0)))
                canvas.setFillColor(GREEN if progress >= 100 else TEXT)
                canvas.setFont("Helvetica-Bold", 6)
                canvas.drawString(min(self.width - 22, left + bar_width + 3), bar_y + 1, "OK" if progress >= 100 else f"{progress:g}%")
            canvas.setStrokeColor(colors.HexColor("#F0F3F5"))
            canvas.line(0, y, self.width, y)


def _report_table(data, widths, header=True):
    header_style = ParagraphStyle(
        "TableHeader", fontName="Helvetica-Bold", fontSize=7,
        leading=8.5, textColor=colors.white,
    )
    body_style = ParagraphStyle(
        "TableBody", fontName="Helvetica", fontSize=7,
        leading=8.5, textColor=TEXT,
    )
    wrapped = []
    for row_index, row in enumerate(data):
        style = header_style if header and row_index == 0 else body_style
        wrapped.append([
            Paragraph(
                xml_escape(str(value if value is not None else "")).replace("\n", "<br/>"),
                style,
            )
            for value in row
        ])
    table = Table(wrapped, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#CDD8DF")),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("LEADING", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFB")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    table.setStyle(TableStyle(commands))
    return table


def build_project_report(
    chart: dict[str, Any],
    issues: list[dict[str, Any]],
    activity: list[dict[str, Any]],
    options: dict[str, Any],
) -> io.BytesIO:
    all_tasks = chart.get("tasks") or []
    numbers = _task_numbers(all_tasks)
    scope = str(options.get("scope") or "project")
    selected_id = str(options.get("task_id") or "")
    tasks = _subtree(all_tasks, selected_id) if scope == "task" else list(all_tasks)
    included_ids = {task["task_id"] for task in tasks}
    scoped_issues = [issue for issue in issues if issue.get("task_id") in included_ids]
    if not options.get("include_resolved", True):
        scoped_issues = [
            issue for issue in scoped_issues
            if issue.get("status") not in {"Resolved", "Closed"}
        ]
    project = chart.get("project") or {}
    title = (
        f"Task Report - {numbers.get(selected_id, '')} "
        f"{next((task.get('title') for task in tasks if task.get('task_id') == selected_id), '')}"
        if scope == "task"
        else "Comprehensive Project Report"
    ).strip()

    stream = io.BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=landscape(letter),
        rightMargin=.38 * inch,
        leftMargin=.38 * inch,
        topMargin=.42 * inch,
        bottomMargin=.42 * inch,
        title=title,
        author="ValiantTMS PM",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "ReportTitle", parent=styles["Title"], textColor=BLUE,
        fontSize=22, leading=26, alignment=TA_CENTER, spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        "Section", parent=styles["Heading2"], textColor=BLUE,
        fontSize=13, leading=16, spaceBefore=10, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "Small", parent=styles["BodyText"], textColor=TEXT,
        fontSize=8, leading=10, alignment=TA_LEFT,
    ))

    story: list[Any] = [
        Paragraph(title, styles["ReportTitle"]),
        Paragraph(
            f"<b>Project:</b> {project.get('project_number', '')} - {project.get('title', '')}<br/>"
            f"<b>Generated:</b> {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}<br/>"
            f"<b>Scope:</b> {'Selected task and descendants' if scope == 'task' else 'Entire project'}",
            styles["Small"],
        ),
        Spacer(1, 10),
    ]
    progress_values = [float(task.get("progress_percent") or 0) for task in tasks]
    average_progress = sum(progress_values) / len(progress_values) if progress_values else 0
    overdue = [
        task for task in tasks
        if (_parse_date(task.get("end_date")) or date.max) < date.today()
        and float(task.get("progress_percent") or 0) < 100
    ]
    open_issues = [
        issue for issue in scoped_issues
        if issue.get("status") not in {"Resolved", "Closed"}
    ]
    summary = _report_table(
        [
            ["Tasks", "Average progress", "Open issues", "Resolved issues", "Overdue tasks"],
            [
                str(len(tasks)), f"{average_progress:.0f}%", str(len(open_issues)),
                str(len(scoped_issues) - len(open_issues)), str(len(overdue)),
            ],
        ],
        [1.2 * inch, 1.7 * inch, 1.4 * inch, 1.5 * inch, 1.4 * inch],
    )
    story.extend([
        summary,
        Paragraph("Gantt schedule", styles["Section"]),
        Spacer(1, 6),
    ])

    for chunk_index in range(0, len(tasks), 27):
        if chunk_index:
            story.append(PageBreak())
            story.append(Paragraph("Gantt schedule - continued", styles["Section"]))
            story.append(Spacer(1, 6))
        story.append(GanttChart(
            tasks[chunk_index:chunk_index + 27],
            numbers,
            scale=str(options.get("timeline_scale") or "auto"),
        ))

    story.extend([PageBreak(), Paragraph("Task register", styles["Section"])])
    task_rows = [["Task", "Title", "Start", "Finish", "Duration", "Progress", "Resource"]]
    assignment_map = {
        assignment["task_id"]: assignment for assignment in chart.get("assignments") or []
    }
    for task in tasks:
        assignment = assignment_map.get(task["task_id"]) or {}
        task_rows.append([
            numbers.get(task["task_id"], ""),
            str(task.get("title") or ""),
            str(task.get("start_date") or ""),
            str(task.get("end_date") or ""),
            f"{float(task.get('duration_value') or 0):g} days",
            f"{float(task.get('progress_percent') or 0):g}%",
            str(assignment.get("resource_name") or assignment.get("role") or "Unassigned"),
        ])
    story.append(_report_table(
        task_rows,
        [.55 * inch, 2.5 * inch, .8 * inch, .8 * inch, .75 * inch, .7 * inch, 1.25 * inch],
    ))

    conditions = _condition_rows(tasks, chart.get("dependencies") or [], numbers)
    story.extend([Paragraph("Schedule conditions", styles["Section"])])
    if conditions:
        story.append(_report_table(
            [["Task", "Title", "Condition"]] + conditions,
            [.6 * inch, 2.2 * inch, 6.6 * inch],
        ))
    else:
        story.append(Paragraph("No schedule conditions in this scope.", styles["Small"]))

    story.extend([Paragraph("Issue register", styles["Section"])])
    if scoped_issues:
        issue_rows = [["Task", "Issue", "Priority", "Owner", "Status", "Due", "Resolution"]]
        for issue in scoped_issues:
            issue_rows.append([
                numbers.get(issue.get("task_id"), ""),
                str(issue.get("title") or ""),
                str(issue.get("priority") or ""),
                str(issue.get("owner") or "Unassigned"),
                str(issue.get("status") or ""),
                str(issue.get("due_date") or ""),
                str(issue.get("resolution_note") or ""),
            ])
        story.append(_report_table(
            issue_rows,
            [.55 * inch, 2.1 * inch, .7 * inch, 1.1 * inch, .85 * inch, .75 * inch, 3.2 * inch],
        ))
    else:
        story.append(Paragraph("No issues in this scope.", styles["Small"]))

    if overdue:
        story.extend([Paragraph("Overdue and incomplete tasks", styles["Section"])])
        story.append(_report_table(
            [["Task", "Title", "Finish", "Progress"]] + [
                [
                    numbers.get(task["task_id"], ""), str(task.get("title") or ""),
                    str(task.get("end_date") or ""), f"{float(task.get('progress_percent') or 0):g}%",
                ]
                for task in overdue
            ],
            [.65 * inch, 4.5 * inch, 1.0 * inch, .9 * inch],
        ))

    if options.get("include_activity"):
        story.extend([PageBreak(), Paragraph("Activity history", styles["Section"])])
        scoped_activity = [
            item for item in activity
            if not item.get("task_id") or item.get("task_id") in included_ids
        ]
        if scoped_activity:
            activity_rows = [["Date", "Task", "Event", "Reason"]]
            for item in scoped_activity:
                activity_rows.append([
                    str(item.get("created_at") or ""),
                    numbers.get(item.get("task_id"), ""),
                    str(item.get("event_type") or "").replace("_", " ").title(),
                    str(item.get("reason") or ""),
                ])
            story.append(_report_table(
                activity_rows,
                [1.35 * inch, .65 * inch, 1.25 * inch, 6.0 * inch],
            ))
        else:
            story.append(Paragraph("No activity history in this scope.", styles["Small"]))

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#607887"))
        canvas.drawRightString(
            landscape(letter)[0] - .38 * inch,
            .2 * inch,
            f"{project.get('project_number', '')} - Page {doc.page}",
        )
        canvas.restoreState()

    document.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    stream.seek(0)
    return stream
