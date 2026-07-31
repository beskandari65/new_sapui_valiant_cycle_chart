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
    dirty: false,
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
          '<div class="vtPmBrand"><strong>Project Management</strong><span>Working-day project schedule</span></div>',
          '<select id="vtPmProjectSelect" class="vtPmProjectSelect" aria-label="PM project">', projectOptions(), '</select>',
          '<button class="vtPmBtn vtPmBtnPrimary" data-action="new-project">+ New Project</button>',
          '<button class="vtPmBtn" data-action="new-task">+ New Task</button>',
          '<button class="vtPmBtn" data-action="new-resource">Resources</button>',
          '<span class="vtPmToolbarSpacer"></span>',
          '<button class="vtPmBtn" data-action="check-conflicts">Check Resources</button>',
          '<button class="vtPmBtn vtPmBtnSuccess" data-action="save">Save Project</button>',
        '</div>',
        '<div id="vtPmStatus" class="vtPmStatus"></div>',
        '<div id="vtPmEmpty" class="vtPmEmpty"></div>',
        '<div id="vtPmBoard" class="vtPmBoard"></div>',
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
      map[dependency.successor_id] = dependency;
    });
    return map;
  }

  function taskDepth(task, map) {
    var depth = 0;
    var parent = task.parent_id;
    var seen = {};
    while (parent && map[parent] && !seen[parent] && depth < 2) {
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
    min = addCalendarDays(min, -2);
    max = addCalendarDays(max, 5);
    return { min: min, max: max, days: Math.max(14, Math.round((max - min) / DAY_MS) + 1) };
  }

  function ganttAxis(bounds) {
    var html = '<div class="vtPmGanttAxis" style="width:' + (bounds.days * state.dayWidth) + 'px">';
    for (var i = 0; i < bounds.days; i++) {
      var day = addCalendarDays(bounds.min, i);
      var weekend = day.getUTCDay() === 0 || day.getUTCDay() === 6;
      html += '<div class="vtPmGanttDay' + (weekend ? " weekend" : "") + '" style="width:' +
        state.dayWidth + 'px">' + (day.getUTCMonth() + 1) + "/" + day.getUTCDate() + "</div>";
    }
    return html + "</div>";
  }

  function renderBoard() {
    if (!state.chart) return renderEmpty();
    var tasks = state.chart.tasks || [];
    tasks.forEach(function (task) { task.end_date = calculateEnd(task); });
    var map = taskMap();
    var assignments = assignmentMap();
    var dependencies = dependencyMap();
    var resources = {};
    state.resources.forEach(function (resource) { resources[resource.resource_id] = resource; });
    var conflictTasks = {};
    state.conflicts.forEach(function (warning) {
      (warning.task_ids || []).forEach(function (id) { conflictTasks[id] = true; });
    });
    var bounds = chartBounds(tasks);
    var project = state.chart.project || {};
    var totalProgress = tasks.length
      ? Math.round(tasks.reduce(function (sum, task) { return sum + Number(task.progress_percent || 0); }, 0) / tasks.length)
      : 0;
    var board = state.root.querySelector("#vtPmBoard");
    board.innerHTML = [
      '<div class="vtPmSummary">',
        '<span><strong>', escapeHtml(project.project_number || ""), "</strong> ", escapeHtml(project.title || ""), "</span>",
        '<span>Start: <strong>', escapeHtml(project.start_date || "Not set"), "</strong></span>",
        '<span>Tasks: <strong>', tasks.length, "</strong></span>",
        '<span>Average progress: <strong>', totalProgress, "%</strong></span>",
        '<span>Calendar: <strong>Working days (Mon-Fri)</strong></span>',
      '</div>',
      '<div class="vtPmGridWrap"><div class="vtPmGrid">',
        '<div class="vtPmGridHeader">',
          '<div class="vtPmCell vtPmIndex">#</div>',
          '<div class="vtPmCell">Task title</div>',
          '<div class="vtPmCell">Start</div>',
          '<div class="vtPmCell">Duration</div>',
          '<div class="vtPmCell">Progress</div>',
          '<div class="vtPmCell">Predecessor</div>',
          '<div class="vtPmCell">Internal resource</div>',
          '<div class="vtPmCell vtPmGanttHead">', ganttAxis(bounds), "</div>",
        "</div>",
        tasks.map(function (task, index) {
          var start = parseDate(task.start_date);
          var end = parseDate(task.end_date);
          var left = start ? Math.round((start - bounds.min) / DAY_MS) * state.dayWidth : 0;
          var width = start && end
            ? Math.max(8, (Math.round((end - start) / DAY_MS) + 1) * state.dayWidth)
            : 0;
          var assignment = assignments[task.task_id];
          var resource = assignment && resources[assignment.resource_id];
          var dependency = dependencies[task.task_id];
          var predecessor = dependency && map[dependency.predecessor_id];
          var progress = Math.max(0, Math.min(100, Number(task.progress_percent) || 0));
          return [
            '<div class="vtPmTaskRow', conflictTasks[task.task_id] ? " vtPmConflict" : "", '" data-task-id="', escapeHtml(task.task_id), '">',
              '<div class="vtPmCell vtPmIndex">', index + 1, "</div>",
              '<div class="vtPmCell vtPmTaskTitle" data-depth="', taskDepth(task, map), '">',
                '<span>', escapeHtml(task.title), "</span>",
                '<span class="vtPmRowActions">',
                  '<button class="vtPmIconBtn" data-action="edit-task" data-task-id="', escapeHtml(task.task_id), '" title="Edit task">Edit</button>',
                  '<button class="vtPmIconBtn vtPmBtnDanger" data-action="delete-task" data-task-id="', escapeHtml(task.task_id), '" title="Delete task">Delete</button>',
                "</span>",
              "</div>",
              '<div class="vtPmCell">', escapeHtml(task.start_date || "—"), "</div>",
              '<div class="vtPmCell">', escapeHtml(task.duration_value), " working day", Number(task.duration_value) === 1 ? "" : "s", "</div>",
              '<div class="vtPmCell"><div class="vtPmProgressTrack"><div class="vtPmProgressFill" style="width:', progress, '%"></div></div><span class="vtPmProgressText">', progress, "%</span></div>",
              '<div class="vtPmCell">', predecessor ? escapeHtml(predecessor.title) : '<span class="vtPmMuted">None</span>', "</div>",
              '<div class="vtPmCell">', resource ? escapeHtml(resource.name) : '<span class="vtPmMuted">Unassigned</span>', "</div>",
              '<div class="vtPmCell vtPmGanttCell" style="width:', bounds.days * state.dayWidth, 'px">',
                width ? '<div class="vtPmBar" style="left:' + left + 'px;width:' + width + 'px;background:' + escapeHtml(task.color || "#0a6ed1") + '" title="' + escapeHtml(task.title + ": " + task.start_date + " to " + task.end_date) + '"><div class="vtPmBarProgress" style="width:' + progress + '%"></div><span class="vtPmBarLabel">' + escapeHtml(task.title) + "</span></div>" : "",
              "</div>",
            "</div>"
          ].join("");
        }).join(""),
      "</div></div>"
    ].join("");
    renderEmpty();
  }

  function renderProjectSelect() {
    var select = state.root.querySelector("#vtPmProjectSelect");
    if (select) select.innerHTML = projectOptions();
  }

  function modal(title, body, onSave, saveText) {
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
    wrapper.querySelector("[data-modal-cancel]").addEventListener("click", close);
    wrapper.addEventListener("click", function (event) {
      if (event.target === wrapper) close();
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

  function openTaskDialog(taskId) {
    if (!state.chart) return openProjectDialog();
    var existing = (state.chart.tasks || []).find(function (task) { return task.task_id === taskId; });
    var assignment = (state.chart.assignments || []).find(function (item) { return item.task_id === taskId; });
    var dependency = (state.chart.dependencies || []).find(function (item) { return item.successor_id === taskId; });
    var defaultStart = state.chart.project.start_date || isoDate(new Date());
    modal(existing ? "Edit task" : "Add task", [
      '<div class="vtPmField vtPmFieldFull"><label>Task title</label><input id="pmTaskTitle" value="', escapeHtml(existing && existing.title || ""), '" required></div>',
      '<div class="vtPmField"><label>Start date</label><input id="pmTaskStart" type="date" value="', escapeHtml(existing && existing.start_date || defaultStart), '"></div>',
      '<div class="vtPmField"><label>Task Duration (working days)</label><input id="pmTaskDuration" type="number" min="0" step="1" value="', escapeHtml(existing && existing.duration_value != null ? existing.duration_value : 1), '"></div>',
      '<div class="vtPmField"><label>Progress (%)</label><input id="pmTaskProgress" type="number" min="0" max="100" value="', escapeHtml(existing && existing.progress_percent || 0), '"></div>',
      '<div class="vtPmField"><label>Parent task</label><select id="pmTaskParent">', selectOptions(state.chart.tasks || [], "task_id", "title", existing && existing.parent_id, "No parent", taskId), "</select></div>",
      '<div class="vtPmField"><label>Predecessor (Finish-to-Start)</label><select id="pmTaskPredecessor">', selectOptions(state.chart.tasks || [], "task_id", "title", dependency && dependency.predecessor_id, "No predecessor", taskId), "</select></div>",
      '<div class="vtPmField"><label>Internal resource</label><select id="pmTaskResource">', selectOptions(state.resources, "resource_id", "name", assignment && assignment.resource_id, "Unassigned"), "</select></div>",
      '<div class="vtPmField"><label>Allocation</label><input id="pmTaskAllocation" type="number" min="0.1" step="0.1" value="', escapeHtml(assignment && assignment.allocation || 1), '"></div>'
    ].join(""), function (dialog) {
      var title = dialog.querySelector("#pmTaskTitle").value.trim();
      if (!title) throw new Error("Task title is required");
      var id = existing ? existing.task_id : uid("pmtask");
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

      state.chart.dependencies = (state.chart.dependencies || []).filter(function (item) {
        return item.successor_id !== id;
      });
      var predecessor = dialog.querySelector("#pmTaskPredecessor").value;
      if (predecessor) {
        state.chart.dependencies.push({
          predecessor_id: predecessor,
          successor_id: id,
          dependency_type: "FS",
          lag_value: 0,
          lag_unit: "working_days"
        });
      }

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
      markDirty();
      renderBoard();
    }, existing ? "Update Task" : "Add Task");
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
    state.chart.tasks = state.chart.tasks.filter(function (task) { return !ids[task.task_id]; });
    state.chart.dependencies = state.chart.dependencies.filter(function (dep) {
      return !ids[dep.predecessor_id] && !ids[dep.successor_id];
    });
    state.chart.assignments = state.chart.assignments.filter(function (assignment) {
      return !ids[assignment.task_id];
    });
    markDirty();
    renderBoard();
  }

  function saveChart() {
    if (!state.chart || !state.projectId) return Promise.resolve();
    state.chart.tasks.forEach(function (task, index) {
      task.tree_index = index;
      task.end_date = calculateEnd(task);
      task.duration_unit = "working_days";
    });
    setStatus("Saving project...", "info");
    return request("/projects/" + encodeURIComponent(state.projectId) + "/chart", {
      method: "PUT",
      body: JSON.stringify({
        project: state.chart.project,
        tasks: state.chart.tasks,
        dependencies: state.chart.dependencies,
        assignments: state.chart.assignments,
        deliverables: state.chart.deliverables || []
      })
    }).then(function (chart) {
      state.chart = chart;
      state.dirty = false;
      setStatus("Project saved", "success");
      renderBoard();
      return chart;
    }).catch(function (error) {
      setStatus(error.message, "error");
      throw error;
    });
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
    first.then(function () {
      return request("/projects/" + encodeURIComponent(state.projectId) + "/resource-conflicts");
    }).then(function (result) {
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
      renderProjectSelect();
      renderEmpty();
      return Promise.resolve();
    }
    setStatus("Loading project...", "info");
    return request("/projects/" + encodeURIComponent(projectId) + "/chart").then(function (chart) {
      state.projectId = projectId;
      state.chart = chart;
      state.conflicts = [];
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
      renderEmpty();
      setStatus("", "");
    }).catch(function (error) {
      setStatus(error.message, "error");
    });
  }

  function bindEvents() {
    state.root.addEventListener("change", function (event) {
      if (event.target.id === "vtPmProjectSelect") {
        if (state.dirty && !window.confirm("Discard unsaved PM changes and load another project?")) {
          event.target.value = state.projectId;
          return;
        }
        loadChart(event.target.value);
      }
    });
    state.root.addEventListener("click", function (event) {
      var button = event.target.closest("[data-action]");
      if (!button) return;
      var action = button.getAttribute("data-action");
      if (action === "new-project") openProjectDialog();
      if (action === "new-task") openTaskDialog("");
      if (action === "edit-task") openTaskDialog(button.getAttribute("data-task-id"));
      if (action === "delete-task") deleteTask(button.getAttribute("data-task-id"));
      if (action === "new-resource") openResourceDialog();
      if (action === "save") saveChart();
      if (action === "check-conflicts") checkConflicts();
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

