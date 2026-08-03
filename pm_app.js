(function () {
  "use strict";

  var API = "/api/pm";
  var DAY_MS = 86400000;
  var state = {
    root: null,
    projects: [],
    projectId: "",
    chart: null,
    resources: [],
    conflicts: [],
    issues: [],
    pendingIssueChanges: {},
    dirty: false,
    selectedTaskId: "",
    selectedTaskIds: [],
    pendingScheduleCondition: null,
    lastTitleClickId: "",
    lastTitleClickTime: 0,
    lastBarClickId: "",
    lastBarClickTime: 0,
    collapsedTaskIds: {},
    monthView: "all",
    columnWidths: {
      index: 64,
      title: 220,
      start: 105,
      duration: 110,
      finish: 105,
      progress: 110,
      issues: 82,
      resource: 165
    },
    clipboard: { mode: "", tasks: [], dependencies: [], assignments: [], rootIds: [] },
    pendingActivityLogs: {},
    lastAddedTaskId: "",
    timeScale: "auto",
    dayWidth: 34
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function uid(prefix) {
    if (window.crypto && window.crypto.randomUUID) {
      return prefix + "_" + window.crypto.randomUUID().replace(/-/g, "");
    }
    return prefix + "_" + Date.now() + "_" + Math.random().toString(16).slice(2);
  }

  function parseDate(value) {
    if (!value) return null;
    var parts = String(value).split("-").map(Number);
    if (parts.length !== 3 || parts.some(isNaN)) return null;
    return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  }

  function isoDate(date) {
    return date ? date.toISOString().slice(0, 10) : "";
  }

  function addCalendarDays(date, count) {
    return new Date(date.getTime() + count * DAY_MS);
  }

  function addWorkingDays(start, duration) {
    var days = Math.max(0, Math.ceil(Number(duration) || 0));
    if (!start || days === 0) return start;
    var cursor = new Date(start.getTime());
    var consumed = 1;
    while (consumed < days) {
      cursor = addCalendarDays(cursor, 1);
      var weekday = cursor.getUTCDay();
      if (weekday !== 0 && weekday !== 6) consumed++;
    }
    return cursor;
  }

  function shiftWorkingDays(start, count) {
    if (!start || !count) return start;
    var cursor = new Date(start.getTime());
    var direction = count < 0 ? -1 : 1;
    var remaining = Math.abs(Math.round(count));
    while (remaining > 0) {
      cursor = addCalendarDays(cursor, direction);
      var weekday = cursor.getUTCDay();
      if (weekday !== 0 && weekday !== 6) remaining--;
    }
    return cursor;
  }

  function scheduleConfig(task) {
    if (!task) return {};
    if (typeof task.schedule_config === "object" && task.schedule_config) return task.schedule_config;
    try { return JSON.parse(task.schedule_config || "{}"); } catch (error) { return {}; }
  }

  function calculateEnd(task) {
    var start = parseDate(task.start_date);
    if (!start) return "";
    var duration = Math.max(0, Number(task.duration_value) || 0);
    if (task.task_type === "milestone" || duration === 0) return task.start_date;
    if (task.duration_unit === "calendar_days") {
      return isoDate(addCalendarDays(start, Math.max(0, Math.ceil(duration) - 1)));
    }
    return isoDate(addWorkingDays(start, duration));
  }

  function recalculateSchedule(changedTaskId) {
    if (!state.chart) return;
    var tasks = state.chart.tasks || [];
    var map = taskMap();
    var children = {};
    tasks.forEach(function (task) {
      if (task.parent_id) (children[task.parent_id] || (children[task.parent_id] = [])).push(task);
      task.end_date = calculateEnd(task);
    });

    // The CC parent-process rule: parent and first child always share a start.
    Object.keys(children).forEach(function (parentId) {
      var parent = map[parentId];
      var first = children[parentId][0];
      if (!parent || !first) return;
      if (changedTaskId === first.task_id) parent.start_date = first.start_date;
      else first.start_date = parent.start_date;
      first.end_date = calculateEnd(first);
    });

    function aggregateParentSpans() {
      Object.keys(children).reverse().forEach(function (parentId) {
        var parent = map[parentId];
        var group = children[parentId];
        if (!parent || !group.length) return;
        parent.start_date = group[0].start_date;
        var ends = group.map(function (task) { return parseDate(task.end_date); }).filter(Boolean);
        if (ends.length) {
          parent.end_date = isoDate(new Date(Math.max.apply(null, ends.map(Number))));
        }
      });
    }

    // Parent references must expose their child-derived dates before another
    // task evaluates an After/With/Before condition against that parent.
    aggregateParentSpans();

    // Apply persisted PM schedule conditions. Multiple passes resolve chains.
    for (var pass = 0; pass < tasks.length; pass++) {
      var changed = false;
      aggregateParentSpans();
      var dependencyGroups = {};
      (state.chart.dependencies || []).forEach(function (dep) {
        var key = dep.successor_id + "|" + dep.dependency_type;
        (dependencyGroups[key] || (dependencyGroups[key] = [])).push(dep);
      });
      Object.keys(dependencyGroups).forEach(function (key) {
        var group = dependencyGroups[key];
        var successor = map[group[0].successor_id];
        if (!successor) return;
        var dependencyType = group[0].dependency_type;
        var candidateDates = group.map(function (dep) {
          var predecessor = map[dep.predecessor_id];
          if (!predecessor) return null;
          var lag = Number(dep.lag_value) || 0;
          if (dependencyType === "FS" && predecessor.end_date) {
            return shiftWorkingDays(parseDate(predecessor.end_date), 1 + lag);
          }
          if (dependencyType === "SS" && predecessor.start_date) {
            return shiftWorkingDays(parseDate(predecessor.start_date), lag);
          }
          if (dependencyType === "FF" && predecessor.end_date) {
            return shiftWorkingDays(parseDate(predecessor.end_date), lag);
          }
          if (dependencyType === "SF" && predecessor.start_date) {
            return shiftWorkingDays(parseDate(predecessor.start_date), lag);
          }
          return null;
        }).filter(Boolean);
        if (!candidateDates.length) return;
        // After/parallel completion use the latest referenced value.
        // Before uses the earliest referenced start, matching CC Run Conditions.
        var resolvedDate = new Date(
          (dependencyType === "SF" ? Math.min : Math.max).apply(
            null, candidateDates.map(Number)
          )
        );
        var nextStart = successor.start_date;
        var nextEnd = successor.end_date;
        if (dependencyType === "FS" || dependencyType === "SS") {
          nextStart = isoDate(resolvedDate);
        } else {
          nextEnd = isoDate(resolvedDate);
          nextStart = isoDate(shiftWorkingDays(parseDate(nextEnd), -(Math.max(1, Number(successor.duration_value) || 1) - 1)));
        }
        var previousStart = successor.start_date;
        var previousEnd = successor.end_date;
        successor.start_date = nextStart;
        if (dependencyType === "FS" || dependencyType === "SS") {
          if (children[successor.task_id] && children[successor.task_id].length) {
            // A parent has no independent finish. Moving it moves its first
            // child, then its finish is re-derived from the last child.
            var firstChild = children[successor.task_id][0];
            firstChild.start_date = nextStart;
            firstChild.end_date = calculateEnd(firstChild);
            aggregateParentSpans();
          } else {
            successor.end_date = calculateEnd(successor);
          }
        } else {
          successor.end_date = nextEnd;
        }
        if (previousStart !== successor.start_date || previousEnd !== successor.end_date) {
          changed = true;
        }
      });
      tasks.forEach(function (task) {
        var config = scheduleConfig(task);
        var ref = map[config.duration_equal_to];
        if (ref && task.duration_value !== ref.duration_value) {
          task.duration_value = ref.duration_value;
          task.end_date = calculateEnd(task);
          changed = true;
        }
      });
      if (!changed) break;
    }

    // Keep final displayed parent bars synchronized with their descendants.
    aggregateParentSpans();
  }

  function request(path, options) {
    options = options || {};
    options.headers = Object.assign(
      { "Content-Type": "application/json" },
      options.headers || {}
    );
    return fetch(API + path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body.error || body.detail || ("HTTP " + response.status));
        return body;
      });
    });
  }

  function setStatus(message, type) {
    if (!state.root) return;
    var node = state.root.querySelector("#vtPmStatus");
    if (!node) return;
    node.className = "vtPmStatus " + (message ? "show " + (type || "info") : "");
    node.textContent = message || "";
  }

  function markDirty() {
    state.dirty = true;
    setStatus("Unsaved project changes", "warning");
  }

  function projectOptions() {
    var options = '<option value="">Select PM project...</option>';
    state.projects.forEach(function (project) {
      var selected = project.project_id === state.projectId ? " selected" : "";
      options += '<option value="' + escapeHtml(project.project_id) + '"' + selected + ">" +
        escapeHtml(project.project_number + " - " + project.title) + "</option>";
    });
    return options;
  }

  function shellHtml() {
    return [
      '<div class="vtPmWorkspace">',
        '<div class="vtPmToolbar">',
          '<div class="vtPmToolGroup"><div class="vtPmToolGroupTitle">Structure</div><div class="vtPmToolPanel vtPmStructurePanel">',
            '<div><button class="vtPmBtn vtPmBtnAccept" data-action="add-above">↑&nbsp; Add Above</button><button class="vtPmBtn vtPmBtnAccept" data-action="add-below">↓&nbsp; Add Below</button></div>',
            '<div><button class="vtPmBtn vtPmBtnAccept" data-action="add-subtask">⌘&nbsp; New Process</button><button class="vtPmBtn vtPmBtnReject" data-action="delete-selected">⌫&nbsp; Delete Process</button></div>',
            '<input id="vtPmTaskQuantity" class="vtPmQuantity" type="number" min="1" max="100" step="1" value="1" aria-label="Number of tasks">',
          '</div></div>',
          '<div class="vtPmToolGroup"><div class="vtPmToolGroupTitle">Schedule</div><div class="vtPmToolPanel vtPmSchedulePanel">',
            '<button class="vtPmPhaseButton" data-schedule-phase="start"><strong>Start</strong><span>Condition…</span></button>',
            '<button class="vtPmPhaseButton" data-schedule-phase="duration"><strong>Duration</strong><span>Condition…</span></button>',
            '<button class="vtPmPhaseButton" data-schedule-phase="end"><strong>End</strong><span>Condition…</span></button>',
          '</div></div>',
          '<div class="vtPmToolGroup"><div class="vtPmToolGroupTitle">Copy/Paste</div><div class="vtPmToolPanel vtPmCommandPanel">',
            '<button class="vtPmIconCommand" data-action="copy">▣<small>Copy</small></button>',
            '<button class="vtPmIconCommand" data-action="cut">✂<small>Cut</small></button>',
            '<button class="vtPmIconCommand" data-action="paste-child">⌘<small>Paste Child</small></button>',
            '<button class="vtPmIconCommand" data-action="paste-above">↑<small>Paste Above</small></button>',
            '<button class="vtPmIconCommand" data-action="paste-below">↓<small>Paste Below</small></button>',
            '<button class="vtPmIconCommand" data-action="confirm-copy">✓<small>Confirm</small></button>',
          '</div></div>',
          '<div class="vtPmToolGroup"><div class="vtPmToolGroupTitle">Rearrange</div><div class="vtPmToolPanel vtPmCommandPanel">',
            '<button class="vtPmIconCommand" disabled>⇤<small>Hierarchy</small></button><button class="vtPmIconCommand" disabled>↕<small>Order</small></button>',
          '</div></div>',
          '<div class="vtPmToolGroup"><div class="vtPmToolGroupTitle">Tools</div><div class="vtPmToolPanel vtPmCommandPanel">',
            '<button class="vtPmIconCommand" data-action="edit-selected">▦<small>More Spec</small></button><button class="vtPmIconCommand" data-action="new-resource">♟<small>Resources</small></button><button class="vtPmIconCommand" data-action="check-conflicts">⚠<small>Check</small></button><button class="vtPmIconCommand" data-action="history">◷<small>History</small></button><button class="vtPmIconCommand" data-action="issues">⚑<small>Open Issues</small></button>',
          '</div></div>',
          '<div class="vtPmToolGroup vtPmProjectGroup"><div class="vtPmToolGroupTitle">Project</div><div class="vtPmToolPanel vtPmProjectPanel">',
            '<select id="vtPmProjectSelect" class="vtPmProjectSelect" aria-label="PM project">', projectOptions(), '</select>',
            '<div><button class="vtPmBtn" data-action="new-project">New Project</button><button class="vtPmBtn vtPmBtnAccept" data-action="save">Save Project</button></div>',
          '</div></div>',
          '<span class="vtPmToolbarSpacer"></span>',
        '</div>',
        '<div id="vtPmEmpty" class="vtPmEmpty"></div>',
        '<div id="vtPmBoard" class="vtPmBoard"></div>',
        '<div id="vtPmStatus" class="vtPmStatus"></div>',
      '</div>'
    ].join("");
  }

  function renderEmpty() {
    var empty = state.root.querySelector("#vtPmEmpty");
    var board = state.root.querySelector("#vtPmBoard");
    if (state.chart) {
      empty.style.display = "none";
      board.classList.add("show");
      return;
    }
    board.classList.remove("show");
    empty.style.display = "flex";
    empty.innerHTML = [
      '<div class="vtPmEmptyCard">',
        '<h2>Start an internal project plan</h2>',
        '<p>Create a PM project to schedule the engineering, purchasing, build, installation, and commissioning work required to deliver a customer production line.</p>',
        '<button class="vtPmBtn vtPmBtnPrimary" data-action="new-project">Create the first PM project</button>',
      '</div>'
    ].join("");
  }

  function taskMap() {
    var map = {};
    (state.chart && state.chart.tasks || []).forEach(function (task) { map[task.task_id] = task; });
    return map;
  }

  function taskNumberMap(tasks) {
    var numbers = {};
    var children = {};
    var known = {};
    (tasks || []).forEach(function (task) { known[task.task_id] = true; });
    (tasks || []).forEach(function (task) {
      var parentId = task.parent_id && known[task.parent_id] ? task.parent_id : "";
      (children[parentId] || (children[parentId] = [])).push(task);
    });
    function numberChildren(parentId, prefix) {
      (children[parentId] || []).forEach(function (task, index) {
        var number = prefix ? prefix + "-" + (index + 1) : "T" + (index + 1);
        numbers[task.task_id] = number;
        numberChildren(task.task_id, number);
      });
    }
    numberChildren("", "");
    return numbers;
  }

  function assignmentMap() {
    var map = {};
    (state.chart && state.chart.assignments || []).forEach(function (assignment) {
      map[assignment.task_id] = assignment;
    });
    return map;
  }

  function dependencyMap() {
    var map = {};
    (state.chart && state.chart.dependencies || []).forEach(function (dependency) {
      (map[dependency.successor_id] || (map[dependency.successor_id] = [])).push(dependency);
    });
    return map;
  }

  function taskDepth(task, map) {
    var depth = 0;
    var parent = task.parent_id;
    var seen = {};
    while (parent && map[parent] && !seen[parent] && depth < 7) {
      seen[parent] = true;
      depth++;
      parent = map[parent].parent_id;
    }
    return depth;
  }

  function chartBounds(tasks) {
    var dates = [];
    tasks.forEach(function (task) {
      var start = parseDate(task.start_date);
      var end = parseDate(task.end_date || calculateEnd(task));
      if (start) dates.push(start);
      if (end) dates.push(end);
    });
    var today = parseDate(isoDate(new Date()));
    var min = dates.length ? new Date(Math.min.apply(null, dates.map(Number))) : today;
    var max = dates.length ? new Date(Math.max.apply(null, dates.map(Number))) : addCalendarDays(today, 20);
    var sourceMin = new Date(min.getTime());
    var sourceMax = new Date(max.getTime());
    min = addCalendarDays(min, -2);
    max = addCalendarDays(max, 5);
    return {
      min: min,
      max: max,
      days: Math.max(14, Math.round((max - min) / DAY_MS) + 1),
      sourceMin: sourceMin,
      sourceMax: sourceMax
    };
  }

  function timelineMonths(bounds) {
    var months = [];
    var rangeMin = bounds.sourceMin || bounds.min;
    var rangeMax = bounds.sourceMax || bounds.max;
    var cursor = new Date(Date.UTC(rangeMin.getUTCFullYear(), rangeMin.getUTCMonth(), 1));
    var limit = new Date(Date.UTC(rangeMax.getUTCFullYear(), rangeMax.getUTCMonth(), 1));
    while (cursor <= limit) {
      months.push({
        key: cursor.getUTCFullYear() + "-" + String(cursor.getUTCMonth() + 1).padStart(2, "0"),
        label: cursor.toLocaleString("en", { month: "long", year: "numeric", timeZone: "UTC" }),
        start: new Date(cursor.getTime()),
        end: new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0))
      });
      cursor = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
    }
    return months;
  }

  function selectedTimelineBounds(fullBounds) {
    var months = timelineMonths(fullBounds);
    if (state.monthView === "all" || !months.length) return fullBounds;
    var selected = state.monthView === "first"
      ? months[0]
      : state.monthView === "last"
        ? months[months.length - 1]
        : months.find(function (month) { return month.key === state.monthView; });
    if (!selected) return fullBounds;
    return {
      min: selected.start,
      max: selected.end,
      days: Math.round((selected.end - selected.start) / DAY_MS) + 1
    };
  }

  function monthViewOptions(fullBounds) {
    var months = timelineMonths(fullBounds);
    var options = [
      ["all", "All months"],
      ["first", "First month"],
      ["last", "Last month"]
    ].concat(months.map(function (month) { return [month.key, month.label]; }));
    return options.map(function (option) {
      return '<option value="' + option[0] + '"' + (state.monthView === option[0] ? " selected" : "") +
        ">" + escapeHtml(option[1]) + "</option>";
    }).join("");
  }

  function timelineScaleMode(bounds) {
    if (state.timeScale === "days") return "day";
    if (state.timeScale === "weeks") return "week";
    if (state.timeScale === "months") return "month";
    if (state.timeScale === "years") return "year";
    if (bounds.days <= 45) return state.dayWidth < 24 ? "week" : "day";
    if (bounds.days <= 120 && state.dayWidth >= 36) return "day";
    if (bounds.days <= 240 && state.dayWidth >= 22) return "week";
    if (bounds.days <= 370 && state.dayWidth >= 30) return "week";
    return "month";
  }

  function isoWeekNumber(date) {
    var target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    var dayNumber = target.getUTCDay() || 7;
    target.setUTCDate(target.getUTCDate() + 4 - dayNumber);
    var yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
    return Math.ceil((((target - yearStart) / DAY_MS) + 1) / 7);
  }

  function ganttAxis(bounds) {
    var mode = timelineScaleMode(bounds);
    var html = '<div class="vtPmGanttAxis" style="width:' + (bounds.days * state.dayWidth) + 'px">';
    if (mode === "day") {
      for (var i = 0; i < bounds.days; i++) {
        var day = addCalendarDays(bounds.min, i);
        var weekend = day.getUTCDay() === 0 || day.getUTCDay() === 6;
        html += '<div class="vtPmGanttDay' + (weekend ? " weekend" : "") + '" style="width:' +
          state.dayWidth + 'px">' + (day.getUTCMonth() + 1) + "/" + day.getUTCDate() + "</div>";
      }
    } else if (mode === "week") {
      for (var weekStartIndex = 0; weekStartIndex < bounds.days; weekStartIndex += 7) {
        var weekStart = addCalendarDays(bounds.min, weekStartIndex);
        var weekDays = Math.min(7, bounds.days - weekStartIndex);
        html += '<div class="vtPmGanttPeriod vtPmGanttWeek" style="width:' +
          (weekDays * state.dayWidth) + 'px"><strong>W' + isoWeekNumber(weekStart) +
          '</strong><span>' + (weekStart.getUTCMonth() + 1) + "/" + weekStart.getUTCDate() + "</span></div>";
      }
    } else if (mode === "month") {
      var monthSegments = [];
      var monthIndex = 0;
      while (monthIndex < bounds.days) {
        var monthStart = addCalendarDays(bounds.min, monthIndex);
        var monthNumber = monthStart.getUTCMonth();
        var monthYear = monthStart.getUTCFullYear();
        var monthDays = 0;
        while (monthIndex + monthDays < bounds.days) {
          var monthDay = addCalendarDays(bounds.min, monthIndex + monthDays);
          if (monthDay.getUTCMonth() !== monthNumber || monthDay.getUTCFullYear() !== monthYear) break;
          monthDays++;
        }
        monthSegments.push({
          start: monthStart,
          days: monthDays,
          label: monthStart.toLocaleString("en", { month: "short", timeZone: "UTC" }),
          year: monthYear
        });
        monthIndex += monthDays;
      }
      var minimumMonthLabelWidth = 88;
      for (var segmentIndex = 0; segmentIndex < monthSegments.length;) {
        var group = [];
        var groupDays = 0;
        while (segmentIndex < monthSegments.length &&
               (group.length === 0 || groupDays * state.dayWidth < minimumMonthLabelWidth)) {
          group.push(monthSegments[segmentIndex]);
          groupDays += monthSegments[segmentIndex].days;
          segmentIndex++;
        }
        var firstMonth = group[0];
        var lastMonth = group[group.length - 1];
        var groupLabel = firstMonth.label;
        if (group.length > 1) groupLabel += "–" + lastMonth.label;
        groupLabel += firstMonth.year === lastMonth.year
          ? " " + firstMonth.year
          : " " + firstMonth.year + "–" + lastMonth.year;
        html += '<div class="vtPmGanttPeriod vtPmGanttMonth" style="width:' +
          (groupDays * state.dayWidth) + 'px">' + escapeHtml(groupLabel) + "</div>";
      }
    } else {
      var yearIndex = 0;
      while (yearIndex < bounds.days) {
        var yearStart = addCalendarDays(bounds.min, yearIndex);
        var year = yearStart.getUTCFullYear();
        var yearDays = 0;
        while (yearIndex + yearDays < bounds.days) {
          var yearDay = addCalendarDays(bounds.min, yearIndex + yearDays);
          if (yearDay.getUTCFullYear() !== year) break;
          yearDays++;
        }
        html += '<div class="vtPmGanttPeriod vtPmGanttYear" style="width:' +
          (yearDays * state.dayWidth) + 'px">' + year + "</div>";
        yearIndex += yearDays;
      }
    }
    return html + "</div>";
  }

  function renderBoard() {
    if (!state.chart) return renderEmpty();
    var tasks = state.chart.tasks || [];
    recalculateSchedule();
    var map = taskMap();
    var taskNumbers = taskNumberMap(tasks);
    var parentIds = {};
    var successorsByPredecessor = {};
    tasks.forEach(function (task) {
      if (task.parent_id) parentIds[task.parent_id] = true;
    });
    (state.chart.dependencies || []).forEach(function (dependency) {
      if (dependency.dependency_type === "FS" && !(Number(dependency.lag_value) || 0)) {
        (successorsByPredecessor[dependency.predecessor_id] ||
          (successorsByPredecessor[dependency.predecessor_id] = [])).push(dependency.successor_id);
      }
    });
    var assignments = assignmentMap();
    var resources = {};
    state.resources.forEach(function (resource) { resources[resource.resource_id] = resource; });
    var openIssueCounts = {};
    var resolvedIssueCounts = {};
    (state.issues || []).forEach(function (issue) {
      if (issue.status === "Resolved" || issue.status === "Closed") {
        resolvedIssueCounts[issue.task_id] = (resolvedIssueCounts[issue.task_id] || 0) + 1;
      } else {
        openIssueCounts[issue.task_id] = (openIssueCounts[issue.task_id] || 0) + 1;
      }
    });
    var conflictTasks = {};
    state.conflicts.forEach(function (warning) {
      (warning.task_ids || []).forEach(function (id) { conflictTasks[id] = true; });
    });
    var fullBounds = chartBounds(tasks);
    var bounds = selectedTimelineBounds(fullBounds);
    var scaleMode = timelineScaleMode(bounds);
    var verticalDayLabels = scaleMode === "day" && state.dayWidth < 36;
    var today = parseDate(isoDate(new Date()));
    var todayLeft = today && today >= bounds.min && today <= bounds.max
      ? (Math.round((today - bounds.min) / DAY_MS) + 0.5) * state.dayWidth
      : null;
    var timelineWidth = Math.max(430, bounds.days * state.dayWidth);
    var totalGridWidth = state.columnWidths.index + state.columnWidths.title + timelineWidth +
      state.columnWidths.start + state.columnWidths.duration + state.columnWidths.finish +
      state.columnWidths.progress + state.columnWidths.issues + state.columnWidths.resource;
    var project = state.chart.project || {};
    var totalProgress = tasks.length
      ? Math.round(tasks.reduce(function (sum, task) { return sum + Number(task.progress_percent || 0); }, 0) / tasks.length)
      : 0;
    var board = state.root.querySelector("#vtPmBoard");
    board.innerHTML = [
      '<div class="vtPmScaleToolbar">',
        '<label class="vtPmTimeScaleSelector">Display:<select id="vtPmTimeScale">',
          '<option value="auto"', state.timeScale === "auto" ? " selected" : "", '>Auto</option>',
          '<option value="days"', state.timeScale === "days" ? " selected" : "", '>Days</option>',
          '<option value="weeks"', state.timeScale === "weeks" ? " selected" : "", '>Weeks</option>',
          '<option value="months"', state.timeScale === "months" ? " selected" : "", '>Months</option>',
          '<option value="years"', state.timeScale === "years" ? " selected" : "", '>Years</option>',
        '</select></label>',
        '<span>Scale:</span><input id="vtPmScale" type="range" min="1" max="70" step="1" value="', state.dayWidth, '">',
        '<strong>', state.dayWidth, ' px/day</strong><span class="vtPmScaleMode">', scaleMode.charAt(0).toUpperCase() + scaleMode.slice(1), ' view</span><span class="vtPmScaleSeparator"></span>',
        '<button class="vtPmScaleBtn" data-action="expand-all" title="Expand All">⊞</button><button class="vtPmScaleBtn" data-action="collapse-all" title="Collapse All">⊟</button>',
        '<label class="vtPmMonthSelector">Month:<select id="vtPmMonthView">', monthViewOptions(fullBounds), "</select></label>",
        '<span><strong>', escapeHtml(project.project_number || ""), "</strong> ", escapeHtml(project.title || ""), "</span>",
        '<span>Start: <strong>', escapeHtml(project.start_date || "Not set"), "</strong></span>",
        '<span>Tasks: <strong>', tasks.length, "</strong></span>",
        '<span>Average progress: <strong>', totalProgress, "%</strong></span>",
        '<span>Calendar: <strong>Working days (Mon-Fri)</strong></span>',
      '</div>',
      '<div class="vtPmGridWrap"><div class="vtPmGrid" style="width:', totalGridWidth,
        'px;--vt-pm-day-width:', state.dayWidth,
        'px;--pm-timeline-width:', timelineWidth, 'px',
        ';--pm-index-width:', state.columnWidths.index, 'px;--pm-title-width:', state.columnWidths.title,
        'px;--pm-start-width:', state.columnWidths.start, 'px;--pm-duration-width:', state.columnWidths.duration,
        'px;--pm-finish-width:', state.columnWidths.finish, 'px;--pm-progress-width:', state.columnWidths.progress,
        'px;--pm-issues-width:', state.columnWidths.issues,
        'px;--pm-resource-width:', state.columnWidths.resource, 'px">',
        '<div class="vtPmGridHeader', verticalDayLabels ? " vtPmVerticalDates" : "", '">',
          '<div class="vtPmCell vtPmIndex">Task<span class="vtPmColumnResizer" data-resize-column="index"></span></div>',
          '<div class="vtPmCell">Task title<span class="vtPmColumnResizer" data-resize-column="title"></span></div>',
          '<div class="vtPmCell vtPmGanttHead vtPmScale-', scaleMode, '">', ganttAxis(bounds),
            todayLeft != null
              ? '<span class="vtPmTodayLine vtPmTodayLineHeader" style="left:' + todayLeft + 'px"><em>Today</em></span>'
              : "",
          "</div>",
          '<div class="vtPmCell">Start<span class="vtPmColumnResizer" data-resize-column="start"></span></div>',
          '<div class="vtPmCell">Duration<span class="vtPmColumnResizer" data-resize-column="duration"></span></div>',
          '<div class="vtPmCell">Finish<span class="vtPmColumnResizer" data-resize-column="finish"></span></div>',
          '<div class="vtPmCell">Progress<span class="vtPmColumnResizer" data-resize-column="progress"></span></div>',
          '<div class="vtPmCell">Issues<span class="vtPmColumnResizer" data-resize-column="issues"></span></div>',
          '<div class="vtPmCell">Internal resource<span class="vtPmColumnResizer" data-resize-column="resource"></span></div>',
        "</div>",
        tasks.filter(function (task) {
          var parent = map[task.parent_id];
          while (parent) {
            if (state.collapsedTaskIds[parent.task_id]) return false;
            parent = map[parent.parent_id];
          }
          return true;
        }).map(function (task) {
          var index = tasks.indexOf(task);
          var start = parseDate(task.start_date);
          var end = parseDate(task.end_date);
          var left = start ? Math.round((start - bounds.min) / DAY_MS) * state.dayWidth : 0;
          var durationValue = Math.max(0, Number(task.duration_value) || 0);
          var progress = Math.max(0, Math.min(100, Number(task.progress_percent) || 0));
          var isOverdue = !!(today && end && end < today && progress < 100);
          var trailingUnusedDay = task.duration_unit === "working_days"
            ? (durationValue === 0 ? 1 : Math.max(0, Math.ceil(durationValue) - durationValue))
            : 0;
          var width = start && end
            ? Math.max(
                4,
                (Math.round((end - start) / DAY_MS) + 1 - trailingUnusedDay) * state.dayWidth
              )
            : 0;
          var weekendSegments = [];
          var weekendContinuationDays = 0;
          if (start && end && !parentIds[task.task_id]) {
            for (var barDay = new Date(start.getTime()), dayIndex = 0;
                 barDay <= end;
                 barDay = addCalendarDays(barDay, 1), dayIndex++) {
              if (barDay.getUTCDay() === 0 || barDay.getUTCDay() === 6) {
                weekendSegments.push(
                  '<span class="vtPmWeekendBarSegment" style="left:' +
                  (dayIndex * state.dayWidth) + 'px;width:' + state.dayWidth + 'px"></span>'
                );
              }
            }
            // For a zero-lag Finish-to-Start link ending before a weekend,
            // continue the same visual bar through the non-working days so
            // the Monday successor does not appear disconnected.
            var linkedStarts = (successorsByPredecessor[task.task_id] || []).map(function (successorId) {
              return parseDate(map[successorId] && map[successorId].start_date);
            }).filter(Boolean);
            if (linkedStarts.length) {
              var nearestSuccessorStart = new Date(Math.min.apply(null, linkedStarts.map(Number)));
              for (var gapDay = addCalendarDays(end, 1), gapIndex = 0;
                   gapDay < nearestSuccessorStart;
                   gapDay = addCalendarDays(gapDay, 1), gapIndex++) {
                if (gapDay.getUTCDay() === 0 || gapDay.getUTCDay() === 6) {
                  weekendContinuationDays = Math.max(weekendContinuationDays, gapIndex + 1);
                  weekendSegments.push(
                    '<span class="vtPmWeekendBarSegment vtPmWeekendContinuation" style="left:' +
                    (width + gapIndex * state.dayWidth) + 'px;width:' + state.dayWidth + 'px"></span>'
                  );
                }
              }
            }
          }
          var assignment = assignments[task.task_id];
          var resource = assignment && resources[assignment.resource_id];
          var titleLevel = taskDepth(task, map) + 1;
          var barColor = titleLevel === 1 ? "brown" : (task.color || "#0a6ed1");
          var barTitle = task.title + ": " + task.start_date + " to " + task.end_date +
            " (" + progress + "% complete)";
          var ancestorMarkers = [];
          var ancestor = map[task.parent_id];
          while (ancestor) {
            var ancestorEnd = parseDate(ancestor.end_date);
            if (ancestorEnd) {
              ancestorMarkers.push(
                Math.max(0, (Math.round((ancestorEnd - bounds.min) / DAY_MS) + 1) * state.dayWidth)
              );
            }
            ancestor = map[ancestor.parent_id];
          }
          return [
            '<div class="vtPmTaskRow', conflictTasks[task.task_id] ? " vtPmConflict" : "",
              state.selectedTaskIds.indexOf(task.task_id) >= 0 ? " vtPmSelected" : "",
              state.pendingScheduleCondition && state.pendingScheduleCondition.taskId === task.task_id ? " vtPmConditionSource" : "",
              state.pendingScheduleCondition && state.pendingScheduleCondition.referenceIds.indexOf(task.task_id) >= 0 ? " vtPmConditionValue" : "",
              isOverdue ? " vtPmOverdue" : "",
              '" data-task-id="', escapeHtml(task.task_id), '">',
              '<div class="vtPmCell vtPmIndex">', escapeHtml(taskNumbers[task.task_id] || "T" + (index + 1)), "</div>",
              '<div class="vtPmCell vtPmTaskTitle" data-depth="', titleLevel - 1, '">',
                parentIds[task.task_id]
                  ? '<button class="vtPmTreeToggle" data-tree-toggle="' + escapeHtml(task.task_id) +
                    '" title="' + (state.collapsedTaskIds[task.task_id] ? "Expand" : "Collapse") + '">' +
                    (state.collapsedTaskIds[task.task_id] ? "▸" : "▾") + "</button>"
                  : '<span class="vtPmTreeToggleSpacer"></span>',
                task.task_type === "milestone" ? '<span class="vtPmMilestoneIcon" title="Milestone"></span>' : "",
                '<span class="vtPmTaskTitleText vtPmTitleLevel', Math.min(titleLevel, 8),
                  task.title.indexOf("(**copy)") >= 0 ? " vtPmCopiedTitle" : "", '">',
                  escapeHtml(task.title), "</span>",
              "</div>",
              '<div class="vtPmCell vtPmGanttCell vtPmScale-', scaleMode, '" style="width:', bounds.days * state.dayWidth, 'px">',
                todayLeft != null
                  ? '<span class="vtPmTodayLine" style="left:' + todayLeft + 'px"></span>'
                  : "",
                ancestorMarkers.map(function (markerLeft) {
                  return '<span class="vtPmParentEndGuide" style="left:' + markerLeft + 'px"></span>';
                }).join(""),
                width ? '<div class="vtPmBar' + (parentIds[task.task_id] ? ' vtPmParentBar' : '') +
                  '" style="left:' + left + 'px;width:' + width + 'px;--vt-pm-bar-color:' +
                  escapeHtml(barColor) + ';--vt-pm-label-offset:' +
                  (5 + weekendContinuationDays * state.dayWidth) + 'px" aria-label="' + escapeHtml(barTitle) + '">' +
                  weekendSegments.join("") +
                  '<span class="vtPmBarProgressLabel' + (progress >= 100 ? ' vtPmBarComplete' : '') +
                    '" aria-label="' + (progress >= 100 ? 'Completed' : progress + '% complete') + '">' +
                    (progress >= 100 ? '✓' : progress + '%') + '</span></div>' : "",
              "</div>",
              '<div class="vtPmCell">', escapeHtml(task.start_date || "—"), "</div>",
              '<div class="vtPmCell">', escapeHtml(task.duration_value), " working day", Number(task.duration_value) === 1 ? "" : "s", "</div>",
              '<div class="vtPmCell', isOverdue ? " vtPmOverdueFinish" : "", '">', escapeHtml(task.end_date || "—"),
                isOverdue ? '<span class="vtPmOverdueBadge" title="Incomplete after due date">Overdue</span>' : "",
              "</div>",
              '<div class="vtPmCell vtPmProgressCell"><span class="vtPmProgressTrack"><span class="vtPmProgressFill" style="width:', progress,
                '%"></span></span><strong>', progress, "%</strong>",
                isOverdue ? '<span class="vtPmOverdueIcon" title="Incomplete after due date">!</span>' : "",
              "</div>",
              '<div class="vtPmCell vtPmIssueCell">',
                openIssueCounts[task.task_id]
                  ? '<button type="button" class="vtPmIssueCount" data-task-issues="' +
                    escapeHtml(task.task_id) + '" title="Open ' + openIssueCounts[task.task_id] +
                    ' related issue(s)">⚑ ' + openIssueCounts[task.task_id] + "</button>"
                  : "",
                resolvedIssueCounts[task.task_id]
                  ? '<button type="button" class="vtPmIssueCount vtPmIssueResolvedCount" data-task-resolved="' +
                    escapeHtml(task.task_id) + '" title="Open ' + resolvedIssueCounts[task.task_id] +
                    ' resolved issue(s)">✓ ' + resolvedIssueCounts[task.task_id] + "</button>"
                  : "",
                !openIssueCounts[task.task_id] && !resolvedIssueCounts[task.task_id]
                  ? '<span class="vtPmMuted">—</span>' : "",
              "</div>",
              '<div class="vtPmCell">', resource ? escapeHtml(resource.name) : '<span class="vtPmMuted">Unassigned</span>', "</div>",
            "</div>"
          ].join("");
        }).join(""),
      '</div></div>',
      '<div class="vtPmHorizontalScroll" aria-label="Horizontal timeline navigation">',
        '<div class="vtPmHorizontalScrollTrack" style="width:', totalGridWidth, 'px"></div>',
      "</div>"
    ].join("");
    renderEmpty();
    var gridWrap = board.querySelector(".vtPmGridWrap");
    var horizontalScroll = board.querySelector(".vtPmHorizontalScroll");
    var synchronizingScroll = false;
    if (gridWrap && horizontalScroll) {
      gridWrap.addEventListener("scroll", function () {
        if (synchronizingScroll) return;
        synchronizingScroll = true;
        horizontalScroll.scrollLeft = gridWrap.scrollLeft;
        synchronizingScroll = false;
      });
      horizontalScroll.addEventListener("scroll", function () {
        if (synchronizingScroll) return;
        synchronizingScroll = true;
        gridWrap.scrollLeft = horizontalScroll.scrollLeft;
        synchronizingScroll = false;
      });
    }
  }

  function renderProjectSelect() {
    var select = state.root.querySelector("#vtPmProjectSelect");
    if (select) select.innerHTML = projectOptions();
  }

  function modal(title, body, onSave, saveText, onCancel) {
    var wrapper = document.createElement("div");
    wrapper.className = "vtPmModalBackdrop";
    wrapper.innerHTML = [
      '<div class="vtPmDialog" role="dialog" aria-modal="true">',
        '<div class="vtPmDialogHeader">', escapeHtml(title), "</div>",
        '<div class="vtPmDialogBody">', body, "</div>",
        '<div class="vtPmDialogFooter">',
          '<button class="vtPmBtn" data-modal-cancel>Cancel</button>',
          '<button class="vtPmBtn vtPmBtnPrimary" data-modal-save>', escapeHtml(saveText || "Save"), "</button>",
        "</div>",
      "</div>"
    ].join("");
    document.body.appendChild(wrapper);
    function close() { wrapper.remove(); }
    function cancel() {
      if (onCancel) onCancel();
      close();
    }
    wrapper.querySelector("[data-modal-cancel]").addEventListener("click", cancel);
    wrapper.addEventListener("click", function (event) {
      if (event.target === wrapper) cancel();
    });
    wrapper.querySelector("[data-modal-save]").addEventListener("click", function () {
      Promise.resolve(onSave(wrapper)).then(function (shouldClose) {
        if (shouldClose !== false) close();
      }).catch(function (error) {
        setStatus(error.message, "error");
      });
    });
    return wrapper;
  }

  function ensureTaskTooltip() {
    var tooltip = document.getElementById("vtPmTaskTooltip");
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.id = "vtPmTaskTooltip";
      document.body.appendChild(tooltip);
    }
    return tooltip;
  }

  function positionTaskTooltip(event) {
    var tooltip = document.getElementById("vtPmTaskTooltip");
    if (!tooltip || tooltip.style.display === "none") return;
    var x = event.clientX + 16;
    var y = event.clientY + 16;
    if (x + tooltip.offsetWidth > window.innerWidth - 10) {
      x = event.clientX - tooltip.offsetWidth - 16;
    }
    if (y + tooltip.offsetHeight > window.innerHeight - 10) {
      y = event.clientY - tooltip.offsetHeight - 16;
    }
    tooltip.style.left = Math.max(6, x) + "px";
    tooltip.style.top = Math.max(6, y) + "px";
  }

  function showTaskTooltip(taskId, event) {
    var map = taskMap();
    var task = map[taskId];
    if (!task) return;
    var numbers = taskNumberMap(state.chart.tasks || []);
    var assignment = assignmentMap()[taskId];
    var resource = assignment && state.resources.find(function (item) {
      return item.resource_id === assignment.resource_id;
    });
    var conditionLabels = { FS: "Start after", SS: "Start with", SF: "End before", FF: "End with" };
    var conditions = (state.chart.dependencies || []).filter(function (dependency) {
      return dependency.successor_id === taskId;
    }).map(function (dependency) {
      var reference = map[dependency.predecessor_id];
      var referenceText = reference
        ? ((numbers[reference.task_id] || "") + " " + reference.title).trim()
        : dependency.predecessor_id;
      var lag = Number(dependency.lag_value) || 0;
      return '<div class="vtPmTooltipCondition"><strong>' +
        escapeHtml(conditionLabels[dependency.dependency_type] || dependency.dependency_type) +
        ':</strong><em>' + escapeHtml(referenceText) +
        (lag ? " (" + (lag > 0 ? "+" : "") + lag + " working days)" : "") +
        "</em></div>";
    });
    var config = scheduleConfig(task);
    if (config.start_from_parent) {
      var parent = map[task.parent_id];
      conditions.push('<div class="vtPmTooltipCondition"><strong>Start from parent:</strong><em>' +
        escapeHtml(parent ? ((numbers[parent.task_id] || "") + " " + parent.title).trim() : "Parent task") +
        "</em></div>");
    }
    if (config.duration_equal_to) {
      var durationReference = map[config.duration_equal_to];
      conditions.push('<div class="vtPmTooltipCondition"><strong>Duration equal to:</strong><em>' +
        escapeHtml(durationReference
          ? ((numbers[durationReference.task_id] || "") + " " + durationReference.title).trim()
          : config.duration_equal_to) + "</em></div>");
    }
    var progress = Math.max(0, Math.min(100, Number(task.progress_percent) || 0));
    var tooltip = ensureTaskTooltip();
    tooltip.innerHTML = [
      '<div class="vtPmTooltipCard">',
        '<div class="vtPmTooltipHeader"><strong>', escapeHtml((numbers[taskId] || "") + "> " + task.title), "</strong></div>",
        '<div class="vtPmTooltipBody">',
          '<div><strong>Start:</strong><em>', escapeHtml(task.start_date || "—"), "</em></div>",
          '<div><strong>End:</strong><em>', escapeHtml(task.end_date || "—"), "</em></div>",
          '<div><strong>Task Duration:</strong><em>', escapeHtml(task.duration_value), " working day", Number(task.duration_value) === 1 ? "" : "s", "</em></div>",
          '<div><strong>Progress:</strong><em>', progress >= 100 ? "✓ Completed" : progress + "%", "</em></div>",
          '<div><strong>Resource:</strong><em>', escapeHtml(resource ? resource.name : "Unassigned"), "</em></div>",
          conditions.length ? '<div class="vtPmTooltipDivider"></div>' + conditions.join("") : "",
        "</div>",
      "</div>"
    ].join("");
    tooltip.style.display = "block";
    positionTaskTooltip(event);
  }

  function hideTaskTooltip() {
    var tooltip = document.getElementById("vtPmTaskTooltip");
    if (tooltip) tooltip.style.display = "none";
  }

  function openProjectDialog() {
    modal("Create PM project", [
      '<div class="vtPmField"><label>Project number</label><input id="pmProjectNumber" required placeholder="PM-1371"></div>',
      '<div class="vtPmField"><label>Start date</label><input id="pmProjectStart" type="date"></div>',
      '<div class="vtPmField vtPmFieldFull"><label>Project title</label><input id="pmProjectTitle" required placeholder="Build customer production line"></div>',
      '<div class="vtPmField vtPmFieldFull"><label>Related Cycle Chart project number</label><input id="pmCcProjectNumber" placeholder="1371"></div>'
    ].join(""), function (dialog) {
      var projectNumber = dialog.querySelector("#pmProjectNumber").value.trim();
      var title = dialog.querySelector("#pmProjectTitle").value.trim();
      if (!projectNumber || !title) throw new Error("Project number and title are required");
      return request("/projects", {
        method: "POST",
        body: JSON.stringify({
          project_number: projectNumber,
          title: title,
          start_date: dialog.querySelector("#pmProjectStart").value || null,
          cc_project_number: dialog.querySelector("#pmCcProjectNumber").value.trim() || null
        })
      }).then(function (project) {
        state.projects.push(project);
        state.projectId = project.project_id;
        renderProjectSelect();
        return loadChart(project.project_id);
      });
    }, "Create Project");
  }

  function selectOptions(items, valueKey, labelKey, selected, emptyText, excludedId) {
    var html = '<option value="">' + escapeHtml(emptyText || "None") + "</option>";
    items.forEach(function (item) {
      if (excludedId && item[valueKey] === excludedId) return;
      html += '<option value="' + escapeHtml(item[valueKey]) + '"' +
        (item[valueKey] === selected ? " selected" : "") + ">" +
        escapeHtml(item[labelKey]) + "</option>";
    });
    return html;
  }

  function openScheduleConditionDialog(phase) {
    if (!state.chart || !state.selectedTaskId) {
      setStatus("Select a task row first", "warning");
      return;
    }
    var task = taskMap()[state.selectedTaskId];
    if (!task) return;
    var config = scheduleConfig(task);
    var dependency = (state.chart.dependencies || []).find(function (item) {
      return item.successor_id === task.task_id;
    });
    var currentCondition = "custom";
    if (phase === "start" && dependency) currentCondition = dependency.dependency_type === "SS" ? "with_start" : "after";
    if (phase === "start" && config.start_from_parent) currentCondition = "parent_start";
    if (phase === "duration" && config.duration_equal_to) currentCondition = "equal_duration";
    if (phase === "end" && dependency) currentCondition = dependency.dependency_type === "FF" ? "with_end" : "before";
    var conditionOptions = phase === "start"
      ? [["custom", "Custom date"], ["parent_start", "From parent start"], ["after", "After…"],
        ["with_start", "With… (same start)"], ["sequential", "Sequential"], ["parallel", "Parallel"]]
      : phase === "duration"
        ? [["custom", "Custom duration"], ["equal_duration", "Equal to…"]]
        : [["custom", "Custom date"], ["before", "Before…"], ["with_end", "With… (same completion)"],
          ["sequential", "Sequential"], ["parallel", "Parallel"]];
    var options = conditionOptions.map(function (item) {
      return '<option value="' + item[0] + '"' + (item[0] === currentCondition ? " selected" : "") + ">" + item[1] + "</option>";
    }).join("");
    var scheduleDialog = modal("Schedule Conditions — " + phase.charAt(0).toUpperCase() + phase.slice(1), [
      '<div class="vtPmField"><label>Condition</label><select id="pmScheduleCondition">', options, "</select></div>",
      '<div class="vtPmField"><label>Offset (working days)</label><input id="pmScheduleOffset" type="number" step="1" value="', escapeHtml(dependency && dependency.lag_value || 0), '"></div>',
      '<div class="vtPmField"><label>', phase === "duration" ? "Duration (working days)" : "Date", '</label><input id="pmScheduleValue" type="',
        phase === "duration" ? "number" : "date", '" min="', phase === "duration" ? "0" : "", '" value="',
        escapeHtml(phase === "start" ? task.start_date : phase === "end" ? task.end_date : task.duration_value), '"></div>'
    ].join(""), function (dialog) {
      var condition = dialog.querySelector("#pmScheduleCondition").value;
      var offset = Number(dialog.querySelector("#pmScheduleOffset").value) || 0;
      var value = dialog.querySelector("#pmScheduleValue").value;
      var nextConfig = scheduleConfig(task);
      if (phase === "start") delete nextConfig.start_from_parent;
      if (phase === "duration") delete nextConfig.duration_equal_to;
      if (phase !== "duration") {
        state.chart.dependencies = (state.chart.dependencies || []).filter(function (item) {
          return item.successor_id !== task.task_id;
        });
      }
      var needsReference = condition === "after" || condition === "with_start" ||
        condition === "equal_duration" || condition === "before" || condition === "with_end" ||
        condition === "sequential" || condition === "parallel";
      if (needsReference) {
        task.schedule_config = nextConfig;
        state.pendingScheduleCondition = {
          taskId: task.task_id,
          phase: phase,
          condition: condition,
          offset: offset,
          referenceIds: state.selectedTaskIds.filter(function (id) {
            return id !== task.task_id;
          })
        };
        if (condition === "sequential" || condition === "parallel") {
          if (state.selectedTaskIds.length < 2) {
            state.pendingScheduleCondition = null;
            setStatus("Ctrl+click at least two tasks before applying " + condition + ".", "warning");
            return false;
          }
          finalizeScheduleReferences();
          return true;
        }
        setStatus(
          state.pendingScheduleCondition.referenceIds.length
            ? state.pendingScheduleCondition.referenceIds.length + " task value(s) already selected. Press Enter to apply or Ctrl+click more tasks."
            : "Schedule condition: select one or more task values, then press Enter. Press Esc to cancel.",
          "info"
        );
        renderBoard();
        state.root.setAttribute("tabindex", "-1");
        state.root.focus();
        return true;
      }
      if (phase === "start") {
        if (condition === "custom") task.start_date = value;
        if (condition === "parent_start") {
          nextConfig.start_from_parent = true;
          var parent = taskMap()[task.parent_id];
          if (parent) task.start_date = parent.start_date;
        }
      } else if (phase === "duration") {
        if (condition === "custom") task.duration_value = Math.max(0, Number(value) || 0);
      } else {
        if (condition === "custom" && value) {
          task.end_date = value;
          task.start_date = isoDate(shiftWorkingDays(parseDate(value), -(Math.max(1, Number(task.duration_value) || 1) - 1)));
        }
      }
      task.schedule_config = nextConfig;
      recalculateSchedule(task.task_id);
      markDirty();
      renderBoard();
    }, "Apply Condition");
    scheduleDialog.querySelector("#pmScheduleCondition").addEventListener("change", function (event) {
      var condition = event.target.value;
      if (condition === "after" || condition === "with_start" ||
          condition === "equal_duration" || condition === "before" || condition === "with_end" ||
          condition === "sequential" || condition === "parallel") {
        scheduleDialog.querySelector("[data-modal-save]").click();
      }
    });
  }

  function applyScheduleReference(referenceId) {
    var pending = state.pendingScheduleCondition;
    if (!pending || !state.chart) return false;
    if (referenceId === pending.taskId) {
      setStatus("Choose a different task as the schedule-condition value.", "warning");
      return true;
    }
    var index = pending.referenceIds.indexOf(referenceId);
    if (index >= 0) pending.referenceIds.splice(index, 1);
    else pending.referenceIds.push(referenceId);
    renderBoard();
    setStatus(
      pending.referenceIds.length
        ? pending.referenceIds.length + " task value(s) selected. Press Enter to apply or Esc to cancel."
        : "Select one or more task values, then press Enter.",
      "info"
    );
    return true;
  }

  function finalizeScheduleReferences() {
    var pending = state.pendingScheduleCondition;
    if (!pending || !state.chart) return;
    if (!pending.referenceIds.length) {
      setStatus("Select at least one task value before pressing Enter.", "warning");
      return;
    }
    var map = taskMap();
    var task = map[pending.taskId];
    if (!task) return;
    var referenceIds = pending.referenceIds.slice();
    if (pending.condition === "sequential" || pending.condition === "parallel") {
      var orderedIds = [pending.taskId].concat(referenceIds).filter(function (id, index, ids) {
        return ids.indexOf(id) === index;
      }).sort(function (a, b) {
        return (state.chart.tasks || []).findIndex(function (task) { return task.task_id === a; }) -
          (state.chart.tasks || []).findIndex(function (task) { return task.task_id === b; });
      });
      if (orderedIds.length < 2) {
        setStatus("Select at least two tasks for " + pending.condition + " scheduling.", "warning");
        return;
      }
      var sharedParent = (map[orderedIds[0]] || {}).parent_id || null;
      if (!orderedIds.every(function (id) { return ((map[id] || {}).parent_id || null) === sharedParent; })) {
        setStatus("Sequential and Parallel tasks must share the same parent.", "warning");
        return;
      }
      var multiDependencyType;
      if (pending.phase === "start") {
        multiDependencyType = pending.condition === "sequential" ? "FS" : "SS";
      } else {
        multiDependencyType = pending.condition === "sequential" ? "SF" : "FF";
      }
      for (var multiIndex = 1; multiIndex < orderedIds.length; multiIndex++) {
        var successorId = orderedIds[multiIndex];
        var predecessorId = pending.condition === "sequential"
          ? orderedIds[multiIndex - 1] : orderedIds[0];
        state.chart.dependencies = (state.chart.dependencies || []).filter(function (item) {
          return item.successor_id !== successorId;
        });
        state.chart.dependencies.push({
          predecessor_id: predecessorId,
          successor_id: successorId,
          dependency_type: multiDependencyType,
          lag_value: pending.offset,
          lag_unit: "working_days"
        });
      }
      state.pendingScheduleCondition = null;
      recalculateSchedule(orderedIds[0]);
      markDirty();
      renderBoard();
      setStatus(
        (pending.condition === "sequential" ? "Sequential" : "Parallel") +
        " " + pending.phase + " condition applied to " + orderedIds.length + " tasks.",
        "success"
      );
      return;
    } else if (pending.condition === "equal_duration") {
      var config = scheduleConfig(task);
      config.duration_equal_to = referenceIds[referenceIds.length - 1];
      task.schedule_config = config;
    } else {
      var dependencyType = {
        after: "FS",
        with_start: "SS",
        before: "SF",
        with_end: "FF"
      }[pending.condition];
      referenceIds.sort(function (a, b) {
        var left = map[a] || {};
        var right = map[b] || {};
        var leftDate = dependencyType === "SS" || dependencyType === "SF" ? left.start_date : left.end_date;
        var rightDate = dependencyType === "SS" || dependencyType === "SF" ? right.start_date : right.end_date;
        var result = Number(parseDate(leftDate)) - Number(parseDate(rightDate));
        return dependencyType === "SF" ? -result : result;
      });
      state.chart.dependencies = (state.chart.dependencies || []).filter(function (item) {
        return item.successor_id !== task.task_id;
      });
      referenceIds.forEach(function (referenceId) {
        state.chart.dependencies.push({
          predecessor_id: referenceId,
          successor_id: task.task_id,
          dependency_type: dependencyType,
          lag_value: pending.offset,
          lag_unit: "working_days"
        });
      });
    }
    state.pendingScheduleCondition = null;
    recalculateSchedule(task.task_id);
    markDirty();
    renderBoard();
    setStatus("Schedule condition applied using " + referenceIds.length + " task value(s).", "success");
  }

  function issueListHtml(issues) {
    if (!issues.length) return '<div class="vtPmIssueEmpty">No issues are linked to this task.</div>';
    var numbers = taskNumberMap(state.chart && state.chart.tasks || []);
    return issues.map(function (issue) {
      var taskNumber = numbers[issue.task_id] || "";
      return [
        '<button type="button" class="vtPmIssueCard" data-edit-issue="', escapeHtml(issue.issue_id), '">',
          '<span class="vtPmIssueCardTop"><strong>', escapeHtml(issue.title), '</strong>',
            '<i class="vtPmIssuePriority vtPmIssuePriority', escapeHtml(issue.priority), '">', escapeHtml(issue.priority), '</i></span>',
          '<span>', taskNumber ? escapeHtml(taskNumber) + " · " : "", escapeHtml(issue.status), issue.owner ? " · " + escapeHtml(issue.owner) : "",
            issue.due_date ? " · Due " + escapeHtml(issue.due_date) : "", '</span>',
        '</button>'
      ].join("");
    }).join("");
  }

  function openIssueDialog(task, issue, onSaved) {
    issue = issue || {};
    modal(issue.issue_id ? "Edit issue" : "Add issue", [
      '<div class="vtPmField vtPmFieldFull"><label>Issue title</label><input id="pmIssueTitle" maxlength="200" value="', escapeHtml(issue.title || ""), '" required></div>',
      '<div class="vtPmField vtPmFieldFull"><label>Description</label><textarea id="pmIssueDescription" class="vtPmReasonInput" rows="4">', escapeHtml(issue.description || ""), '</textarea></div>',
      '<div class="vtPmField"><label>Priority</label><select id="pmIssuePriority">',
        ["Low", "Medium", "High", "Critical"].map(function (value) {
          return '<option' + ((issue.priority || "Medium") === value ? " selected" : "") + ">" + value + "</option>";
        }).join(""), '</select></div>',
      '<div class="vtPmField"><label>Status</label><select id="pmIssueStatus">',
        ["Open", "In Progress", "Blocked", "Resolved", "Closed"].map(function (value) {
          return '<option' + ((issue.status || "Open") === value ? " selected" : "") + ">" + value + "</option>";
        }).join(""), '</select></div>',
      '<div class="vtPmField"><label>Owner</label><input id="pmIssueOwner" value="', escapeHtml(issue.owner || ""), '" placeholder="Person or team"></div>',
      '<div class="vtPmField"><label>Due date</label><input id="pmIssueDueDate" type="date" value="', escapeHtml(issue.due_date || ""), '"></div>',
      '<div class="vtPmField vtPmFieldFull"><label>Resolution note</label><textarea id="pmIssueResolution" class="vtPmReasonInput" rows="3" placeholder="Required when resolved or closed.">', escapeHtml(issue.resolution_note || ""), '</textarea></div>',
      '<div class="vtPmValidationMessage vtPmFieldFull" id="pmIssueValidation"></div>'
    ].join(""), function (dialog) {
      var title = dialog.querySelector("#pmIssueTitle").value.trim();
      var status = dialog.querySelector("#pmIssueStatus").value;
      var resolution = dialog.querySelector("#pmIssueResolution").value.trim();
      if (!title) {
        dialog.querySelector("#pmIssueValidation").textContent = "Issue title is required.";
        return false;
      }
      if ((status === "Resolved" || status === "Closed") && !resolution) {
        dialog.querySelector("#pmIssueValidation").textContent = "Add a resolution note before resolving or closing the issue.";
        return false;
      }
      var payload = {
        issue_id: issue.issue_id || uid("pmissue"),
        task_id: task.task_id,
        title: title,
        description: dialog.querySelector("#pmIssueDescription").value.trim(),
        priority: dialog.querySelector("#pmIssuePriority").value,
        status: status,
        owner: dialog.querySelector("#pmIssueOwner").value.trim(),
        due_date: dialog.querySelector("#pmIssueDueDate").value || null,
        resolution_note: resolution
      };
      var now = new Date().toISOString();
      var saved = Object.assign({}, issue, payload, {
        project_id: state.projectId,
        project_number: state.chart.project.project_number,
        task_title: task.title,
        created_at: issue.created_at || now,
        updated_at: now,
        _pendingNew: issue._pendingNew || !issue.issue_id
      });
      var issueIndex = state.issues.findIndex(function (item) {
        return item.issue_id === saved.issue_id;
      });
      if (issueIndex >= 0) state.issues[issueIndex] = saved;
      else state.issues.push(saved);
      state.pendingIssueChanges[saved.issue_id] = saved;
      markDirty();
      renderBoard();
      setStatus("Issue change pending. Save Project to store it.", "warning");
      if (onSaved) onSaved(saved);
      return true;
    }, issue.issue_id ? "Update Issue" : "Add Issue");
  }

  function openRelatedIssues(taskId, resolvedOnly) {
    var task = taskMap()[taskId];
    if (!task) return;
    function relatedIssues() {
      return (state.issues || []).filter(function (issue) {
        var resolved = issue.status === "Resolved" || issue.status === "Closed";
        return issue.task_id === taskId && (resolvedOnly ? resolved : !resolved);
      });
    }
    var issues = relatedIssues();
    if (!issues.length) {
      setStatus("This task has no " + (resolvedOnly ? "resolved" : "open") + " issues", "info");
      return;
    }
    if (issues.length === 1) {
      openIssueDialog(task, issues[0]);
      return;
    }
    var numbers = taskNumberMap(state.chart.tasks || []);
    var dialog = modal(
      (numbers[taskId] || "") + " Related " + (resolvedOnly ? "resolved" : "open") + " issues",
      '<div class="vtPmTaskIssueList vtPmFieldFull">' + issueListHtml(issues) + "</div>",
      function () { return true; },
      "Close"
    );
    dialog.querySelector("[data-modal-cancel]").style.display = "none";
    dialog.querySelector(".vtPmTaskIssueList").addEventListener("click", function (event) {
      var issueButton = event.target.closest("[data-edit-issue]");
      if (!issueButton) return;
      var issue = relatedIssues().find(function (item) {
        return item.issue_id === issueButton.getAttribute("data-edit-issue");
      });
      if (!issue) return;
      openIssueDialog(task, issue, function () {
        issues = relatedIssues();
        if (!issues.length) {
          dialog.remove();
          return;
        }
        dialog.querySelector(".vtPmTaskIssueList").innerHTML = issueListHtml(issues);
      });
    });
  }

  function openTaskDialog(taskId) {
    if (!state.chart) return openProjectDialog();
    var existing = (state.chart.tasks || []).find(function (task) { return task.task_id === taskId; });
    if (!existing) return renderTaskDialog(taskId, []);
    var issues = (state.issues || []).filter(function (issue) {
      return issue.task_id === taskId;
    });
    renderTaskDialog(taskId, issues);
  }

  function renderTaskDialog(taskId, taskIssues) {
    if (!state.chart) return openProjectDialog();
    var existing = (state.chart.tasks || []).find(function (task) { return task.task_id === taskId; });
    var assignment = (state.chart.assignments || []).find(function (item) { return item.task_id === taskId; });
    var taskDependencies = (state.chart.dependencies || []).filter(function (item) { return item.successor_id === taskId; });
    var dialogTaskNumbers = taskNumberMap(state.chart.tasks || []);
    var dialogTaskMap = taskMap();
    var conditionNames = {
      FS: "Start after",
      SS: "Start with",
      SF: "End before",
      FF: "End with"
    };
    var conditionGroups = {};
    taskDependencies.forEach(function (item) {
      var conditionName = conditionNames[item.dependency_type] || item.dependency_type;
      var lag = Number(item.lag_value) || 0;
      var groupKey = conditionName + "|" + lag;
      var referenced = dialogTaskMap[item.predecessor_id];
      var referenceLabel = referenced
        ? ((dialogTaskNumbers[referenced.task_id] || "") + " " + referenced.title).trim()
        : item.predecessor_id;
      (conditionGroups[groupKey] || (conditionGroups[groupKey] = {
        name: conditionName,
        lag: lag,
        values: []
      })).values.push(referenceLabel);
    });
    var taskScheduleConfig = existing ? scheduleConfig(existing) : {};
    var scheduleSummary = Object.keys(conditionGroups).map(function (key) {
      var group = conditionGroups[key];
      var offsetText = group.lag
        ? " (" + (group.lag > 0 ? "+" : "") + group.lag + " working days)"
        : "";
      return '<div class="vtPmScheduleSummaryRow"><strong>' +
        escapeHtml(group.name) + ':</strong><span>' +
        escapeHtml(group.values.join(", ")) + escapeHtml(offsetText) + "</span></div>";
    });
    if (taskScheduleConfig.start_from_parent) {
      var parentTask = dialogTaskMap[existing && existing.parent_id];
      scheduleSummary.push(
        '<div class="vtPmScheduleSummaryRow"><strong>Start from parent:</strong><span>' +
        escapeHtml(parentTask
          ? ((dialogTaskNumbers[parentTask.task_id] || "") + " " + parentTask.title).trim()
          : "Parent task") + "</span></div>"
      );
    }
    if (taskScheduleConfig.duration_equal_to) {
      var durationTask = dialogTaskMap[taskScheduleConfig.duration_equal_to];
      scheduleSummary.push(
        '<div class="vtPmScheduleSummaryRow"><strong>Duration equal to:</strong><span>' +
        escapeHtml(durationTask
          ? ((dialogTaskNumbers[durationTask.task_id] || "") + " " + durationTask.title).trim()
          : taskScheduleConfig.duration_equal_to) + "</span></div>"
      );
    }
    var defaultStart = state.chart.project.start_date || isoDate(new Date());
    var pendingReason = existing && state.pendingActivityLogs[existing.task_id] || "";
    var taskDialog = modal(existing
      ? "Edit task " + (dialogTaskNumbers[existing.task_id] || "")
      : "Add task", [
      '<div class="vtPmDialogTabs vtPmFieldFull">',
        '<button type="button" class="active" data-task-dialog-tab="details">Task Details</button>',
        '<button type="button" data-task-dialog-tab="issues"', existing ? "" : " disabled", '>Issues', existing ? " (" + taskIssues.length + ")" : "", '</button>',
      '</div>',
      '<div class="vtPmTaskTabPanel vtPmFieldFull active" data-task-tab-panel="details">',
      '<div class="vtPmTaskDetailsGrid">',
      '<div class="vtPmField vtPmFieldFull"><label>Task title</label><input id="pmTaskTitle" value="', escapeHtml(existing && existing.title || ""), '" required></div>',
      '<div class="vtPmField"><label>Start date</label><input id="pmTaskStart" type="date" value="', escapeHtml(existing && existing.start_date || defaultStart), '"></div>',
      '<div class="vtPmField"><label>Task Duration (working days)</label><input id="pmTaskDuration" type="number" min="0" step="0.5" value="', escapeHtml(existing && existing.duration_value != null ? existing.duration_value : 1), '"></div>',
      '<div class="vtPmField"><label>Progress (%)</label><input id="pmTaskProgress" type="number" min="0" max="100" value="', escapeHtml(existing && existing.progress_percent || 0), '"></div>',
      '<div class="vtPmField"><label>Parent task</label><select id="pmTaskParent">', selectOptions(state.chart.tasks || [], "task_id", "title", existing && existing.parent_id, "No parent", taskId), "</select></div>",
      '<div class="vtPmField vtPmFieldFull"><label>Schedule conditions</label><div class="vtPmReadOnlyField">',
        scheduleSummary.length ? scheduleSummary.join("") : "None",
        '<small>Use the Schedule Conditions buttons to change these values.</small></div></div>',
      '<div class="vtPmField"><label>Internal resource</label><select id="pmTaskResource">', selectOptions(state.resources, "resource_id", "name", assignment && assignment.resource_id, "Unassigned"), "</select></div>",
      '<div class="vtPmField"><label>Allocation</label><input id="pmTaskAllocation" type="number" min="0.1" step="0.1" value="', escapeHtml(assignment && assignment.allocation || 1), '"></div>',
      '<div class="vtPmField vtPmFieldFull vtPmLogChangeBox">',
        '<label class="vtPmCheckboxLabel"><input id="pmTaskLogChange" type="checkbox"', pendingReason ? " checked" : "", '> Add this task change to the log book</label>',
        '<div id="pmTaskLogReasonWrap"', pendingReason ? "" : ' hidden', '>',
          '<label for="pmTaskLogReason">Reason for the change</label>',
          '<textarea id="pmTaskLogReason" class="vtPmReasonInput" rows="3" maxlength="1000" placeholder="Explain why this task was changed.">', escapeHtml(pendingReason), '</textarea>',
          '<div class="vtPmValidationMessage" id="pmTaskLogValidation"></div>',
        '</div>',
      '</div>',
      '</div></div>',
      '<div class="vtPmTaskTabPanel vtPmFieldFull" data-task-tab-panel="issues">',
        '<div class="vtPmIssuePanelHeader"><span>Issues linked to this task</span><button type="button" class="vtPmBtn vtPmBtnPrimary" data-add-task-issue>Add Issue</button></div>',
        '<div class="vtPmTaskIssueList">', issueListHtml(taskIssues), '</div>',
      '</div>'
    ].join(""), function (dialog) {
      var title = dialog.querySelector("#pmTaskTitle").value.trim();
      if (!title) throw new Error("Task title is required");
      var id = existing ? existing.task_id : uid("pmtask");
      var shouldLog = dialog.querySelector("#pmTaskLogChange").checked;
      var logReason = dialog.querySelector("#pmTaskLogReason").value.trim();
      if (shouldLog && !logReason) {
        dialog.querySelector("#pmTaskLogValidation").textContent = "Enter a reason to add this change to the log book.";
        dialog.querySelector("#pmTaskLogReason").focus();
        return false;
      }
      var task = existing || {
        task_id: id,
        tree_index: state.chart.tasks.length,
        description: "",
        task_type: "task",
        duration_unit: "working_days",
        color: "#0a6ed1",
        cc_item_id: null
      };
      task.title = title;
      task.start_date = dialog.querySelector("#pmTaskStart").value;
      task.duration_value = Number(dialog.querySelector("#pmTaskDuration").value || 0);
      task.progress_percent = Number(dialog.querySelector("#pmTaskProgress").value || 0);
      task.parent_id = dialog.querySelector("#pmTaskParent").value || null;
      task.end_date = calculateEnd(task);
      if (!existing) state.chart.tasks.push(task);
      if (shouldLog) state.pendingActivityLogs[id] = logReason;
      else delete state.pendingActivityLogs[id];

      state.chart.assignments = (state.chart.assignments || []).filter(function (item) {
        return item.task_id !== id;
      });
      var resourceId = dialog.querySelector("#pmTaskResource").value;
      if (resourceId) {
        state.chart.assignments.push({
          task_id: id,
          resource_id: resourceId,
          allocation: Number(dialog.querySelector("#pmTaskAllocation").value || 1),
          role: ""
        });
      }
      recalculateSchedule(id);
      markDirty();
      renderBoard();
    }, existing ? "Update Task" : "Add Task");
    taskDialog.querySelector("#pmTaskLogChange").addEventListener("change", function (event) {
      var reasonWrap = taskDialog.querySelector("#pmTaskLogReasonWrap");
      reasonWrap.hidden = !event.target.checked;
      if (event.target.checked) taskDialog.querySelector("#pmTaskLogReason").focus();
    });
    taskDialog.querySelectorAll("[data-task-dialog-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (button.disabled) return;
        taskDialog.querySelectorAll("[data-task-dialog-tab]").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        taskDialog.querySelectorAll("[data-task-tab-panel]").forEach(function (panel) {
          panel.classList.toggle("active", panel.getAttribute("data-task-tab-panel") ===
            button.getAttribute("data-task-dialog-tab"));
        });
      });
    });
    function refreshTaskIssues() {
      taskIssues = (state.issues || []).filter(function (issue) {
        return issue.task_id === existing.task_id;
      });
      taskDialog.querySelector(".vtPmTaskIssueList").innerHTML = issueListHtml(taskIssues);
      var issueTab = taskDialog.querySelector('[data-task-dialog-tab="issues"]');
      issueTab.textContent = "Issues (" + taskIssues.length + ")";
    }
    var addIssueButton = taskDialog.querySelector("[data-add-task-issue]");
    if (addIssueButton && existing) {
      addIssueButton.addEventListener("click", function () {
        openIssueDialog(existing, null, refreshTaskIssues);
      });
    }
    taskDialog.querySelector(".vtPmTaskIssueList").addEventListener("click", function (event) {
      var issueButton = event.target.closest("[data-edit-issue]");
      if (!issueButton || !existing) return;
      var issue = taskIssues.find(function (item) {
        return item.issue_id === issueButton.getAttribute("data-edit-issue");
      });
      if (issue) openIssueDialog(existing, issue, refreshTaskIssues);
    });
  }

  function subtreeEndIndex(taskId) {
    var tasks = state.chart.tasks || [];
    var rootIndex = tasks.findIndex(function (task) { return task.task_id === taskId; });
    if (rootIndex < 0) return tasks.length;
    var descendants = {};
    descendants[taskId] = true;
    var end = rootIndex + 1;
    for (var index = rootIndex + 1; index < tasks.length; index++) {
      var task = tasks[index];
      if (task.parent_id && descendants[task.parent_id]) {
        descendants[task.task_id] = true;
        end = index + 1;
      } else {
        break;
      }
    }
    return end;
  }

  function previousSibling(parentId, insertionIndex) {
    var tasks = state.chart.tasks || [];
    for (var index = insertionIndex - 1; index >= 0; index--) {
      if ((tasks[index].parent_id || null) === (parentId || null)) return tasks[index];
    }
    return null;
  }

  function defaultTask(parentId, insertionIndex) {
    var projectStart = state.chart.project.start_date || isoDate(new Date());
    var previous = previousSibling(parentId, insertionIndex);
    var start = projectStart;
    if (previous && previous.end_date) {
      var next = addCalendarDays(parseDate(previous.end_date), 1);
      while (next.getUTCDay() === 0 || next.getUTCDay() === 6) {
        next = addCalendarDays(next, 1);
      }
      start = isoDate(next);
    }
    var task = {
      task_id: uid("pmtask"),
      parent_id: parentId || null,
      tree_index: insertionIndex,
      title: "New Task",
      description: "",
      start_date: start,
      duration_value: 1,
      duration_unit: "working_days",
      progress_percent: 0,
      task_type: "task",
      color: "#0a6ed1",
      cc_item_id: null
    };
    task.end_date = calculateEnd(task);
    return { task: task, previous: previous };
  }

  function addTasksAt(mode) {
    if (!state.chart) {
      openProjectDialog();
      return;
    }
    var tasks = state.chart.tasks || (state.chart.tasks = []);
    var quantityInput = state.root.querySelector("#vtPmTaskQuantity");
    var quantity = Math.max(1, Math.min(100, Math.floor(Number(quantityInput && quantityInput.value) || 1)));
    if (quantityInput) quantityInput.value = quantity;
    var selected = tasks.find(function (task) { return task.task_id === state.selectedTaskId; });

    if ((mode === "above" || mode === "below") && !selected) {
      setStatus("Select a task row first", "warning");
      return;
    }

    // Match Cycle Chart behavior: with no row selected, New Subtask starts a
    // new top-level task. Repeated clicks after an auto-added child create
    // siblings under the same parent rather than nesting indefinitely.
    var parentId = null;
    var insertionIndex = tasks.length;
    if (mode === "subtask" && selected) {
      if (selected.task_id === state.lastAddedTaskId && selected.parent_id) {
        parentId = selected.parent_id;
        insertionIndex = subtreeEndIndex(selected.task_id);
      } else {
        parentId = selected.task_id;
        insertionIndex = subtreeEndIndex(selected.task_id);
      }
    } else if (mode === "above" && selected) {
      parentId = selected.parent_id || null;
      insertionIndex = tasks.indexOf(selected);
    } else if (mode === "below" && selected) {
      parentId = selected.parent_id || null;
      insertionIndex = subtreeEndIndex(selected.task_id);
    }

    var previousAtInsertion = previousSibling(parentId, insertionIndex);
    var followingAtInsertion = null;
    if (mode === "above" || mode === "below") {
      for (var followingIndex = insertionIndex; followingIndex < tasks.length; followingIndex++) {
        if ((tasks[followingIndex].parent_id || null) === (parentId || null)) {
          followingAtInsertion = tasks[followingIndex];
          break;
        }
      }
    }

    var createdIds = [];
    for (var count = 0; count < quantity; count++) {
      var built = defaultTask(parentId, insertionIndex);
      tasks.splice(insertionIndex, 0, built.task);
      createdIds.push(built.task.task_id);
      if (built.previous) {
        state.chart.dependencies.push({
          predecessor_id: built.previous.task_id,
          successor_id: built.task.task_id,
          dependency_type: "FS",
          lag_value: 0,
          lag_unit: "working_days"
        });
      }
      insertionIndex = subtreeEndIndex(built.task.task_id);
    }
    if ((mode === "above" || mode === "below") && followingAtInsertion && createdIds.length) {
      // Insert the new block into the existing sibling dependency chain:
      // previous -> following becomes previous -> new block -> following.
      if (previousAtInsertion) {
        state.chart.dependencies = (state.chart.dependencies || []).filter(function (dependency) {
          return !(dependency.predecessor_id === previousAtInsertion.task_id &&
            dependency.successor_id === followingAtInsertion.task_id &&
            dependency.dependency_type === "FS");
        });
      }
      var lastCreatedId = createdIds[createdIds.length - 1];
      var followingLinkExists = (state.chart.dependencies || []).some(function (dependency) {
        return dependency.predecessor_id === lastCreatedId &&
          dependency.successor_id === followingAtInsertion.task_id;
      });
      if (!followingLinkExists) {
        state.chart.dependencies.push({
          predecessor_id: lastCreatedId,
          successor_id: followingAtInsertion.task_id,
          dependency_type: "FS",
          lag_value: 0,
          lag_unit: "working_days"
        });
      }
    }
    state.selectedTaskId = createdIds[createdIds.length - 1];
    state.selectedTaskIds = [state.selectedTaskId];
    state.lastAddedTaskId = state.selectedTaskId;
    markDirty();
    renderBoard();
    setStatus(quantity + (quantity === 1 ? " task added" : " tasks added") + ". Edit the selected task to set its details.", "success");
    if (quantity === 1) openTaskDialog(state.selectedTaskId);
  }

  function openResourceDialog() {
    modal("Add internal resource", [
      '<div class="vtPmField vtPmFieldFull"><label>Resource name</label><input id="pmResourceName" required placeholder="Controls Engineering"></div>',
      '<div class="vtPmField"><label>Resource type</label><select id="pmResourceType"><option value="person">Person</option><option value="team">Team</option><option value="internal_machine">Internal machine</option><option value="contractor">Contractor</option><option value="other">Other</option></select></div>',
      '<div class="vtPmField"><label>Available capacity</label><input id="pmResourceCapacity" type="number" min="0.1" step="0.1" value="1"></div>'
    ].join(""), function (dialog) {
      var name = dialog.querySelector("#pmResourceName").value.trim();
      if (!name) throw new Error("Resource name is required");
      return request("/resources", {
        method: "POST",
        body: JSON.stringify({
          name: name,
          resource_type: dialog.querySelector("#pmResourceType").value,
          capacity: Number(dialog.querySelector("#pmResourceCapacity").value || 1)
        })
      }).then(function (resource) {
        state.resources.push(resource);
        setStatus("Internal resource created", "success");
        renderBoard();
      });
    }, "Add Resource");
  }

  function selectedClipboardRoots() {
    if (!state.chart || !state.selectedTaskIds.length) return [];
    var selected = {};
    state.selectedTaskIds.forEach(function (id) { selected[id] = true; });
    var map = taskMap();
    return state.selectedTaskIds.filter(function (id) {
      var parent = map[id] && map[id].parent_id;
      while (parent) {
        if (selected[parent]) return false;
        parent = map[parent] && map[parent].parent_id;
      }
      return !!map[id];
    }).sort(function (a, b) {
      return state.chart.tasks.findIndex(function (task) { return task.task_id === a; }) -
        state.chart.tasks.findIndex(function (task) { return task.task_id === b; });
    });
  }

  function clipboardTaskIds(rootIds) {
    var included = {};
    rootIds.forEach(function (id) { included[id] = true; });
    var changed = true;
    while (changed) {
      changed = false;
      state.chart.tasks.forEach(function (task) {
        if (task.parent_id && included[task.parent_id] && !included[task.task_id]) {
          included[task.task_id] = true;
          changed = true;
        }
      });
    }
    return included;
  }

  function copyTasksToClipboard(mode) {
    var rootIds = selectedClipboardRoots();
    if (!rootIds.length) {
      setStatus("Select one or more tasks first.", "warning");
      return;
    }
    var ids = clipboardTaskIds(rootIds);
    state.clipboard = {
      mode: mode,
      rootIds: rootIds.slice(),
      tasks: JSON.parse(JSON.stringify(state.chart.tasks.filter(function (task) { return ids[task.task_id]; }))),
      dependencies: JSON.parse(JSON.stringify((state.chart.dependencies || []).filter(function (dependency) {
        return ids[dependency.predecessor_id] && ids[dependency.successor_id];
      }))),
      assignments: JSON.parse(JSON.stringify((state.chart.assignments || []).filter(function (assignment) {
        return ids[assignment.task_id];
      })))
    };
    if (mode === "cut") {
      var originalTasks = state.chart.tasks.slice();
      var originalDependencies = (state.chart.dependencies || []).slice();
      state.chart.dependencies = repairDependenciesAfterRemoval(originalTasks, originalDependencies, ids);
      state.chart.tasks = state.chart.tasks.filter(function (task) { return !ids[task.task_id]; });
      state.chart.assignments = (state.chart.assignments || []).filter(function (assignment) {
        return !ids[assignment.task_id];
      });
      state.selectedTaskId = "";
      state.selectedTaskIds = [];
      markDirty();
      renderBoard();
      setStatus("Cut " + rootIds.length + " task block(s). Select a destination and paste.", "success");
    } else {
      setStatus("Copied " + rootIds.length + " task block(s).", "success");
    }
  }

  function pasteTasksFromClipboard(mode) {
    var clipboard = state.clipboard;
    if (!state.chart || !clipboard.tasks.length) {
      setStatus("Nothing has been copied or cut.", "warning");
      return;
    }
    var tasks = state.chart.tasks;
    var reference = taskMap()[state.selectedTaskId];
    if (!reference && mode !== "child") {
      setStatus("Select a destination task first.", "warning");
      return;
    }
    var parentId = null;
    var insertionIndex = tasks.length;
    if (mode === "child") {
      parentId = reference ? reference.task_id : null;
      insertionIndex = reference ? subtreeEndIndex(reference.task_id) : tasks.length;
    } else if (mode === "above") {
      parentId = reference.parent_id || null;
      insertionIndex = tasks.indexOf(reference);
    } else {
      parentId = reference.parent_id || null;
      insertionIndex = subtreeEndIndex(reference.task_id);
    }
    var previous = previousSibling(parentId, insertionIndex);
    var following = null;
    for (var nextIndex = insertionIndex; nextIndex < tasks.length; nextIndex++) {
      if ((tasks[nextIndex].parent_id || null) === (parentId || null)) {
        following = tasks[nextIndex];
        break;
      }
    }

    var idMap = {};
    clipboard.tasks.forEach(function (task) { idMap[task.task_id] = uid("pmtask"); });
    var rootSet = {};
    clipboard.rootIds.forEach(function (id) { rootSet[id] = true; });
    var freshTasks = clipboard.tasks.map(function (source) {
      var clone = JSON.parse(JSON.stringify(source));
      clone.task_id = idMap[source.task_id];
      clone.parent_id = rootSet[source.task_id] ? parentId : (idMap[source.parent_id] || parentId);
      if (clipboard.mode === "copy" && clone.title.indexOf("(**copy)") < 0) {
        clone.title = "(**copy) " + clone.title;
      }
      var config = scheduleConfig(clone);
      if (config.duration_equal_to && idMap[config.duration_equal_to]) {
        config.duration_equal_to = idMap[config.duration_equal_to];
      } else if (config.duration_equal_to) {
        delete config.duration_equal_to;
      }
      clone.schedule_config = config;
      return clone;
    });
    tasks.splice.apply(tasks, [insertionIndex, 0].concat(freshTasks));

    clipboard.dependencies.forEach(function (dependency) {
      state.chart.dependencies.push({
        predecessor_id: idMap[dependency.predecessor_id],
        successor_id: idMap[dependency.successor_id],
        dependency_type: dependency.dependency_type,
        lag_value: dependency.lag_value,
        lag_unit: dependency.lag_unit
      });
    });
    clipboard.assignments.forEach(function (assignment) {
      var cloneAssignment = JSON.parse(JSON.stringify(assignment));
      cloneAssignment.task_id = idMap[assignment.task_id];
      state.chart.assignments.push(cloneAssignment);
    });

    var newRootIds = clipboard.rootIds.map(function (id) { return idMap[id]; });
    if (previous && following) {
      state.chart.dependencies = state.chart.dependencies.filter(function (dependency) {
        return !(dependency.predecessor_id === previous.task_id &&
          dependency.successor_id === following.task_id &&
          dependency.dependency_type === "FS");
      });
    }
    if (previous && newRootIds.length) {
      state.chart.dependencies.push({
        predecessor_id: previous.task_id, successor_id: newRootIds[0],
        dependency_type: "FS", lag_value: 0, lag_unit: "working_days"
      });
    }
    for (var rootIndex = 1; rootIndex < newRootIds.length; rootIndex++) {
      state.chart.dependencies.push({
        predecessor_id: newRootIds[rootIndex - 1], successor_id: newRootIds[rootIndex],
        dependency_type: "FS", lag_value: 0, lag_unit: "working_days"
      });
    }
    if (following && newRootIds.length) {
      state.chart.dependencies.push({
        predecessor_id: newRootIds[newRootIds.length - 1], successor_id: following.task_id,
        dependency_type: "FS", lag_value: 0, lag_unit: "working_days"
      });
    }
    var dependencyKeys = {};
    state.chart.dependencies = state.chart.dependencies.filter(function (dependency) {
      var key = dependency.predecessor_id + "|" + dependency.successor_id;
      if (dependencyKeys[key]) return false;
      dependencyKeys[key] = true;
      return true;
    });
    if (clipboard.mode === "cut") state.clipboard = { mode: "", tasks: [], dependencies: [], assignments: [], rootIds: [] };
    state.selectedTaskIds = newRootIds;
    state.selectedTaskId = newRootIds[newRootIds.length - 1] || "";
    state.collapsedTaskIds = {};
    recalculateSchedule(state.selectedTaskId);
    markDirty();
    renderBoard();
    setStatus("Pasted " + newRootIds.length + " task block(s).", "success");
  }

  function confirmCopiedTasks() {
    if (!state.chart || !state.selectedTaskIds.length) {
      setStatus("Select copied tasks to confirm.", "warning");
      return;
    }
    var ids = clipboardTaskIds(selectedClipboardRoots());
    var changed = 0;
    state.chart.tasks.forEach(function (task) {
      if (ids[task.task_id] && task.title.indexOf("(**copy)") >= 0) {
        task.title = task.title.replace(/^\(\*\*copy\)\s*/, "");
        changed++;
      }
    });
    if (changed) {
      markDirty();
      renderBoard();
      setStatus("Confirmed " + changed + " copied task(s).", "success");
    }
  }

  function repairDependenciesAfterRemoval(tasks, dependencies, removedIds) {
    var taskById = {};
    tasks.forEach(function (task) { taskById[task.task_id] = task; });
    var survivingIds = {};
    tasks.forEach(function (task) {
      if (!removedIds[task.task_id]) survivingIds[task.task_id] = true;
    });
    var repaired = dependencies.filter(function (dependency) {
      return !removedIds[dependency.predecessor_id] && !removedIds[dependency.successor_id];
    });

    function upstreamSurvivors(taskId, seen) {
      if (seen[taskId]) return [];
      seen[taskId] = true;
      var values = [];
      dependencies.forEach(function (dependency) {
        if (dependency.successor_id !== taskId) return;
        if (survivingIds[dependency.predecessor_id]) values.push(dependency.predecessor_id);
        else if (removedIds[dependency.predecessor_id]) {
          values = values.concat(upstreamSurvivors(dependency.predecessor_id, seen));
        }
      });
      return values.filter(function (id, index, ids) { return ids.indexOf(id) === index; });
    }

    function addDependency(predecessorId, successorId, template) {
      if (!predecessorId || !successorId || predecessorId === successorId) return;
      if (!survivingIds[predecessorId] || !survivingIds[successorId]) return;
      var exists = repaired.some(function (dependency) {
        return dependency.predecessor_id === predecessorId &&
          dependency.successor_id === successorId;
      });
      if (exists) return;
      repaired.push({
        predecessor_id: predecessorId,
        successor_id: successorId,
        dependency_type: template && template.dependency_type || "FS",
        lag_value: template && Number(template.lag_value) || 0,
        lag_unit: template && template.lag_unit || "working_days"
      });
    }

    // Preserve explicit chains through any number of removed intermediate tasks.
    dependencies.forEach(function (dependency) {
      if (!removedIds[dependency.predecessor_id] || !survivingIds[dependency.successor_id]) return;
      upstreamSurvivors(dependency.predecessor_id, {}).forEach(function (predecessorId) {
        addDependency(predecessorId, dependency.successor_id, dependency);
      });
    });

    // CC sibling repair: reconnect the next surviving sibling to the previous
    // surviving sibling even when the removed task had no explicit dependency.
    tasks.forEach(function (removedTask, removedIndex) {
      if (!removedIds[removedTask.task_id] ||
          (removedTask.parent_id && removedIds[removedTask.parent_id])) return;
      var previous = null;
      var next = null;
      for (var before = removedIndex - 1; before >= 0; before--) {
        if (!removedIds[tasks[before].task_id] &&
            (tasks[before].parent_id || null) === (removedTask.parent_id || null)) {
          previous = tasks[before];
          break;
        }
      }
      for (var after = removedIndex + 1; after < tasks.length; after++) {
        if (!removedIds[tasks[after].task_id] &&
            (tasks[after].parent_id || null) === (removedTask.parent_id || null)) {
          next = tasks[after];
          break;
        }
      }
      if (previous && next) addDependency(previous.task_id, next.task_id, null);
    });
    return repaired;
  }

  function deleteTask(taskId) {
    if (!state.chart || !window.confirm("Delete this task and its assignments/dependencies?")) return;
    var ids = {};
    ids[taskId] = true;
    var changed = true;
    while (changed) {
      changed = false;
      state.chart.tasks.forEach(function (task) {
        if (task.parent_id && ids[task.parent_id] && !ids[task.task_id]) {
          ids[task.task_id] = true;
          changed = true;
        }
      });
    }
    var originalTasks = state.chart.tasks.slice();
    var originalDependencies = (state.chart.dependencies || []).slice();
    state.chart.dependencies = repairDependenciesAfterRemoval(
      originalTasks, originalDependencies, ids
    );
    state.chart.tasks = state.chart.tasks.filter(function (task) { return !ids[task.task_id]; });
    state.chart.assignments = state.chart.assignments.filter(function (assignment) {
      return !ids[assignment.task_id];
    });
    state.issues = (state.issues || []).filter(function (issue) {
      return !ids[issue.task_id];
    });
    Object.keys(state.pendingIssueChanges).forEach(function (issueId) {
      if (ids[state.pendingIssueChanges[issueId].task_id]) {
        delete state.pendingIssueChanges[issueId];
      }
    });
    if (ids[state.selectedTaskId]) state.selectedTaskId = "";
    state.selectedTaskIds = state.selectedTaskIds.filter(function (id) { return !ids[id]; });
    if (!state.selectedTaskId && state.selectedTaskIds.length) {
      state.selectedTaskId = state.selectedTaskIds[state.selectedTaskIds.length - 1];
    }
    if (ids[state.lastAddedTaskId]) state.lastAddedTaskId = "";
    markDirty();
    renderBoard();
  }

  function performChartSave() {
    if (!state.chart || !state.projectId) return Promise.resolve();
    recalculateSchedule();
    state.chart.tasks.forEach(function (task, index) {
      task.tree_index = index;
      task.duration_unit = "working_days";
    });
    setStatus("Saving project...", "info");
    var pendingIssueIds = Object.keys(state.pendingIssueChanges);
    return request("/projects/" + encodeURIComponent(state.projectId) + "/chart", {
      method: "PUT",
      body: JSON.stringify({
        project: state.chart.project,
        tasks: state.chart.tasks,
        dependencies: state.chart.dependencies,
        assignments: state.chart.assignments,
        deliverables: state.chart.deliverables || [],
        activity_log_requests: Object.keys(state.pendingActivityLogs).map(function (taskId) {
          return { task_id: taskId, reason: state.pendingActivityLogs[taskId] };
        })
      })
    }).then(function (chart) {
      state.chart = chart;
      return Promise.all(pendingIssueIds.map(function (issueId) {
        var issue = state.pendingIssueChanges[issueId];
        var path = "/projects/" + encodeURIComponent(state.projectId) + "/issues";
        if (!issue._pendingNew) path += "/" + encodeURIComponent(issue.issue_id);
        return request(path, {
          method: issue._pendingNew ? "POST" : "PUT",
          body: JSON.stringify(issue)
        });
      }));
    }).then(function (savedIssues) {
      savedIssues.forEach(function (saved) {
        var index = state.issues.findIndex(function (issue) {
          return issue.issue_id === saved.issue_id;
        });
        if (index >= 0) state.issues[index] = saved;
        else state.issues.push(saved);
      });
      pendingIssueIds.forEach(function (issueId) {
        delete state.pendingIssueChanges[issueId];
      });
      state.dirty = false;
      state.pendingActivityLogs = {};
      setStatus("Project saved", "success");
      renderBoard();
      return state.chart;
    }).catch(function (error) {
      setStatus(error.message, "error");
      throw error;
    });
  }

  function saveChart() {
    if (!state.chart || !state.projectId) return Promise.resolve(null);
    if (!state.dirty) {
      setStatus("No project changes to save", "info");
      return Promise.resolve(state.chart);
    }
    return performChartSave();
  }

  function activityValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function activityDescription(activity) {
    var details = activity.details || {};
    if (activity.event_type === "task_updated") {
      return Object.keys(details.changes || {}).map(function (field) {
        var change = details.changes[field] || {};
        return '<div class="vtPmActivityChange"><strong>' + escapeHtml(field.replace(/_/g, " ")) +
          '</strong><span>' + escapeHtml(activityValue(change.before)) + '</span><b>→</b><span>' +
          escapeHtml(activityValue(change.after)) + '</span></div>';
      }).join("");
    }
    if (activity.event_type === "dependencies_changed") {
      return '<span>Added ' + escapeHtml((details.added || []).length) +
        ' and removed ' + escapeHtml((details.removed || []).length) + ' schedule relationship(s).</span>';
    }
    if (activity.event_type === "task_added") return "<span>Task added to the chart.</span>";
    if (activity.event_type === "task_deleted") return "<span>Task removed from the chart.</span>";
    return "<span>Project chart changed.</span>";
  }

  function openActivityHistory() {
    if (!state.projectId) {
      setStatus("Select a PM project first", "warning");
      return;
    }
    setStatus("Loading activity history...", "info");
    request("/projects/" + encodeURIComponent(state.projectId) + "/activity").then(function (activities) {
      var numbers = taskNumberMap(state.chart && state.chart.tasks || []);
      var body = activities.length ? activities.map(function (activity) {
        var date = new Date(activity.created_at);
        var timestamp = isNaN(date.getTime()) ? activity.created_at : date.toLocaleString();
        var taskLabel = activity.task_title || "Schedule relationships";
        if (activity.task_id && numbers[activity.task_id]) {
          taskLabel = numbers[activity.task_id] + " " + taskLabel;
        }
        return [
          '<article class="vtPmActivityItem">',
            '<div class="vtPmActivityMeta"><time>', escapeHtml(timestamp), '</time>',
              '<span>', escapeHtml(activity.event_type.replace(/_/g, " ")), '</span></div>',
            '<h4>', escapeHtml(taskLabel), '</h4>',
            '<p class="vtPmActivityReason">', escapeHtml(activity.reason), '</p>',
            '<div class="vtPmActivityDetails">', activityDescription(activity), '</div>',
          '</article>'
        ].join("");
      }).join("") : '<div class="vtPmActivityEmpty">No saved chart changes have been logged yet.</div>';
      var dialog = modal("Project activity history",
        '<div class="vtPmActivityList vtPmFieldFull">' + body + "</div>",
        function () { return true; }, "Close");
      dialog.querySelector("[data-modal-cancel]").style.display = "none";
      dialog.querySelector(".vtPmDialog").classList.add("vtPmHistoryDialog");
      setStatus(activities.length + " activity record(s)", "success");
    }).catch(function (error) {
      setStatus(error.message, "error");
    });
  }

  function csvCell(value) {
    return '"' + String(value == null ? "" : value).replace(/"/g, '""') + '"';
  }

  function exportIssuesFile(issues) {
    var headers = [
      "Issue ID", "Project", "Task Number", "Task", "Title", "Description", "Priority",
      "Owner", "Status", "Created", "Due Date", "Resolution"
    ];
    var rows = [headers.map(csvCell).join(",")];
    var numbers = taskNumberMap(state.chart && state.chart.tasks || []);
    issues.forEach(function (issue) {
      rows.push([
        issue.issue_id, issue.project_number, numbers[issue.task_id] || "",
        issue.task_title, issue.title,
        issue.description, issue.priority, issue.owner, issue.status,
        issue.created_at, issue.due_date, issue.resolution_note
      ].map(csvCell).join(","));
    });
    var blob = new Blob(["\ufeff" + rows.join("\r\n")], {
      type: "text/csv;charset=utf-8"
    });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    var projectNumber = state.chart && state.chart.project &&
      state.chart.project.project_number || "project";
    link.download = projectNumber.replace(/[^a-z0-9_-]+/gi, "_") + "_open_issues.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 0);
    setStatus("Open issues file generated", "success");
  }

  function projectIssueListHtml(issues) {
    if (!issues.length) return '<div class="vtPmActivityEmpty">There are no open issues for this project.</div>';
    var numbers = taskNumberMap(state.chart && state.chart.tasks || []);
    return issues.map(function (issue) {
      var taskLabel = ((numbers[issue.task_id] || "") + " " + issue.task_title).trim();
      return [
        '<button type="button" class="vtPmProjectIssueRow" data-project-issue="', escapeHtml(issue.issue_id), '">',
          '<span><strong>', escapeHtml(issue.title), '</strong><small>', escapeHtml(taskLabel), '</small></span>',
          '<span class="vtPmIssuePriority vtPmIssuePriority', escapeHtml(issue.priority), '">', escapeHtml(issue.priority), '</span>',
          '<span>', escapeHtml(issue.owner || "Unassigned"), '</span>',
          '<span>', escapeHtml(issue.status), '</span>',
          '<span>', escapeHtml(issue.due_date || "No due date"), '</span>',
        '</button>'
      ].join("");
    }).join("");
  }

  function openIssuesRegister() {
    if (!state.projectId) {
      setStatus("Select a PM project first", "warning");
      return;
    }
    var issues = (state.issues || []).filter(function (issue) {
      return issue.status !== "Resolved" && issue.status !== "Closed";
    });
      var dialog = modal("Project open issues", [
        '<div class="vtPmIssueRegister vtPmFieldFull">',
          '<div class="vtPmIssueRegisterActions"><strong>', issues.length, ' open issue(s)</strong>',
            '<button type="button" class="vtPmBtn vtPmBtnPrimary" data-export-issues>Export File</button></div>',
          '<div class="vtPmProjectIssueHeader"><span>Issue / Task</span><span>Priority</span><span>Owner</span><span>Status</span><span>Due</span></div>',
          '<div class="vtPmProjectIssueList">', projectIssueListHtml(issues), '</div>',
        '</div>'
      ].join(""), function () { return true; }, "Close");
      dialog.querySelector("[data-modal-cancel]").style.display = "none";
      dialog.querySelector(".vtPmDialog").classList.add("vtPmIssuesDialog");
      dialog.querySelector("[data-export-issues]").addEventListener("click", function () {
        exportIssuesFile(issues);
      });
      dialog.querySelector(".vtPmProjectIssueList").addEventListener("click", function (event) {
        var row = event.target.closest("[data-project-issue]");
        if (!row) return;
        var issue = issues.find(function (item) {
          return item.issue_id === row.getAttribute("data-project-issue");
        });
        var task = issue && taskMap()[issue.task_id];
        if (!task) return;
        openIssueDialog(task, issue, function () {
          issues = (state.issues || []).filter(function (item) {
            return item.status !== "Resolved" && item.status !== "Closed";
          });
          dialog.querySelector(".vtPmProjectIssueList").innerHTML = projectIssueListHtml(issues);
          dialog.querySelector(".vtPmIssueRegisterActions strong").textContent =
            issues.length + " open issue(s)";
        });
      });
      setStatus(issues.length + " open issue(s)", issues.length ? "warning" : "success");
  }

  function showConflicts(warnings) {
    if (!warnings.length) {
      setStatus("No internal-resource capacity conflicts found", "success");
      renderBoard();
      return;
    }
    var list = warnings.map(function (warning) {
      return "<li><strong>" + escapeHtml(warning.resource_name) + "</strong> is allocated at " +
        escapeHtml(warning.demand) + " against capacity " + escapeHtml(warning.capacity) +
        " on " + escapeHtml(warning.date) + ".</li>";
    }).join("");
    modal("Resource capacity warnings",
      '<div class="vtPmField vtPmFieldFull"><ul class="vtPmConflictList">' + list + "</ul></div>",
      function () { return true; }, "Close");
    setStatus(warnings.length + " resource-capacity warning(s)", "warning");
    renderBoard();
  }

  function checkConflicts() {
    if (!state.projectId) return;
    var first = state.dirty ? saveChart() : Promise.resolve();
    first.then(function (saved) {
      if (saved === null) return null;
      return request("/projects/" + encodeURIComponent(state.projectId) + "/resource-conflicts");
    }).then(function (result) {
      if (!result) return;
      state.conflicts = result.warnings || [];
      showConflicts(state.conflicts);
    }).catch(function (error) {
      setStatus(error.message, "error");
    });
  }

  function loadChart(projectId) {
    if (!projectId) {
      state.projectId = "";
      state.chart = null;
      state.conflicts = [];
      state.issues = [];
      state.pendingIssueChanges = {};
      state.pendingActivityLogs = {};
      renderProjectSelect();
      renderEmpty();
      return Promise.resolve();
    }
    setStatus("Loading project...", "info");
    return Promise.all([
      request("/projects/" + encodeURIComponent(projectId) + "/chart"),
      request("/projects/" + encodeURIComponent(projectId) + "/issues")
    ]).then(function (values) {
      var chart = values[0];
      state.projectId = projectId;
      state.chart = chart;
      state.conflicts = [];
      state.issues = values[1] || [];
      state.pendingIssueChanges = {};
      state.selectedTaskId = "";
      state.selectedTaskIds = [];
      state.pendingScheduleCondition = null;
      state.pendingActivityLogs = {};
      state.collapsedTaskIds = {};
      state.lastAddedTaskId = "";
      state.dirty = false;
      renderProjectSelect();
      renderBoard();
      setStatus("", "");
    }).catch(function (error) {
      setStatus(error.message, "error");
    });
  }

  function loadInitialData() {
    setStatus("Loading PM projects...", "info");
    return Promise.all([request("/projects"), request("/resources")]).then(function (values) {
      state.projects = values[0] || [];
      state.resources = values[1] || [];
      renderProjectSelect();
      if (state.projects.length) return loadChart(state.projects[0].project_id);
      state.chart = null;
      state.issues = [];
      state.pendingIssueChanges = {};
      state.pendingActivityLogs = {};
      renderEmpty();
      setStatus("", "");
    }).catch(function (error) {
      setStatus(error.message, "error");
    });
  }

  function bindEvents() {
    state.root.setAttribute("tabindex", "-1");
    ensureTaskTooltip();
    state.root.addEventListener("mouseover", function (event) {
      var bar = event.target.closest(".vtPmBar");
      if (!bar || (event.relatedTarget && bar.contains(event.relatedTarget))) return;
      var row = bar.closest(".vtPmTaskRow");
      if (row) showTaskTooltip(row.getAttribute("data-task-id") || "", event);
    });
    state.root.addEventListener("mousemove", function (event) {
      if (event.target.closest(".vtPmBar")) positionTaskTooltip(event);
    });
    state.root.addEventListener("mouseout", function (event) {
      var bar = event.target.closest(".vtPmBar");
      if (!bar || (event.relatedTarget && bar.contains(event.relatedTarget))) return;
      hideTaskTooltip();
    });
    state.root.addEventListener("change", function (event) {
      if (event.target.id === "vtPmProjectSelect") {
        if (state.dirty && !window.confirm("Discard unsaved PM changes and load another project?")) {
          event.target.value = state.projectId;
          return;
        }
        loadChart(event.target.value);
      }
      if (event.target.id === "vtPmScale") {
        state.dayWidth = Number(event.target.value) || 34;
        renderBoard();
      }
      if (event.target.id === "vtPmTimeScale") {
        state.timeScale = event.target.value || "auto";
        state.dayWidth = {
          auto: 34,
          days: 34,
          weeks: 12,
          months: 4,
          years: 1
        }[state.timeScale] || 34;
        renderBoard();
      }
      if (event.target.id === "vtPmMonthView") {
        state.monthView = event.target.value || "all";
        renderBoard();
      }
    });
    state.root.addEventListener("pointerdown", function (event) {
      var resizer = event.target.closest("[data-resize-column]");
      if (!resizer) return;
      event.preventDefault();
      var column = resizer.getAttribute("data-resize-column");
      state.activeColumnResize = {
        column: column,
        startX: event.clientX,
        startWidth: state.columnWidths[column]
      };
      document.body.classList.add("vtPmResizingColumn");
    });
    document.addEventListener("pointermove", function (event) {
      if (!state.activeColumnResize) return;
      var resize = state.activeColumnResize;
      var minimum = resize.column === "index" ? 36 : 72;
      var width = Math.max(minimum, resize.startWidth + event.clientX - resize.startX);
      state.columnWidths[resize.column] = Math.round(width);
      var grid = state.root && state.root.querySelector(".vtPmGrid");
      if (grid) grid.style.setProperty("--pm-" + resize.column + "-width", Math.round(width) + "px");
    });
    document.addEventListener("pointerup", function () {
      if (!state.activeColumnResize) return;
      state.activeColumnResize = null;
      document.body.classList.remove("vtPmResizingColumn");
      renderBoard();
    });
    state.root.addEventListener("click", function (event) {
      var treeToggle = event.target.closest("[data-tree-toggle]");
      if (treeToggle) {
        event.preventDefault();
        event.stopPropagation();
        var toggleId = treeToggle.getAttribute("data-tree-toggle");
        if (state.collapsedTaskIds[toggleId]) delete state.collapsedTaskIds[toggleId];
        else state.collapsedTaskIds[toggleId] = true;
        renderBoard();
        state.root.focus();
        return;
      }
      var phaseButton = event.target.closest("[data-schedule-phase]");
      if (phaseButton) {
        openScheduleConditionDialog(phaseButton.getAttribute("data-schedule-phase"));
        return;
      }
      var clickedBar = event.target.closest(".vtPmBar");
      if (clickedBar && !state.pendingScheduleCondition) {
        hideTaskTooltip();
        var clickedBarRow = clickedBar.closest(".vtPmTaskRow");
        var clickedBarId = clickedBarRow && clickedBarRow.getAttribute("data-task-id") || "";
        var barClickTime = Date.now();
        if (clickedBarId && state.lastBarClickId === clickedBarId &&
            barClickTime - state.lastBarClickTime < 420) {
          state.lastBarClickId = "";
          state.lastBarClickTime = 0;
          state.selectedTaskId = clickedBarId;
          state.selectedTaskIds = [clickedBarId];
          state.lastAddedTaskId = "";
          openTaskDialog(clickedBarId);
          return;
        }
        state.lastBarClickId = clickedBarId;
        state.lastBarClickTime = barClickTime;
      }
      var clickedTitle = event.target.closest(".vtPmTaskTitleText");
      if (clickedTitle && !state.pendingScheduleCondition) {
        var clickedTitleRow = clickedTitle.closest(".vtPmTaskRow");
        var clickedTitleId = clickedTitleRow && clickedTitleRow.getAttribute("data-task-id") || "";
        var clickTime = Date.now();
        if (clickedTitleId && state.lastTitleClickId === clickedTitleId &&
            clickTime - state.lastTitleClickTime < 420) {
          state.lastTitleClickId = "";
          state.lastTitleClickTime = 0;
          state.selectedTaskId = clickedTitleId;
          state.selectedTaskIds = [clickedTitleId];
          openTaskDialog(clickedTitleId);
          return;
        }
        state.lastTitleClickId = clickedTitleId;
        state.lastTitleClickTime = clickTime;
      }
      var conditionRow = event.target.closest(".vtPmTaskRow");
      if (state.pendingScheduleCondition && conditionRow) {
        event.preventDefault();
        applyScheduleReference(conditionRow.getAttribute("data-task-id") || "");
        state.root.focus();
        return;
      }
      var issueIndicator = event.target.closest("[data-task-issues]");
      if (issueIndicator) {
        event.preventDefault();
        event.stopPropagation();
        openRelatedIssues(issueIndicator.getAttribute("data-task-issues") || "");
        return;
      }
      var resolvedIndicator = event.target.closest("[data-task-resolved]");
      if (resolvedIndicator) {
        event.preventDefault();
        event.stopPropagation();
        openRelatedIssues(resolvedIndicator.getAttribute("data-task-resolved") || "", true);
        return;
      }
      var button = event.target.closest("[data-action]");
      if (!button) {
        var row = event.target.closest(".vtPmTaskRow");
        if (row) {
          var clickedTaskId = row.getAttribute("data-task-id") || "";
          if (event.ctrlKey || event.metaKey) {
            var selectedIndex = state.selectedTaskIds.indexOf(clickedTaskId);
            if (selectedIndex >= 0) state.selectedTaskIds.splice(selectedIndex, 1);
            else state.selectedTaskIds.push(clickedTaskId);
            state.selectedTaskId = state.selectedTaskIds.indexOf(clickedTaskId) >= 0
              ? clickedTaskId
              : (state.selectedTaskIds[state.selectedTaskIds.length - 1] || "");
          } else {
            state.selectedTaskId = clickedTaskId;
            state.selectedTaskIds = clickedTaskId ? [clickedTaskId] : [];
          }
          state.lastAddedTaskId = "";
          renderBoard();
          state.root.focus();
        } else if (event.target.closest(".vtPmGridWrap")) {
          state.pendingScheduleCondition = null;
          state.selectedTaskId = "";
          state.selectedTaskIds = [];
          setStatus("Task selection cleared.", "info");
          renderBoard();
        }
        return;
      }
      var action = button.getAttribute("data-action");
      if (action === "new-project") openProjectDialog();
      if (action === "add-above") addTasksAt("above");
      if (action === "add-below") addTasksAt("below");
      if (action === "add-subtask") addTasksAt("subtask");
      if (action === "delete-selected") {
        if (state.selectedTaskId) deleteTask(state.selectedTaskId);
        else setStatus("Select a task row first", "warning");
      }
      if (action === "edit-task") openTaskDialog(button.getAttribute("data-task-id"));
      if (action === "delete-task") deleteTask(button.getAttribute("data-task-id"));
      if (action === "new-resource") openResourceDialog();
      if (action === "copy") copyTasksToClipboard("copy");
      if (action === "cut") copyTasksToClipboard("cut");
      if (action === "paste-child") pasteTasksFromClipboard("child");
      if (action === "paste-above") pasteTasksFromClipboard("above");
      if (action === "paste-below") pasteTasksFromClipboard("below");
      if (action === "confirm-copy") confirmCopiedTasks();
      if (action === "expand-all") {
        state.collapsedTaskIds = {};
        renderBoard();
      }
      if (action === "collapse-all") {
        var parentMap = {};
        (state.chart && state.chart.tasks || []).forEach(function (task) {
          if (task.parent_id) parentMap[task.parent_id] = true;
        });
        state.collapsedTaskIds = parentMap;
        renderBoard();
      }
      if (action === "edit-selected") {
        if (state.selectedTaskId) openTaskDialog(state.selectedTaskId);
        else setStatus("Select a task row first", "warning");
      }
      if (action === "save") saveChart();
      if (action === "history") openActivityHistory();
      if (action === "issues") openIssuesRegister();
      if (action === "check-conflicts") checkConflicts();
    });
    state.root.addEventListener("keydown", function (event) {
      var editableTarget = event.target.closest &&
        event.target.closest("input,textarea,select,[contenteditable='true']");
      if ((event.ctrlKey || event.metaKey) && !editableTarget) {
        if (event.key === "c" || event.key === "C") {
          event.preventDefault();
          copyTasksToClipboard("copy");
          return;
        }
        if (event.key === "x" || event.key === "X") {
          event.preventDefault();
          copyTasksToClipboard("cut");
          return;
        }
        if (event.key === "v" || event.key === "V") {
          event.preventDefault();
          pasteTasksFromClipboard("child");
          return;
        }
      }
      if (event.key === "Enter" && state.pendingScheduleCondition) {
        event.preventDefault();
        finalizeScheduleReferences();
        return;
      }
      if (event.key === "Escape" && state.pendingScheduleCondition) {
        state.pendingScheduleCondition = null;
        state.selectedTaskId = "";
        state.selectedTaskIds = [];
        setStatus("Schedule-condition selection cancelled.", "info");
        renderBoard();
        return;
      }
      if (event.key === "Escape" && state.selectedTaskIds.length) {
        state.selectedTaskId = "";
        state.selectedTaskIds = [];
        setStatus("Task selection cleared.", "info");
        renderBoard();
        return;
      }
      if (event.shiftKey && (event.key === "P" || event.key === "p")) {
        event.preventDefault();
        addTasksAt("subtask");
      }
    });
  }

  function mount(rootId) {
    var root = document.getElementById(rootId);
    if (!root) return;
    if (root.getAttribute("data-pm-mounted") === "true") return;
    root.setAttribute("data-pm-mounted", "true");
    state.root = root;
    root.innerHTML = shellHtml();
    bindEvents();
    renderEmpty();
    loadInitialData();
  }

  window.VTPM = { mount: mount };
})();
