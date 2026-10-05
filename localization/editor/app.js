/* Local editor UI. All writes go through the token-protected localhost API. */
"use strict";
const token = document.querySelector('meta[name="editor-token"]').content;
const el = id => document.getElementById(id);
const translatorColumns = [
  ["key", "Key"], ["classification", "Type"], ["vanilla_en_US", "Vanilla EN"],
  ["vanilla_en_US_characters", "Vanilla EN characters"],
  ["vanilla_target", "Vanilla translation"], ["lekmod_en_US", "Lekmod EN"],
  ["lekmod_en_US_characters", "Lekmod EN characters"],
  ["lekmod_target", "Existing Lekmod translation"], ["translation", "My translation"],
  ["translation_status", "Status"], ["translation_characters", "My characters"],
  ["english_edited_at", "English edited"], ["translation_updated_at", "Translation edited"],
  ["translator_note", "Translator note"], ["changed_in", "Changed in Lekmod"]
];
const developerColumns = [["key", "Key"], ["kind", "Operation"],
  ["text", "English text"], ["characters", "Characters"],
  ["english_edited_at", "English edited"], ["source_file", "Source file"],
  ["source_line", "Line"], ["changed_in", "Changed in Lekmod"]];
const copyFields = new Set(["key", "vanilla_en_US", "vanilla_target",
  "lekmod_en_US", "lekmod_target", "text", "source_file"]);
const dateFields = new Set(["english_edited_at", "translation_updated_at"]);
function localEditTime(value) {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value.split("-").reverse().join(".");
  // A timestamp without an offset is ambiguous when contributors live abroad.
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return "Time zone not recorded";
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "Date unavailable";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit",
    minute: "2-digit", hourCycle: "h23"
  }).formatToParts(instant).map(part => [part.type, part.value]));
  return `${parts.day}.${parts.month}.${parts.year} / ${parts.hour}:${parts.minute}`;
}
function editTimeTooltip(value) {
  const instant = new Date(value);
  if (!value || Number.isNaN(instant.getTime()) || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return value;
  return new Intl.DateTimeFormat(undefined, {dateStyle: "long", timeStyle: "long"}).format(instant) +
    " · " + Intl.DateTimeFormat().resolvedOptions().timeZone;
}
function localDateBoundary(value, end = false) {
  const [year, month, day] = value.split("-").map(Number);
  // Next local midnight is exclusive, including 23/25-hour daylight-saving days.
  return new Date(year, month - 1, day + (end ? 1 : 0)).toISOString();
}
let meta, prefs, locales = {}, chosen = null, offset = 0, total = 0;
let visible = new Set(), widths = {}, searchTimer, requestId = 0;
let toastTimer;
let inLogs = false;
let downloadTimer;
let sourceVersions = null, sourceVersionRequest = null;
let gameStatusRequest = false;
let editorUpdateTimer, editorUpdateStarted = 0, editorUpdateVersion = "";
let editorUpdateLocked = false, settingsControlsBeforeUpdate = new Map();
let editorUpdateInstance = "", editorIntegrity = null, latestEditorInfo = null;
let savedDraft = null, pendingNavigation = null, committedSearch = "", guardSaving = false;
let pendingColumns = null;
let savePending = null, failedSaves = [];
try { failedSaves = JSON.parse(sessionStorage.getItem("failed-translation-drafts") || "[]"); }
catch (error) { failedSaves = []; }
if (!Array.isArray(failedSaves)) failedSaves = [];
function rememberFailedSaves() {
  try { sessionStorage.setItem("failed-translation-drafts", JSON.stringify(failedSaves)); }
  catch (error) { message("Could not preserve failed drafts in browser storage.", true); }
  el("restore-failed").hidden = !failedSaves.length;
}
let saveCompletion = null, resolveSaveCompletion = null;
let mergeReview = null, mergeChoices = {}, mergePage = 0, inMerge = false;
let mergeApplying = false, mergeChecking = false;
let filters = {kind: "", status: "", date_field: "english_edited_at", date_from: "", date_to: "", version: "", needs_translation: ""};
function logUI(name, detail = "") { api("/api/event", {name, detail}).catch(() => {}); }

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Editor-Token": token},
    body: JSON.stringify(body)
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
function message(value, error = false) {
  el("message").textContent = value;
  el("message").style.color = error ? "#a22d24" : "#246a39";
  if (!value) return;
  if (error) logUI("ui-error", value);
  const toast = el("toast");
  toast.textContent = value;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 5500 : 3200);
}
function sectionMessage(section, value, tone = "success") {
  const target = el(section + "-status");
  const next = value ? ({success: "✓ ", warning: "⚠ ", error: "✕ "}[tone] || "") + value : "";
  if (value && target.textContent !== next && ["warning", "error"].includes(tone))
    logUI("ui-" + tone, section + ": " + value);
  target.textContent = next;
  target.className = "section-status " + tone;
}
function showConnectionLoading(value, failed = false) {
  if (el("settings-dialog").open) el("settings-dialog").close();
  el("workspace").hidden = true;
  el("no-source").hidden = true;
  el("app-loading").hidden = false;
  el("loading-label").textContent = value;
  el("loading-spinner").hidden = failed;
  el("loading-retry").hidden = !failed;
}
function pageSize() { return prefs.page_size || "100"; }
function pageStep() { return pageSize() === "all" ? total : Number(pageSize()); }
async function preference(values) {
  const result = await api("/api/preferences", values);
  prefs = result.preferences;
}
function developer() { return prefs.mode === "developer"; }
function activeColumns() { return developer() ? developerColumns : translatorColumns; }
function columnsPreference() { return developer() ? "developer_visible_columns" : "translator_visible_columns"; }
function savedColumns() {
  if (prefs[columnsPreference()].length) return prefs[columnsPreference()];
  const specific = developer() ? ["kind", "text", "source_file"]
    : ["classification", "lekmod_target", "translation"];
  return prefs.visible_columns.some(field => specific.includes(field)) ? prefs.visible_columns : [];
}
function columnTitle(field, title) {
  return field === "vanilla_target" ? "Vanilla " + (el("locale").value.split("_")[0] || "language") : title;
}
function captureDraft() {
  return JSON.stringify({text: el("translation").value,
    gender: grammarValue("gender"), plurality: grammarValue("plurality"),
    note: el("note").value, identifier: developer() ? el("identifier").value : ""});
}
function hasUnsaved() { return !!chosen && savedDraft !== null && captureDraft() !== savedDraft; }
function markDraft() {
  el("discard").disabled = !hasUnsaved();
  el("save").disabled = !hasUnsaved() || !!savePending;
}
function restoreDraft() {
  if (!savedDraft) return;
  const draft = JSON.parse(savedDraft);
  el("translation").value = draft.text;
  setGrammar("gender", draft.gender);
  setGrammar("plurality", draft.plurality);
  el("note").value = draft.note;
  el("identifier").value = draft.identifier;
  countText(); markDraft();
}
async function guardNavigation(action) {
  if (!hasUnsaved()) return action();
  if (el("unsaved-dialog").open) return;
  pendingNavigation = action;
  el("unsaved-dialog").showModal();
}
async function continueNavigation(save) {
  if (guardSaving) return;
  if (save) {
    guardSaving = true;
    for (const id of ["unsaved-save", "unsaved-discard", "unsaved-keep"]) el(id).disabled = true;
    el("unsaved-save").classList.add("busy-action");
    let saved;
    try {
      if (saveCompletion) {
        message("Waiting for the previous row to finish saving…");
        await saveCompletion;
      }
      saved = await saveCurrent();
    }
    finally {
      guardSaving = false;
      for (const id of ["unsaved-save", "unsaved-discard", "unsaved-keep"]) el(id).disabled = false;
      el("unsaved-save").classList.remove("busy-action");
    }
    if (!saved) return;
  }
  if (!save) restoreDraft();
  const next = pendingNavigation;
  pendingNavigation = null;
  el("unsaved-dialog").close();
  if (next) await next();
}
function countText() {
  const value = el("translation").value;
  el("characters").textContent = "Characters: " + Array.from(value).length +
    " · spaces: " + (value.match(/ /g) || []).length +
    (chosen && !developer() ? " · English: " + chosen.lekmod_en_US_characters : "");
}
function grammarValue(name) {
  return el(name).value === "__custom__" ? el(name + "-custom").value.trim() : el(name).value;
}
function setGrammar(name, value) {
  const select = el(name), custom = el(name + "-custom");
  select.value = Array.from(select.options).some(option => option.value === value)
    ? value : "__custom__";
  custom.hidden = select.value !== "__custom__";
  custom.disabled = custom.hidden || select.disabled;
  custom.value = custom.hidden ? "" : value;
}
async function copyText(value) {
  try { await navigator.clipboard.writeText(value); logUI("copy"); message("Copied to clipboard."); }
  catch (error) { message("Clipboard access was denied by the browser.", true); }
}
function updateHistory(state) {
  el("undo").disabled = !!savePending || !state.undo_available;
  el("redo").disabled = !!savePending || !state.redo_available;
}
function renderConnections() {
  const project = meta.project;
  el("source-badge").textContent = project
    ? "Source: " + (meta.included_source ? "included " : "project ") + meta.release
    : "Source: disconnected";
  el("source-badge").classList.toggle("missing", !project);
  const game = meta.game;
  const selected = game.mods.find(item => item.name === game.selected_mod);
  const matches = game.state === "installed" && selected && project && selected.version.toLowerCase() ===
    project.version.toLowerCase() && selected.release.toLowerCase() === meta.release.toLowerCase();
  el("game-badge").textContent = matches ? "Game: " + selected.name + " · matched"
    : game.state === "vanilla" ? "Game: Lekmod not installed"
    : game.state === "damaged" ? "Game: Lekmod files invalid"
    : game.state === "mismatch" ? "Game: version or rules mismatch"
    : game.state === "multiple" ? "Game: multiple Lekmod copies"
    : game.path ? "Game: invalid folder" : "Game: disconnected";
  el("game-badge").classList.toggle("missing", !matches);
  const changedGameFolder = el("settings-dialog").open &&
    el("game-path").value !== (prefs.game_path || game.path);
  el("game-apply").disabled = !!savePending || !matches || !meta.ready || changedGameFolder;
  el("workspace").hidden = !meta.ready || inLogs || inMerge;
  el("merge-view").hidden = !meta.ready || inLogs || !inMerge;
  el("no-source").hidden = meta.ready || inLogs || inMerge;
  el("connection-error").textContent = meta.connection_error || "";
}
function updateBaselineNotice() {
  const notice = el("baseline-notice");
  if (developer()) { notice.hidden = true; return; }
  const locale = el("locale").value, counts = meta.vanilla_counts;
  notice.hidden = false;
  if (!Object.keys(counts).length) {
    notice.textContent = meta.snapshot_error ||
      "Original vanilla sentences are unavailable. Import the matching snapshot in Settings to compare them.";
  } else if (!counts[locale]) {
    notice.textContent = "The pinned vanilla snapshot contains no " + locale +
      " entries. An empty vanilla cell does not mean the game has no translation.";
  } else {
    notice.hidden = true;
  }
}
function changeMode() {
  el("mode-name").textContent = developer() ? "Developer" : "Translator";
  el("mode").dataset.mode = developer() ? "developer" : "translator";
  el("mode-symbol").setAttribute("d", developer()
    ? "m8 5-6 7 6 7m8-14 6 7-6 7M14 3l-4 18"
    : "M4 5h13a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9l-4 3v-3H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm2 9 2.5-6 2.5 6m-4-2h3m3-4h4m-2 0c0 3-1 5-3 6m1-4c.5 2 1.5 3 3 4");
  el("mode").setAttribute("aria-pressed", developer() ? "true" : "false");
  el("locale-label").hidden = developer();
  el("category-label").hidden = developer();
  el("key-tools").hidden = !developer();
  el("create-key").hidden = !developer();
  el("grammar").hidden = developer();
  el("note-help").hidden = developer();
  el("run-checks").hidden = !developer();
  el("text-heading").textContent = developer() ? "English source text" : "My translation text";
  el("text-help").textContent = developer()
    ? "Saved in primary.xml; changes make previous translations stale."
    : "Saved in the language CSV; the project's game XML is rebuilt.";
  el("save").textContent = "Save and apply";
  el("share").hidden = developer();
  el("share-english").hidden = !developer();
  updateBaselineNotice();
  offset = 0;
  const saved = savedColumns();
  visible = new Set(saved.length ? saved
    : [...translatorColumns, ...developerColumns].map(item => item[0]));
  if (!saved.length) visible.delete("vanilla_target");
  if (!activeColumns().some(([field]) => visible.has(field)))
    visible = new Set(activeColumns().map(([field]) => field));
  if (!developer() && !saved.length) {
    if (!Object.keys(meta.vanilla_counts).length) {
      visible.delete("vanilla_en_US"); visible.delete("vanilla_en_US_characters");
      visible.delete("vanilla_target");
    } else if (!meta.vanilla_counts[el("locale").value]) {
      visible.delete("vanilla_target");
    }
  }
  renderColumnChoices();
  renderFilterChoices();
  load();
}
function renderFilterChoices() {
  const select = el("filter-kind");
  select.replaceChildren(new Option("Any", ""));
  for (const item of (developer() ? ["Row", "Replace"] : ["lekmod_new", "vanilla_modified", "source_conflict"]))
    select.append(new Option(item, item));
  el("filter-kind-title").textContent = developer() ? "Operation" : "Type";
  el("filter-status-label").hidden = developer();
  el("filter-date-field").querySelector('option[value="translation_updated_at"]').hidden = developer();
  if (developer()) filters.date_field = "english_edited_at";
  select.value = filters.kind;
  el("filter-status").value = filters.status;
  el("filter-date-field").value = filters.date_field;
  el("filter-from").value = filters.date_from;
  el("filter-to").value = filters.date_to;
  el("filter-version").replaceChildren(new Option("Any version", ""));
  if (meta.version_history?.upgrade_versions?.length)
    el("filter-version").append(new Option("Since last project update", "upgrade"));
  for (const version of meta.version_history?.synced || [])
    el("filter-version").append(new Option(version, version));
  el("filter-version").value = filters.version || "";
  el("filter-needs").checked = filters.needs_translation === "true";
  el("filter-needs").parentElement.hidden = developer();
  el("filters-button").classList.toggle("active", Object.entries(filters).some(
    ([key, value]) => key !== "date_field" && !!value));
}
function categories() {
  const select = el("category");
  select.replaceChildren(new Option("All categories", "all"));
  for (const name of locales[el("locale").value] || []) {
    const option = document.createElement("option");
    option.value = name; option.textContent = name.replace(/_/g, " ");
    select.append(option);
  }
  select.value = prefs.category === "all" || (locales[el("locale").value] || []).includes(prefs.category)
    ? prefs.category : "all";
  updateBaselineNotice();
}
function renderColumnChoices() {
  const container = el("column-choices");
  container.replaceChildren();
  const groups = developer()
    ? [["English source", ["key", "kind", "text", "characters"]],
       ["File and history", ["source_file", "source_line", "english_edited_at", "changed_in"]]]
    : [["Identity", ["key", "classification"]],
       ["Vanilla reference", ["vanilla_en_US", "vanilla_en_US_characters", "vanilla_target"]],
       ["Lekmod source", ["lekmod_en_US", "lekmod_en_US_characters", "lekmod_target"]],
       ["Translation and review", ["translation", "translation_status", "translation_characters",
         "english_edited_at", "translation_updated_at", "translator_note", "changed_in"]]];
  const fields = new Map(activeColumns());
  for (const [heading, names] of groups) {
    const group = document.createElement("div");
    group.className = "column-group";
    const title = document.createElement("h3");
    title.textContent = heading;
    const choices = document.createElement("div");
    choices.className = "column-choices";
    for (const field of names) {
      const label = document.createElement("label");
      label.className = "column-choice";
      const check = document.createElement("input");
      check.type = "checkbox"; check.checked = (pendingColumns || visible).has(field);
      if (field === "vanilla_target" && !meta.vanilla_counts[el("locale").value]) {
        check.disabled = true;
        label.title = "No original vanilla text for this language in the shared snapshot.";
      }
      check.addEventListener("change", () => {
        if (!pendingColumns) return;
        check.checked ? pendingColumns.add(field) : pendingColumns.delete(field);
        if (!pendingColumns.size) {
          pendingColumns.add(field); check.checked = true;
          message("Keep at least one visible column.", true);
        }
      });
      label.append(check, document.createTextNode(columnTitle(field, fields.get(field))));
      choices.append(label);
    }
    group.append(title, choices);
    container.append(group);
  }
}
function colWidth(field) {
  return widths[field] || (field === "key" ? 255 :
    ["lekmod_en_US", "lekmod_target", "text"].includes(field) ? 490 : 245);
}
function renderTable(rows) {
  window.currentRows = rows;
  const cols = activeColumns().filter(([field]) => visible.has(field));
  const group = el("table").querySelector("colgroup");
  const head = el("table").querySelector("thead");
  const body = el("table").querySelector("tbody");
  group.replaceChildren(); head.replaceChildren(); body.replaceChildren();
  const header = document.createElement("tr");
  for (const [field, title] of cols) {
    const col = document.createElement("col");
    col.style.width = colWidth(field) + "px";
    group.append(col);
    const th = document.createElement("th");
    th.textContent = columnTitle(field, title);
    const handle = document.createElement("span");
    handle.className = "resizer";
    handle.setAttribute("role", "separator");
    handle.title = "Drag to resize";
    handle.addEventListener("pointerdown", event => {
      event.preventDefault(); event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
      const start = event.clientX, original = colWidth(field);
      function move(pointer) {
        widths[field] = Math.round(Math.max(100, Math.min(1500,
          original + pointer.clientX - start)));
        col.style.width = widths[field] + "px";
        tableWidth(cols);
      }
      function end() {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", end);
        preference({column_widths: widths}).catch(err => message(err.message, true));
        logUI("columns-changed");
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end);
    });
    th.append(handle); header.append(th);
  }
  head.append(header);
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (savePending && savePending.key === row.key &&
        savePending.locale === el("locale").value && !developer()) {
      tr.classList.add("saving"); tr.setAttribute("aria-busy", "true");
      tr.title = "This row is saving; you can edit other rows.";
    }
    for (const [field] of cols) {
      const td = document.createElement("td");
      const value = String(row[field] ?? "");
      if (value && copyFields.has(field)) {
        const content = document.createElement("div");
        content.className = "cell";
        const span = document.createElement("span");
        span.className = "cell-text";
        span.textContent = value; span.title = value;
        const copy = document.createElement("button");
        copy.className = "copy"; copy.type = "button"; copy.title = "Copy " + field;
        copy.setAttribute("aria-label", "Copy " + field);
        copy.textContent = "⧉";
        copy.addEventListener("click", async event => {
          event.stopPropagation();
          await copyText(value);
        });
        content.append(span, copy); td.append(content);
      } else {
        td.textContent = dateFields.has(field) ? localEditTime(value) : value;
        if (value) td.title = dateFields.has(field) ? editTimeTooltip(value) : value;
      }
      tr.append(td);
    }
    tr.addEventListener("click", () => {
      if (tr.classList.contains("saving")) return;
      if (!hasUnsaved()) { selectRow(row, tr); return; }
      guardNavigation(async () => {
        await load();
        const index = (window.currentRows || []).findIndex(item => item.key === row.key);
        if (index >= 0) selectRow(window.currentRows[index],
          el("table").querySelectorAll("tbody tr")[index]);
        else message("That row is no longer visible. Select it again from the table.");
      });
    });
    body.append(tr);
  }
  tableWidth(cols);
}
function tableWidth(cols) {
  el("table").style.width = cols.reduce((sum, [field]) => sum + colWidth(field), 0) + "px";
}
function selectRow(row, tr) {
  if (savePending && !developer() && row.key === savePending.key &&
      el("locale").value === savePending.locale) return;
  logUI("row-selected");
  chosen = row;
  document.querySelectorAll("tbody tr").forEach(item => item.classList.remove("selected"));
  tr.classList.add("selected");
  const locale = el("locale").value;
  el("selected").textContent = row.key + (developer() ? " · English source" : " · " + locale);
  const sourceBlocked = !developer() && row.classification === "source_conflict";
  el("translation").disabled = sourceBlocked;
  el("discard").disabled = true;
  el("identifier").disabled = !developer();
  for (const field of ["gender", "plurality", "note"]) el(field).disabled = developer() || sourceBlocked;
  if (el("prefill").checked) {
    el("translation").value = developer() ? row.text
      : row.translation || row.lekmod_target || "";
  } else {
    el("translation").value = "";
  }
  setGrammar("gender", row.translation_gender || "");
  setGrammar("plurality", row.translation_plurality || "");
  el("note").value = row.translator_note || "";
  el("identifier").value = developer() ? row.key : "";
  countText();
  savedDraft = captureDraft();
  markDraft();
  if (developer()) {
    el("context").textContent = "English edited: " + (localEditTime(row.english_edited_at) || "unknown") +
      ". Existing translations become stale when this text changes. New keys need a gameplay reference.";
  } else {
    el("context").textContent = "Type: " + row.classification + " · status: " +
      row.translation_status + " · English edited: " + (localEditTime(row.english_edited_at) || "unknown") +
      " · translation edited: " + (localEditTime(row.translation_updated_at) || "unknown") +
      (sourceBlocked ? " · Conflicting English sources need a developer review before translation." : "") +
      (row.translation_status === "stale"
        ? " · Game text falls back to English until reviewed." :
        row.translation_status === "applied" ? " · Applied to the project XML; game install is separate." : "");
  }
  {
    let tokens = {};
    try { tokens = developer() ? row.required_format_tokens : JSON.parse(row.required_format_tokens); } catch (error) {}
    tokens = tokens || {};
    const entries = Object.entries(tokens);
    el("token-help").hidden = !entries.length;
    el("tokens").replaceChildren();
    for (const [name, amount] of entries) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.title = "Copy token " + name;
      chip.textContent = name + (amount > 1 ? " × " + amount : "");
      chip.addEventListener("click", () => copyText(name));
      el("tokens").append(chip);
    }
  }
}
async function load() {
  if (!meta.ready) return;
  const sequence = ++requestId;
  clearSelection();
  el("table-loading").hidden = false;
  el("table-scroll").setAttribute("aria-busy", "true");
  const args = new URLSearchParams({q: el("search-input").value, offset,
    limit: pageSize(), kind: filters.kind, date_field: filters.date_field,
    date_from: filters.date_from, date_to: filters.date_to, version: filters.version,
    needs_translation: filters.needs_translation});
  if (filters.date_from) args.set("date_start_utc", localDateBoundary(filters.date_from));
  if (filters.date_to) args.set("date_end_utc", localDateBoundary(filters.date_to, true));
  if (!developer()) {
    args.set("locale", el("locale").value);
    args.set("category", el("category").value);
    args.set("status", filters.status);
  }
  try {
    const result = await api((developer() ? "/api/primary?" : "/api/rows?") + args);
    if (sequence !== requestId) return;
    total = result.total;
    renderTable(result.rows);
    committedSearch = el("search-input").value;
    el("count").textContent = total ? (offset + 1) + "–" + Math.min(offset + result.rows.length, total) +
      " of " + total : "No rows";
    el("prev").disabled = offset === 0 || pageSize() === "all";
    el("next").disabled = offset + pageStep() >= total || pageSize() === "all";
  } catch (error) { if (sequence === requestId) message(error.message, true); }
  finally {
    if (sequence === requestId) {
      el("table-loading").hidden = true;
      el("table-scroll").removeAttribute("aria-busy");
    }
  }
}
function clearSelection() {
  chosen = null;
  savedDraft = null;
  el("selected").textContent = "Select a row";
  el("context").textContent = "";
  el("tokens").replaceChildren();
  el("token-help").hidden = true;
  el("translation").value = "";
  el("identifier").value = "";
  el("note").value = "";
  el("save").disabled = true;
  el("discard").disabled = true;
  el("identifier").disabled = true;
  el("translation").disabled = true;
  for (const field of ["gender", "plurality", "note", "gender-custom", "plurality-custom"])
    el(field).disabled = true;
  setGrammar("gender", "");
  setGrammar("plurality", "");
  document.querySelectorAll("tbody tr.selected").forEach(row => row.classList.remove("selected"));
  countText();
}
function fillSettings() {
  el("project-path").value = prefs.project_path;
  el("game-path").value = prefs.game_path || meta.game.path;
  el("snapshot-url").value = prefs.snapshot_url || "";
  el("snapshot-password").value = "";
  el("history-version").replaceChildren();
  for (const version of meta.version_history?.available || [])
    el("history-version").append(new Option(version, version));
  el("carry-translations").disabled = !meta.ready;
  el("snapshot-new-password").value = "";
  el("snapshot-confirm-password").value = "";
  sectionMessage("source", meta.ready ? "Compatible source connected: " + meta.release :
    "Choose a complete Lekmod source, or download a compatible version.",
    meta.ready ? "success" : "warning");
  sectionMessage("game", meta.game.error || (meta.game.state === "installed" ?
    "Lekmod " + meta.game.mods[0].version + " is installed here." :
    meta.game.state === "vanilla" ? "Civilization V found; Lekmod is not installed." :
      "No game is connected; editing the project still works."),
    meta.game.state === "installed" ? "success" :
      ["vanilla", "multiple", "mismatch"].includes(meta.game.state) ? "warning" :
      meta.game.error ? "error" : "warning");
  el("game-launcher-link").hidden = !["vanilla", "mismatch", "damaged", "multiple"].includes(meta.game.state);
  refreshGameConnection();
  const counts = Object.values(meta.vanilla_counts || {}).reduce((sum, value) => sum + value, 0);
  el("vanilla-summary").textContent = counts ?
    "Original vanilla text available for comparison." :
    (meta.snapshot_error || "Optional comparison: original vanilla text is not loaded.");
  sectionMessage("snapshot", "");
  updateDownload();
  if (!downloadTimer) downloadTimer = setInterval(updateDownload, 1000);
  if (sourceVersions) showSourceVersions(sourceVersions);
  refreshSourceVersions();
}
function showSourceVersions(result) {
  const select = el("project-version"), chosenVersion = select.value || meta?.release;
  select.replaceChildren();
  for (const item of result.versions) {
    const option = new Option(item.version + (item.date ? " · " + item.date : "") +
      (item.supported ? "" : " · needs migration"), item.version);
    option.disabled = !item.supported;
    select.append(option);
  }
  if (result.versions.some(item => item.version === chosenVersion && item.supported))
    select.value = chosenVersion;
  const current = meta?.ready ? meta.release : "not connected";
  const latest = result.versions.find(item => item.supported)?.version;
  const parts = version => version.replace(/^v/, "").split(".").map(Number);
  const newer = (left, right) => {
    const a = parts(left), b = parts(right);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) > (b[i] || 0);
    }
    return false;
  };
  const available = meta?.ready && latest && newer(latest, current);
  const text = "Connected Lekmod: " + current + ". " + (available ?
    "Lekmod " + latest + " is available. Select it, Download/Update, then Save connections to switch. You can keep working on " + current + "." :
    latest ? "Latest available: " + latest + "." : "No release list is available.") +
    (result.warning ? " " + result.warning : "");
  const status = el("source-version-status");
  status.className = "section-status " + (available || result.warning ? "warning" : "success");
  status.textContent = (available || result.warning ? "⚠ " : "✓ ") + text;
}
async function refreshSourceVersions(force = false) {
  if (editorUpdateLocked) return;
  if (sourceVersionRequest) return sourceVersionRequest;
  const button = el("source-check"), status = el("source-version-status");
  button.disabled = true;
  status.className = "section-status busy-inline";
  status.textContent = "Checking available Lekmod versions…";
  sourceVersionRequest = (async () => {
    try {
      const result = await api("/api/versions" + (force ? "?refresh=1" : ""));
      sourceVersions = result;
      if (!editorUpdateLocked) showSourceVersions(result);
    } catch (error) {
      if (!editorUpdateLocked) {
        status.className = "section-status warning";
        status.textContent = "⚠ Lekmod version check failed: " + error.message;
      }
      logUI("source-version-error", error.message);
    } finally {
      if (editorUpdateLocked) settingsControlsBeforeUpdate.set(button, false);
      else button.disabled = false;
      sourceVersionRequest = null;
    }
  })();
  return sourceVersionRequest;
}
el("source-check").addEventListener("click", () => {
  logUI("source-version-check"); refreshSourceVersions(true);
});
setInterval(() => refreshSourceVersions(), 600000);
async function refreshGameConnection() {
  if (!meta?.ready || gameStatusRequest || editorUpdateLocked || savePending || mergeApplying) return;
  gameStatusRequest = true;
  try {
    const game = await api("/api/game-status");
    if (editorUpdateLocked) return;
    meta.game = game;
    renderConnections();
    // Do not replace feedback about a newly browsed, not-yet-saved folder.
    if (el("settings-dialog").open && el("game-path").value === (prefs.game_path || game.path)) {
      sectionMessage("game", game.error || (game.state === "installed" ?
        "Civilization V and matching Lekmod " + game.mods[0].release + " verified." :
        game.state === "vanilla" ? "Civilization V found; Lekmod is not installed." :
          "No game is connected."), game.state === "installed" ? "success" :
          ["vanilla", "mismatch", "multiple"].includes(game.state) ? "warning" : "error");
      el("game-launcher-link").hidden = !["vanilla", "mismatch", "damaged", "multiple"].includes(game.state);
    }
  } catch (error) {
    el("game-apply").disabled = true;
    logUI("game-version-error", error.message);
  } finally { gameStatusRequest = false; }
}
window.addEventListener("focus", () => refreshGameConnection());
function sizeText(bytes) { return (bytes / 1048576).toFixed(1) + " MiB"; }
async function updateDownload() {
  if (!el("settings-dialog").open || editorUpdateLocked) return;
  try {
    const state = await api("/api/download-status");
    if (editorUpdateLocked) return;
    const active = ["running", "canceling"].includes(state.state);
    el("project-download").disabled = active;
    el("project-progress-group").hidden = !active;
    el("project-cancel").disabled = state.state === "canceling";
    if (active) {
      const progress = el("project-progress");
      if (state.total) progress.value = Math.min(1, state.bytes / state.total);
      else progress.removeAttribute("value");
      progress.max = 1;
      const fraction = state.total ? " / " + sizeText(state.total) +
        " (" + Math.floor(100 * state.bytes / state.total) + "%)" : "";
      el("project-progress-text").textContent = (state.state === "canceling" ? "Canceling… " : "") +
        (state.phase || "connecting") + ": " + sizeText(state.bytes || 0) + fraction +
        " · " + (state.destination || "");
      sectionMessage("source", "Source " + state.phase + ". Existing files remain untouched.", "warning");
    } else if (state.state === "complete") {
      el("project-path").value = state.path;
      sectionMessage("source", (state.reused ? "Reused validated source: " :
        "Downloaded source: ") + state.path + ". Click Save connections.");
    } else if (state.state === "error" || state.state === "canceled") {
      sectionMessage("source", state.error + " You can retry with Download.",
        state.state === "error" ? "error" : "warning");
    }
    if (!active && downloadTimer) { clearInterval(downloadTimer); downloadTimer = undefined; }
  } catch (error) { sectionMessage("source", error.message, "error"); }
}
async function refresh() {
  meta = await api("/api/meta");
  for (let attempt = 0; meta.initializing && attempt < 600; attempt++) {
    showConnectionLoading("The editor is open. Preparing the connected Lekmod project…");
    el("editor-version").textContent = "v" + meta.editor_version;
    await new Promise(resolve => setTimeout(resolve, 500));
    meta = await api("/api/meta");
  }
  if (meta.initializing) throw new Error("Project preparation is taking longer than expected. See Logs.");
  el("app-loading").hidden = true;
  prefs = meta.preferences;
  if (meta.handoff && !mergeReview) {
    mergeReview = meta.handoff;
    mergeChoices = {...(meta.handoff.choices || {})};
  }
  el("merge-button").disabled = !mergeReview?.items.length;
  el("restore-failed").hidden = !failedSaves.length;
  el("row-panel").open = prefs.panel_expanded !== false;
  locales = meta.locales;
  widths = {...prefs.column_widths};
  el("wrap").checked = prefs.wrap;
  el("table").classList.toggle("nowrap", !prefs.wrap);
  el("prefill").checked = prefs.prefill;
  el("page-size").value = pageSize();
  el("editor-version").textContent = "v" + (meta.editor_version || "unknown");
  const notice = meta.update_notice;
  const failure = el("update-failure");
  failure.hidden = !notice || notice.result !== "failure";
  if (!failure.hidden) failure.textContent = "The previous editor was restored. " +
    (notice.detail || "The update could not finish.") +
    " See Logs or localization/workspace/editor-updates/update.log.";
  if (notice && notice.result === "failure" &&
      sessionStorage.getItem("last-update-alert") !== notice.at) {
    sessionStorage.setItem("last-update-alert", notice.at);
    message("The update failed; the previous editor was restored. Open Logs or " +
      "localization/workspace/editor-updates/update.log for the cause, then retry.", true);
  }
  renderConnections();
  const select = el("locale");
  select.replaceChildren();
  for (const locale of Object.keys(locales)) select.append(new Option(locale, locale));
  select.value = locales[prefs.locale] ? prefs.locale : Object.keys(locales)[0] || "";
  categories();
  updateHistory(meta);
  if (meta.ready) changeMode();
  if (!prefs.onboarded && !el("settings-dialog").open) {
    el("settings-dialog").showModal(); fillSettings();
  }
}
el("settings-button").addEventListener("click", () => {
  guardNavigation(() => {
    logUI("settings-open");
    el("settings-dialog").showModal(); fillSettings();
  });
});
el("row-panel").addEventListener("toggle", async () => {
  if (!prefs || prefs.panel_expanded === el("row-panel").open) return;
  try { await preference({panel_expanded: el("row-panel").open}); }
  catch (error) { message(error.message, true); }
});
el("settings-close").addEventListener("click", () => el("settings-dialog").close());
el("editor-quit").addEventListener("click", () => guardNavigation(async () => {
  try {
    await api("/api/stop", {});
    el("settings-dialog").close();
    el("workspace").hidden = true;
    el("no-source").hidden = true;
    el("app-loading").hidden = false;
    el("app-loading").textContent = "Editor stopped. You can close this tab.";
  } catch (error) { sectionMessage("source", error.message, "error"); }
}));
el("settings-dialog").addEventListener("close", () => {
  clearInterval(downloadTimer); downloadTimer = undefined;
});
el("settings-dialog").addEventListener("cancel", event => {
  if (editorUpdateLocked) event.preventDefault();
});
el("project-browse").addEventListener("click", async () => {
  sectionMessage("source", "Opening folder chooser…", "busy");
  try { const result = await api("/api/browse", {kind: "project"});
    if (result.path) { el("project-path").value = result.path;
      sectionMessage("source", "Selected " + result.path + ". Save connections to use it."); }
    else sectionMessage("source", "No folder selected.", "warning"); }
  catch (error) { sectionMessage("source", error.message, "error"); }
});
el("project-download").addEventListener("click", async () => {
  try {
    sectionMessage("source", "Starting source download…", "busy");
    await api("/api/download-project", {version: el("project-version").value});
    await updateDownload();
    if (!downloadTimer) downloadTimer = setInterval(updateDownload, 1000);
  } catch (error) { sectionMessage("source", error.message, "error"); }
});
el("project-cancel").addEventListener("click", async () => {
  try { await api("/api/cancel-download", {}); await updateDownload(); }
  catch (error) { sectionMessage("source", error.message, "error"); }
});
async function verifyGame(path) {
  sectionMessage("game", "Checking game files and installed Lekmod…", "busy");
  const found = await api("/api/detect-game", {path});
  el("game-launcher-link").hidden = !["vanilla", "mismatch", "damaged", "multiple"].includes(found.state);
  if (found.state !== "installed") el("game-apply").disabled = true;
  if (found.path) el("game-path").value = found.path;
  if (found.state === "installed") sectionMessage("game", meta.ready ?
    "Civilization V and matching Lekmod " + found.mods[0].version +
      " verified. Save connections to use it." :
    "Civilization V and Lekmod found. Connect a project to compare their versions.",
    meta.ready ? "success" : "warning");
  else if (found.state === "vanilla") sectionMessage("game", "Civilization V found, but Lekmod is not installed. Install the matching release to test in game.", "warning");
  else if (["multiple", "mismatch"].includes(found.state))
    sectionMessage("game", found.error, "warning");
  else sectionMessage("game", found.error || "Civilization V was not found. Select its installation folder with Browse…", "error");
}
el("game-browse").addEventListener("click", async () => {
  sectionMessage("game", "Opening folder chooser…", "busy");
  try { const result = await api("/api/browse", {kind: "game"});
    if (result.path) { el("game-path").value = result.path; await verifyGame(result.path); }
    else sectionMessage("game", "No folder selected.", "warning"); }
  catch (error) { sectionMessage("game", error.message, "error"); }
});
el("detect-game").addEventListener("click", async () => {
  el("game-path").value = "";
  try {
    await verifyGame("");
  } catch (error) { sectionMessage("game", error.message, "error"); }
});
el("game-path").addEventListener("input", () => {
  el("game-apply").disabled = true;
  sectionMessage("game", "Path changed. Use Browse or save connections to verify it.", "warning");
});
el("settings-save").addEventListener("click", async () => {
  const button = el("settings-save"), instance = meta.server_instance;
  button.disabled = true; button.classList.add("busy-action");
  try {
    sectionMessage("source", "Validating and connecting the project…", "busy");
    const result = await api("/api/connect", {project_path: el("project-path").value,
      game_path: el("game-path").value, carry_translations: el("carry-translations").checked});
    if (result.restart) {
      showConnectionLoading("Connecting project and restarting the editor…");
      logUI("project-reconnect");
      for (let attempt = 0; attempt < 300; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        try {
          const live = await api("/api/meta");
          if (!live.initializing && live.server_instance !== instance) {
            if (!live.ready) throw new Error(live.connection_error || "Project could not open");
            location.reload();
            return;
          }
        } catch (error) {
          if (!["TypeError", "SyntaxError"].includes(error.name)) {
            showConnectionLoading("The new editor could not open this project: " +
              error.message + ". Check the source folder in Settings.", true);
            message(error.message, true);
            return;
          }
        }
      }
      showConnectionLoading("The editor did not reconnect. Retry or restart it from its EXE.", true);
      message("Project reconnection timed out.", true);
    } else {
      el("settings-dialog").close(); await refresh(); message("Connections saved.");
    }
  } catch (error) {
    const section = error.message.startsWith("Game:") ? "game" : "source";
    sectionMessage(section, error.message, "error");
  } finally {
    button.disabled = false; button.classList.remove("busy-action");
  }
});
el("loading-retry").addEventListener("click", () => location.reload());
el("snapshot-import").addEventListener("click", async () => {
  const file = el("snapshot-file").files[0];
  if (!file) { sectionMessage("snapshot", "Choose a .json.gz file first.", "error"); return; }
  try {
    sectionMessage("snapshot", "Verifying the local snapshot…", "busy");
    const response = await fetch("/api/snapshot", {method: "POST",
      headers: {"X-Editor-Token": token, "Content-Type": "application/octet-stream"}, body: file});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    sectionMessage("snapshot", "Snapshot matches the shared baseline. Reloading comparison rows…");
    await refresh();
    sectionMessage("snapshot", "Snapshot verified and imported.");
  } catch (error) { sectionMessage("snapshot", error.message, "error"); }
  finally { el("snapshot-file").value = ""; }
});
el("snapshot-reset").addEventListener("click", async () => {
  el("snapshot-file").value = "";
  el("snapshot-url").value = "";
  el("snapshot-password").value = "";
  el("snapshot-new-password").value = "";
  el("snapshot-confirm-password").value = "";
  try {
    await preference({snapshot_url: ""});
    sectionMessage("snapshot", "Inputs cleared. The installed reference, if any, is unchanged.");
  } catch (error) { sectionMessage("snapshot", error.message, "error"); }
});
el("snapshot-url-clear").addEventListener("click", async () => {
  el("snapshot-url").value = "";
  el("snapshot-password").value = "";
  try {
    await preference({snapshot_url: ""});
    sectionMessage("snapshot", "Saved link cleared. The local reference is unchanged.");
  } catch (error) { sectionMessage("snapshot", error.message, "error"); }
});
el("snapshot-encrypt").addEventListener("click", async () => {
  const password = el("snapshot-new-password").value;
  const confirmation = el("snapshot-confirm-password").value;
  if (password !== confirmation) {
    sectionMessage("snapshot", "The two passwords do not match.", "error");
    return;
  }
  const button = el("snapshot-encrypt");
  button.disabled = true;
  try {
    sectionMessage("snapshot", "Verifying and encrypting the local reference…", "busy");
    const result = await api("/api/snapshot-encrypt", {password});
    location.href = "/api/encrypted-snapshot";
    sectionMessage("snapshot", "Encrypted copy created: " + result.path +
      ". Your browser is downloading " + result.filename + ". Upload only this .enc file.");
    message("Encrypted snapshot ready to upload.");
  } catch (error) {
    sectionMessage("snapshot", error.message, "error");
  } finally {
    el("snapshot-new-password").value = "";
    el("snapshot-confirm-password").value = "";
    button.disabled = false;
  }
});
el("snapshot-cloud").addEventListener("click", async () => {
  const password = el("snapshot-password").value;
  const button = el("snapshot-cloud");
  button.disabled = true;
  try {
    sectionMessage("snapshot", "Downloading and verifying the encrypted snapshot…", "busy");
    const result = await api("/api/snapshot-cloud", {
      url: el("snapshot-url").value.trim(), password});
    el("snapshot-password").value = "";
    await refresh();
    sectionMessage("snapshot", "Snapshot verified against this project's reference.");
    message("Vanilla comparison is available for locales in the snapshot.");
  } catch (error) {
    el("snapshot-password").value = "";
    sectionMessage("snapshot", error.message, "error");
  } finally {
    button.disabled = false;
  }
});
el("history-sync").addEventListener("click", async () => {
  const button = el("history-sync"); button.disabled = true;
  sectionMessage("history", "Synchronizing the reviewed change index…", "busy");
  try { const result = await api("/api/history-sync", {version: el("history-version").value});
    meta.version_history = result.version_history; renderFilterChoices();
    sectionMessage("history", "Comparison available in the version filter; no mod files changed."); }
  catch (error) { sectionMessage("history", error.message, "error"); }
  finally { button.disabled = false; }
});
el("mode").addEventListener("click", async () => {
  guardNavigation(async () => {
    try {
      inMerge = false; el("merge-view").hidden = true;
      await preference({mode: developer() ? "translator" : "developer"});
      filters = {kind: "", status: "", date_field: "english_edited_at", date_from: "", date_to: "", version: "", needs_translation: ""};
      if (inLogs) closeLogs();
      changeMode(); renderConnections();
      logUI("mode-switch"); message("Switched to " + (developer() ? "Developer" : "Translator") + " mode.");
    } catch (error) {
      message(error.message + " Connect a full compatible project in Settings.", true);
    }
  });
});
el("locale").addEventListener("change", async () => {
  const next = el("locale").value, previous = prefs.locale;
  el("locale").value = previous;
  guardNavigation(async () => {
    el("locale").value = next;
    categories(); offset = 0;
    try {
      await preference({locale: next, category: el("category").value});
      if (!meta.vanilla_counts[next]) visible.delete("vanilla_target");
      renderColumnChoices(); await load();
    } catch (error) { message(error.message, true); }
  });
});
el("category").addEventListener("change", async () => {
  const next = el("category").value;
  el("category").value = prefs.category;
  guardNavigation(async () => {
    el("category").value = next; offset = 0;
    try { await preference({category: next}); await load(); }
    catch (error) { message(error.message, true); }
  });
});
el("search-input").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const next = el("search-input").value;
    el("search-input").value = committedSearch;
    guardNavigation(() => { el("search-input").value = next; offset = 0; return load(); });
  }, 230);
});
el("page-size").addEventListener("change", async () => {
  const next = el("page-size").value;
  el("page-size").value = pageSize();
  guardNavigation(async () => {
    offset = 0;
    try { await preference({page_size: next}); el("page-size").value = next;
      logUI("page-size-changed"); await load(); }
    catch (error) { message(error.message, true); }
  });
});
el("wrap").addEventListener("change", async () => {
  el("table").classList.toggle("nowrap", !el("wrap").checked);
  await preference({wrap: el("wrap").checked});
  logUI("wrap-changed");
});
el("prefill").addEventListener("change", async () => {
  await preference({prefill: el("prefill").checked});
  logUI("prefill-changed");
  if (!el("prefill").checked && el("translation").value) {
    el("prefill-dialog").showModal();
  } else if (el("prefill").checked && chosen && !el("translation").value) {
    el("translation").value = developer() ? chosen.text :
      chosen.translation || chosen.lekmod_target || "";
    countText(); markDraft();
  }
});
el("prefill-keep").addEventListener("click", () => {
  el("prefill-dialog").close(); message("Auto-fill off; your text was kept.");
});
el("prefill-clear").addEventListener("click", () => {
  el("translation").value = ""; countText(); markDraft(); el("prefill-dialog").close();
  message("Auto-fill off; edit box cleared.");
});
for (const name of ["gender", "plurality"]) el(name).addEventListener("change", () => {
  const custom = el(name + "-custom");
  custom.hidden = el(name).value !== "__custom__";
  custom.disabled = custom.hidden;
  if (!custom.hidden) custom.focus();
  markDraft();
});
el("translation").addEventListener("input", () => { countText(); markDraft(); });
for (const name of ["gender", "gender-custom", "plurality", "plurality-custom", "note", "identifier"])
  el(name).addEventListener("input", markDraft);
el("prev").addEventListener("click", () => guardNavigation(() => {
  offset = Math.max(0, offset - pageStep()); logUI("page-changed"); return load();
}));
el("next").addEventListener("click", () => guardNavigation(() => {
  offset += pageStep(); logUI("page-changed"); return load();
}));
async function openLogs() {
  inLogs = true;
  logUI("logs-open");
  el("workspace").hidden = true;
  el("merge-view").hidden = true;
  el("no-source").hidden = true;
  el("logs-view").hidden = false;
  try {
    const result = await api("/api/logs");
    el("logs-list").textContent = result.events.map(event =>
      localEditTime(event.at) + "  " + event.action + "  " + event.result +
      (event.detail ? " · " + event.detail : "")).join("\n") || "No actions recorded yet.";
  } catch (error) { message(error.message, true); }
}
function closeLogs() {
  inLogs = false;
  el("logs-view").hidden = true;
  renderConnections();
}
el("logs-button").addEventListener("click", () => inLogs ? closeLogs() : guardNavigation(openLogs));
el("logs-back").addEventListener("click", closeLogs);
el("logs-download").addEventListener("click", () => {
  logUI("logs-download");
  location.href = "/api/logs/download";
  message("Action log downloaded; you can attach it when reporting a problem.");
});
el("columns-button").addEventListener("click", () => {
  pendingColumns = new Set(visible); renderColumnChoices(); el("columns-dialog").showModal();
});
el("columns-close").addEventListener("click", async () => {
  if (!pendingColumns) return;
  try {
    await preference({[columnsPreference()]: Array.from(pendingColumns)});
    visible = pendingColumns; pendingColumns = null;
    renderTable(window.currentRows || []);
    logUI("columns-changed"); el("columns-dialog").close();
  } catch (error) { message(error.message, true); }
});
el("columns-x").addEventListener("click", () => el("columns-dialog").close());
el("columns-dialog").addEventListener("close", () => { pendingColumns = null; });
el("filters-button").addEventListener("click", () => {
  renderFilterChoices(); el("filters-dialog").showModal();
});
el("filters-x").addEventListener("click", () => el("filters-dialog").close());
el("filters-close").addEventListener("click", () => {
  const from = el("filter-from").value, to = el("filter-to").value;
  if (from && to && from > to) { message("The end date must follow the start date.", true); return; }
  const next = {kind: el("filter-kind").value, status: developer() ? "" : el("filter-status").value,
    date_field: developer() ? "english_edited_at" : el("filter-date-field").value,
    date_from: from, date_to: to, version: el("filter-version").value,
    needs_translation: el("filter-needs").checked ? "true" : ""};
  el("filters-dialog").close();
  guardNavigation(() => {
    filters = next; offset = 0; renderFilterChoices();
    logUI("filter-changed"); return load();
  });
});
el("filters-reset").addEventListener("click", () => {
  el("filters-dialog").close();
  guardNavigation(async () => {
    filters = {kind: "", status: "", date_field: "english_edited_at", date_from: "", date_to: "", version: "", needs_translation: ""};
    offset = 0; renderFilterChoices(); logUI("filter-changed");
    await load(); message("Filters cleared.");
  });
});
el("discard").addEventListener("click", () => { if (chosen) el("discard-dialog").showModal(); });
el("discard-cancel").addEventListener("click", () => el("discard-dialog").close());
el("discard-confirm").addEventListener("click", () => {
  if (chosen) {
    restoreDraft(); logUI("discard"); message("Unsaved edits discarded; saved files unchanged.");
  }
  el("discard-dialog").close();
});
function fillExchange() {
  el("exchange-title").textContent = developer() ? "Import/Export English source" : "Import/Export translations";
  el("exchange-help").textContent = developer()
    ? "Export the primary English source. A recipient reviews changed keys and affected translations before applying."
    : "Export only selected languages. All their saved rows are included; the recipient reviews keys before applying.";
  el("exchange-locales").hidden = developer();
  el("share").hidden = developer(); el("share-english").hidden = !developer();
  el("handoff-label").textContent = developer() ? "Import an English source ZIP to review" : "Import a translation ZIP to review";
  const choices = el("exchange-locales");
  choices.replaceChildren();
  const active = el("locale").value;
  for (const locale of Object.keys(locales)) {
    const label = document.createElement("label");
    const check = document.createElement("input");
    check.type = "checkbox"; check.value = locale; check.checked = locale === active;
    label.append(check, document.createTextNode(locale));
    choices.append(label);
  }
  el("handoff-file").value = "";
  sectionMessage("exchange", "");
}
el("exports-button").addEventListener("click", () => {
  fillExchange(); el("exports-dialog").showModal();
});
for (const id of ["exports-close", "exports-x"])
  el(id).addEventListener("click", () => el("exports-dialog").close());
function mergeDecisions() {
  return Object.fromEntries((mergeReview?.items || []).map(item =>
    [item.id, mergeChoices[item.id] || (item.status === "stale" || item.status === "blocked" ? "keep" : "incoming")]));
}
function renderMerge() {
  if (!mergeReview) return;
  const items = mergeReview.items;
  const body = el("merge-table").querySelector("tbody");
  body.replaceChildren();
  for (const item of items.slice(mergePage * 100, (mergePage + 1) * 100)) {
    const row = document.createElement("tr");
    const columns = [
      item.locale + " · " + item.status + "\n" + item.key,
      item.locale === "en_US" ? "Primary English" : item.english || "(English source unavailable)",
      (item.team || "(none)") + (item.team_gender ? "\nGender: " + item.team_gender : "") +
        (item.team_plurality ? "\nPlurality: " + item.team_plurality : "") +
        (item.team_note ? "\nNote: " + item.team_note : ""),
      item.incoming + (item.incoming_gender ? "\nGender: " + item.incoming_gender : "") +
        (item.incoming_plurality ? "\nPlurality: " + item.incoming_plurality : "") +
        (item.incoming_note ? "\nNote: " + item.incoming_note : ""),
    ];
    for (const value of columns) {
      const cell = document.createElement("td"); cell.textContent = value;
      row.append(cell);
    }
    const status = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "merge-status" + (["conflict"].includes(item.status) ? " warning" :
      ["stale", "blocked"].includes(item.status) ? " blocked" : "");
    badge.textContent = item.status_label || ({new: "Ready to add", ready: "Ready", conflict: "Review replacement",
      stale: "Source mismatch", blocked: "Needs IDE migration"}[item.status] || item.status);
    badge.title = item.reason || ""; status.append(badge); row.append(status);
    const resets = document.createElement("td");
    resets.textContent = (item.resets || []).map(locale => locale.split("_")[0]).join(", ") || "—";
    resets.title = "Older saved translations fall back to English after this source change. Their CSV text is retained.";
    row.append(resets);
    const cell = document.createElement("td");
    const check = document.createElement("input"); check.type = "checkbox";
    check.disabled = ["stale", "blocked"].includes(item.status) || mergeApplying || mergeChecking;
    check.checked = mergeDecisions()[item.id] === "incoming";
    check.setAttribute("aria-label", "Include " + item.locale + " " + item.key);
    check.addEventListener("change", async () => {
      mergeChoices[item.id] = check.checked ? "incoming" : "keep";
      if (item.locale === "en_US") {
        mergeChecking = true;
        updateMergeSummary();
        for (const input of el("merge-table").querySelectorAll("input")) input.disabled = true;
        el("merge-apply").disabled = true;
        sectionMessage("merge", "Checking translations against the selected English changes…", "busy");
        try {
          const englishChoices = Object.fromEntries(Object.entries(mergeDecisions()).filter(([key]) => key.startsWith("en_US:")));
          mergeReview = await api("/api/handoff-review", {choices: englishChoices});
          for (const entry of mergeReview.items)
            if (["stale", "blocked"].includes(entry.status)) mergeChoices[entry.id] = "keep";
          sectionMessage("merge", "English dependencies checked.");
        } catch (error) {
          mergeChoices[item.id] = check.checked ? "keep" : "incoming";
          message(error.message, true); sectionMessage("merge", error.message, "error");
        } finally { mergeChecking = false; renderMerge(); }
      } else {
        updateMergeSummary();
        try { await api("/api/handoff-choices", {choices: mergeDecisions()}); }
        catch (error) { message("Could not retain merge choices: " + error.message, true); }
      }
    });
    cell.append(check); row.append(cell);
    body.append(row);
  }
  el("merge-prev").disabled = mergePage === 0;
  el("merge-next").disabled = (mergePage + 1) * 100 >= items.length;
  el("merge-count").textContent = items.length ?
    (mergePage * 100 + 1) + "–" + Math.min((mergePage + 1) * 100, items.length) +
      " of " + items.length : "No changes";
  updateMergeSummary();
}
function updateMergeSummary() {
  if (!mergeReview) return;
  const decisions = mergeDecisions();
  const pending = Object.values(decisions).filter(value => value === "review").length;
  const selected = Object.values(decisions).filter(value => value === "incoming").length;
  el("merge-summary").textContent = "Languages: " + mergeReview.locales.join(", ") +
    " · " + mergeReview.items.length + " rows to review · " + selected +
    " selected · " + pending + " unresolved · " + mergeReview.identical_count +
    " identical rows ignored." +
    (mergeReview.source_commit && mergeReview.current_commit &&
     mergeReview.source_commit !== mergeReview.current_commit ?
      " Source revisions differ; each English row was checked." : "");
  el("merge-apply").disabled = !!pending || !!savePending || mergeApplying || mergeChecking;
  for (const id of ["merge-clear", "merge-back", "merge-prev", "merge-next", "mode", "settings-button", "logs-button", "merge-button"])
    if (mergeChecking) el(id).disabled = true;
    else if (!mergeApplying) el(id).disabled = id === "merge-prev" ? mergePage === 0 :
      id === "merge-next" ? (mergePage + 1) * 100 >= mergeReview.items.length : false;
  const gameAvailable = meta.game.state === "installed" && !el("game-apply").disabled;
  el("merge-destination").querySelector('option[value="project_game"]').disabled = !gameAvailable;
  if (!gameAvailable) el("merge-destination").value = "project";
}
function openMerge() {
  if (!mergeReview?.items.length) return;
  if (inLogs) closeLogs();
  inMerge = true; mergePage = 0;
  el("merge-destination").value = !el("game-apply").disabled ? "project_game" : "project";
  renderConnections(); renderMerge();
}
function closeMerge() {
  inMerge = false; renderConnections();
}
el("merge-button").addEventListener("click", () => inMerge ? closeMerge() : guardNavigation(openMerge));
el("merge-clear").addEventListener("click", async () => {
  try { await api("/api/handoff-clear", {}); mergeReview = null; mergeChoices = {};
    el("merge-button").disabled = true; closeMerge(); message("Pending review cleared; saved files unchanged."); }
  catch (error) { message(error.message, true); }
});
el("merge-back").addEventListener("click", closeMerge);
el("merge-prev").addEventListener("click", () => { mergePage--; renderMerge(); });
el("merge-next").addEventListener("click", () => { mergePage++; renderMerge(); });
el("handoff-preview").addEventListener("click", async () => {
  const file = el("handoff-file").files[0];
  if (!file || !file.name.toLowerCase().endsWith(".zip")) {
    sectionMessage("exchange", "Choose a localization ZIP first.", "error"); return;
  }
  const button = el("handoff-preview");
  button.disabled = true; button.classList.add("busy-action");
  sectionMessage("exchange", "Reading and comparing the selected ZIP…", "busy");
  try {
    const response = await fetch("/api/handoff-preview", {method: "POST",
      headers: {"X-Editor-Token": token, "Content-Type": "application/zip"}, body: file});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not preview this ZIP.");
    mergeReview = result;
    mergeChoices = Object.fromEntries(Object.entries(mergeChoices).filter(([id]) => result.items.some(item => item.id === id)));
    for (const item of result.items)
      if (["stale", "blocked"].includes(item.status)) mergeChoices[item.id] = "keep";
    el("merge-button").disabled = !result.items.length;
    sectionMessage("exchange", "Reviewed " + result.locales.join(", ") +
      ": " + result.items.length + " changed rows.");
    el("exports-dialog").close();
    if (result.items.length) guardNavigation(openMerge);
    else message("This ZIP has no changes to merge.");
  } catch (error) { sectionMessage("exchange", error.message, "error"); message(error.message, true); }
  finally { button.disabled = false; button.classList.remove("busy-action"); }
});
el("merge-apply").addEventListener("click", async () => {
  const button = el("merge-apply");
  mergeApplying = true;
  for (const id of ["mode", "settings-button", "logs-button", "merge-back",
                    "merge-prev", "merge-next", "merge-button", "merge-clear", "merge-destination"])
    el(id).disabled = true;
  for (const select of el("merge-table").querySelectorAll("input")) select.disabled = true;
  button.disabled = true; button.classList.add("busy-action");
  sectionMessage("merge", "Merging reviewed rows and rebuilding game XML…", "busy");
  try {
    const result = await api("/api/handoff-apply", {
      handoff_id: mergeReview.handoff_id, choices: mergeDecisions(), destination: el("merge-destination").value});
    mergeReview = null; mergeChoices = {};
    el("merge-button").disabled = true;
    inMerge = false;
    meta = await api("/api/meta"); updateHistory(meta); renderConnections();
    await load();
    if (result.game_error) message("Project saved; game copy failed: " + result.game_error, true);
    else message(result.applied ? (result.game_result ? "Merged into project and installed game. Restart Civilization V." : "Merged into the connected project and rebuilt its XML. Review the project diff.") :
      "Review complete. No rows needed changing.");
  } catch (error) { sectionMessage("merge", error.message, "error"); message(error.message, true);
    button.disabled = false;
  } finally {
    mergeApplying = false;
    for (const id of ["mode", "settings-button", "logs-button", "merge-back", "merge-clear", "merge-destination"])
      el(id).disabled = false;
    el("merge-button").disabled = !mergeReview?.items.length;
    if (mergeReview) renderMerge();
    button.classList.remove("busy-action");
  }
});
el("unsaved-keep").addEventListener("click", () => {
  pendingNavigation = null; el("unsaved-dialog").close();
});
el("unsaved-dialog").addEventListener("cancel", event => {
  if (guardSaving) event.preventDefault();
  else pendingNavigation = null;
});
el("unsaved-discard").addEventListener("click", () => continueNavigation(false));
el("unsaved-save").addEventListener("click", () => continueNavigation(true));
window.addEventListener("beforeunload", event => {
  if (hasUnsaved() || failedSaves.length || savePending) {
    event.preventDefault(); event.returnValue = "";
  }
});
function setSaveLock(active) {
  for (const id of ["mode", "settings-button", "exports-button", "merge-button",
                    "game-apply", "run-checks", "create-key", "locale", "category",
                    "undo", "redo"]) {
    el(id).disabled = active || (id === "merge-button" && !mergeReview?.items.length);
  }
  const status = el("save-state");
  status.hidden = !active;
  status.className = "section-status busy";
  status.textContent = active ? "Saving " + savePending.key +
    " and rebuilding the project. You may edit other rows while this finishes." : "";
  if (!active) {
    updateHistory(meta);
    renderConnections();
    markDraft();
  } else {
    el("save").disabled = true;
  }
}
function updateSavedRow(pending, result) {
  const rows = window.currentRows || [];
  if (el("locale").value === pending.locale) {
    for (const row of rows) {
      row.approved_sha256 = result.approved_sha256;
      if (row.key === pending.key && (el("category").value === pending.category || el("category").value === "all")) {
        row.translation = pending.translation;
        row.translation_gender = pending.translation_gender;
        row.translation_plurality = pending.translation_plurality;
        row.translator_note = pending.translator_note;
        row.translation_updated_at = result.translation_updated_at;
        row.translation_characters = String(Array.from(pending.translation).length);
        row.translation_status = pending.translation ?
          (result.applied_to_game ? "applied" : "saved") : "missing";
      }
    }
    if (chosen) chosen.approved_sha256 = result.approved_sha256;
    renderTable(rows);
    if (chosen) {
      const index = rows.findIndex(row => row === chosen);
      if (index >= 0) el("table").querySelectorAll("tbody tr")[index].classList.add("selected");
    }
  }
}
async function saveCurrent() {
  if (!chosen || !hasUnsaved() || savePending) return false;
  if (developer()) {
    savePending = {key: chosen.key, locale: "", developer: true};
    setSaveLock(true);
    el("table-loading").hidden = false;
    message("Saving English and rebuilding…");
    try {
      const newKey = el("identifier").value;
      const result = await api(newKey === chosen.key ? "/api/primary" : "/api/rename-primary",
        {index: chosen.index, key: chosen.key, old_text: chosen.text,
          text: el("translation").value, ...(newKey === chosen.key ? {} : {new_key: newKey})});
      meta.undo_available = result.undo_available;
      meta.redo_available = result.redo_available;
      updateHistory(result); await load();
      el("table-loading").hidden = false;
      try {
        const checked = await api("/api/check", {});
        message("English source saved and rebuilt. " + checked.summary);
      } catch (error) { message("English saved; checks failed: " + error.message, true); }
      return true;
    } catch (error) { message(error.message, true); markDraft(); return false; }
    finally {
      savePending = null; setSaveLock(false);
      el("table-loading").hidden = true;
    }
  }
  const pending = {locale: el("locale").value, category: chosen.category || el("category").value,
    key: chosen.key, source_fingerprint: chosen.source_fingerprint,
    english_source_sha256: chosen.english_source_sha256,
    approved_sha256: chosen.approved_sha256, translation: el("translation").value,
    translation_gender: grammarValue("gender"), translation_plurality: grammarValue("plurality"),
    translator_note: el("note").value};
  savePending = pending;
  saveCompletion = new Promise(resolve => { resolveSaveCompletion = resolve; });
  setSaveLock(true);
  let completed = false;
  try {
    const started = await api("/api/translate-async", pending);
    const jobId = started.id;
    clearSelection();
    renderTable(window.currentRows || []);
    let status = started;
    while (status.state === "running") {
      await new Promise(resolve => setTimeout(resolve, 500));
      status = await api("/api/save-status");
      if (status.id !== jobId) throw new Error("Another save replaced this row's result.");
    }
    if (status.state !== "complete") throw new Error(status.error || "Translation save failed.");
    savePending = null;
    updateSavedRow(pending, status);
    meta.undo_available = status.undo_available;
    meta.redo_available = status.redo_available;
    setSaveLock(false);
    failedSaves = failedSaves.filter(draft => draft.locale !== pending.locale ||
      draft.category !== pending.category || draft.key !== pending.key);
    rememberFailedSaves();
    message(status.applied_to_game === false ? "Saved. XML generation is Off in config.json." :
      "Saved to the project CSV and generated game XML.");
    completed = true;
    return true;
  } catch (error) {
    savePending = null;
    failedSaves = failedSaves.filter(draft => draft.locale !== pending.locale ||
      draft.category !== pending.category || draft.key !== pending.key);
    failedSaves.push(pending);
    rememberFailedSaves();
    setSaveLock(false);
    renderTable(window.currentRows || []);
    message("Save failed: " + error.message + " Your text is available through Restore translation.", true);
    return false;
  } finally {
    resolveSaveCompletion(completed);
    saveCompletion = null;
    resolveSaveCompletion = null;
  }
}
el("save").addEventListener("click", saveCurrent);
el("restore-failed").addEventListener("click", () => guardNavigation(async () => {
  if (!failedSaves.length) return;
  const draft = failedSaves[0];
  if (el("locale").value !== draft.locale || el("category").value !== draft.category) {
    el("locale").value = draft.locale; categories();
    el("category").value = draft.category;
    await preference({locale: draft.locale, category: draft.category});
  }
  el("search-input").value = draft.key; offset = 0; await load();
  const index = (window.currentRows || []).findIndex(row => row.key === draft.key);
  if (index < 0) { message("The saved draft's key is no longer in the source.", true); return; }
  selectRow(window.currentRows[index], el("table").querySelectorAll("tbody tr")[index]);
  el("translation").value = draft.translation;
  setGrammar("gender", draft.translation_gender);
  setGrammar("plurality", draft.translation_plurality);
  el("note").value = draft.translator_note;
  countText(); markDraft();
  failedSaves.shift(); rememberFailedSaves();
  message("Your text was restored. Review and save it again.");
}));
for (const name of ["undo", "redo"]) el(name).addEventListener("click", async () => {
  guardNavigation(async () => {
    try { const result = await api("/api/" + name, {}); updateHistory(result); await load();
      message(name === "undo" ? "Last saved change undone." : "Saved change restored.");
    } catch (error) { message(error.message, true); updateHistory(await api("/api/meta")); }
  });
});
el("create-key").addEventListener("click", () => guardNavigation(async () => {
  try {
    const info = await api("/api/primary-create-info");
    el("create-source").value = info.source_file;
    el("create-line").value = info.line;
    el("create-operation").value = info.operation;
    el("create-identifier").value = "";
    el("create-text").value = "";
    sectionMessage("create", "");
    el("create-key-dialog").showModal();
    logUI("key-dialog");
  } catch (error) { message(error.message, true); }
}));
for (const name of ["create-x", "create-cancel"])
  el(name).addEventListener("click", () => el("create-key-dialog").close());
el("create-confirm").addEventListener("click", async () => {
  const button = el("create-confirm");
  for (const name of ["create-confirm", "create-x", "create-cancel"]) el(name).disabled = true;
  button.classList.add("busy-action");
  try {
    sectionMessage("create", "Creating the key and rebuilding the project…", "busy");
    const result = await api("/api/create-primary", {
      key: el("create-identifier").value.trim(), text: el("create-text").value,
      operation: el("create-operation").value});
    el("create-key-dialog").close();
    el("search-input").value = result.key;
    updateHistory(result); offset = 0; await load();
    message("Text key created. Add a gameplay reference to use it in game.");
  } catch (error) { sectionMessage("create", error.message, "error"); message(error.message, true); }
  finally {
    for (const name of ["create-confirm", "create-x", "create-cancel"]) el(name).disabled = false;
    button.classList.remove("busy-action");
  }
});
el("create-key-dialog").addEventListener("cancel", event => {
  if (el("create-confirm").disabled) event.preventDefault();
});
el("game-apply").addEventListener("click", async () => {
  guardNavigation(async () => {
    el("game-apply").disabled = true; el("game-apply").classList.add("busy-action");
    try {
      const result = await api("/api/apply-game", {});
      message(result.changed ? "Installed game XML updated. Backup: " + result.backup +
        ". Restart Civilization V to test." : "Installed game XML already matches this project.");
    } catch (error) { message(error.message, true); }
    finally { el("game-apply").classList.remove("busy-action"); renderConnections(); }
  });
});
el("run-checks").addEventListener("click", async () => {
  guardNavigation(async () => {
    el("run-checks").disabled = true; el("run-checks").classList.add("busy-action");
    message("Running configured checks…");
    try { const result = await api("/api/check", {}); message(result.summary); }
    catch (error) { message(error.message, true); }
    finally { el("run-checks").disabled = false; el("run-checks").classList.remove("busy-action"); }
  });
});
el("share").addEventListener("click", () => guardNavigation(() => {
  const selected = Array.from(el("exchange-locales").querySelectorAll("input:checked"),
    input => input.value);
  if (!selected.length) {
    sectionMessage("exchange", "Select at least one language to export.", "error");
    return;
  }
  logUI("translation-export");
  location.href = "/api/export?locales=" + encodeURIComponent(selected.join(","));
  message("ZIP download started for " + selected.join(", ") +
    ". A recipient can import it using Import/Export → Preview merge.");
}));
el("share-english").addEventListener("click", () => guardNavigation(() => {
  logUI("english-export");
  location.href = "/api/export-english";
  message("English source ZIP download started. Send it to a developer for review.");
}));
function setEditorUpdateLock(locked) {
  if (locked === editorUpdateLocked) return;
  const dialog = el("settings-dialog");
  if (locked) {
    settingsControlsBeforeUpdate = new Map(Array.from(dialog.querySelectorAll(
      "button, input, select, textarea"), control => [control, control.disabled]));
    for (const control of settingsControlsBeforeUpdate.keys()) control.disabled = true;
    dialog.dataset.updating = "true";
    dialog.setAttribute("aria-busy", "true");
  } else {
    for (const [control, disabled] of settingsControlsBeforeUpdate) control.disabled = disabled;
    settingsControlsBeforeUpdate.clear();
    delete dialog.dataset.updating;
    dialog.removeAttribute("aria-busy");
    if (sourceVersions) showSourceVersions(sourceVersions);
  }
  editorUpdateLocked = locked;
}
function editorUpdateFailed(reason) {
  setEditorUpdateLock(false);
  el("update-status").classList.remove("busy-inline");
  el("update-status").textContent = "Editor update failed. Check the details below.";
  el("update-failure").textContent = reason +
    " See Logs or localization/workspace/editor-updates/update.log.";
  el("update-failure").hidden = false;
  message("Editor update failed: " + reason, true);
  editorUpdateStarted = 0;
}
async function checkLatest() {
  logUI("update-check");
  el("update-status").classList.add("busy-inline");
  el("update-status").textContent = "Checking published releases…";
  try {
    const info = await api("/api/editor-latest");
    latestEditorInfo = info;
    el("update-link").href = info.release_url;
    el("update-link").hidden = false;
    showEditorVersionStatus();
    el("update-badge").hidden = !info.available;
    if (info.available && !info.can_auto_update)
      el("update-status").textContent += " Source checkouts update with Git.";
  } catch (error) { el("update-status").textContent = "Update check unavailable: " + error.message; }
  finally { el("update-status").classList.remove("busy-inline"); }
}
function showEditorVersionStatus() {
  const info = latestEditorInfo || editorIntegrity;
  if (!info) return;
  const broken = editorIntegrity?.current === info.current && editorIntegrity.damaged_files.length > 0;
  const action = el("update-install");
  el("update-status").parentElement.classList.toggle("warning", broken);
  action.textContent = broken ? "Fix version" : "Download and update editor";
  action.disabled = editorUpdateLocked || !info.can_auto_update || (!broken && !info.available);
  if (broken) {
    el("update-status").textContent = "Editor v" + info.current + " has " +
      editorIntegrity.damaged_files.length + " changed or missing app file(s): " +
      editorIntegrity.damaged_files.join(", ") + ". Fix version installs v" + info.latest +
      "; projects and translations stay in place.";
  } else if (info.available) {
    el("update-status").textContent = "Editor v" + info.latest +
      " is available (current v" + info.current + ")." +
      (editorIntegrity?.verified ? " Installed v" + info.current + " files verified." : "");
  } else {
    el("update-status").textContent = "Editor v" + info.current +
      (editorIntegrity?.verified ? " files verified. Up to date." : " is up to date.");
  }
}
async function verifyEditorFiles() {
  logUI("update-verify");
  const button = el("update-verify");
  button.disabled = true;
  button.classList.add("busy-action");
  el("update-install").disabled = true;
  el("update-failure").hidden = true;
  el("update-status").classList.add("busy-inline");
  el("update-status").textContent = "Downloading the published editor files for comparison…";
  try {
    await api("/api/editor-verify", {});
    for (;;) {
      const state = await api("/api/editor-verify-status");
      if (state.state === "complete") {
        editorIntegrity = state;
        if (!latestEditorInfo || latestEditorInfo.current !== state.current ||
            latestEditorInfo.latest !== state.latest) {
          latestEditorInfo = {...state};
        }
        showEditorVersionStatus();
        message(state.verified ? "Editor files match the published release." :
          "Some editor files differ. Use Fix version to restore them.", !state.verified);
        break;
      }
      if (state.state === "error") throw new Error(state.error);
      const amount = state.total ? " of " + (state.total / 1048576).toFixed(1) : "";
      el("update-status").textContent = "Verifying editor files: " +
        ((state.bytes || 0) / 1048576).toFixed(1) + amount + " MB downloaded…";
      await new Promise(resolve => setTimeout(resolve, 450));
    }
  } catch (error) {
    editorIntegrity = null;
    el("update-status").parentElement.classList.add("warning");
    el("update-status").textContent = "File check failed: " + error.message;
    el("update-install").textContent = "Download and update editor";
    el("update-install").disabled = !latestEditorInfo?.available || !latestEditorInfo.can_auto_update;
    message("Editor file check failed: " + error.message, true);
  } finally {
    button.classList.remove("busy-action");
    button.disabled = editorUpdateLocked;
    el("update-status").classList.remove("busy-inline");
  }
}
el("update-check").addEventListener("click", checkLatest);
el("update-verify").addEventListener("click", verifyEditorFiles);
async function followEditorUpdate() {
  if (!editorUpdateStarted) return;
  try {
    const live = await api("/api/meta");
    if ((live.editor_version === editorUpdateVersion &&
         live.server_instance !== editorUpdateInstance) ||
        (live.update_notice?.result === "failure" &&
         Date.parse(live.update_notice.at) > editorUpdateStarted)) {
      location.reload();
      return;
    }
    const progress = await api("/api/editor-update-status");
    if (progress.state === "error") {
      editorUpdateFailed(progress.error);
      return;
    }
    if (progress.state === "downloading") {
      const size = progress.total ? " of " + (progress.total / 1048576).toFixed(1) : "";
      el("update-status").textContent = "Downloading editor v" + progress.version + ": " +
        ((progress.bytes || 0) / 1048576).toFixed(1) + size + " MB…";
    } else if (progress.state === "installing") {
      el("update-status").textContent = "Installing editor v" + progress.version +
        " and restarting this page…";
    } else {
      el("update-status").textContent = "Checking editor release…";
    }
  } catch (_) {
    el("update-status").textContent = "The editor is restarting. This page will reconnect automatically…";
  }
  if (Date.now() - editorUpdateStarted > 600000) {
    editorUpdateFailed("The editor did not reopen. Double-click its EXE to retry.");
    return;
  }
  editorUpdateTimer = setTimeout(followEditorUpdate, 700);
}
async function startEditorUpdate() {
  const repair = !!editorIntegrity?.damaged_files.length;
  editorUpdateInstance = meta.server_instance;
  setEditorUpdateLock(true);
  el("update-failure").hidden = true;
  el("update-status").classList.add("busy-inline");
  el("update-status").textContent = "Checking editor release…";
  try {
    editorUpdateVersion = (await api("/api/editor-latest")).latest;
    await api("/api/editor-update", {repair});
    editorUpdateStarted = Date.now();
    clearTimeout(editorUpdateTimer);
    followEditorUpdate();
  } catch (error) {
    editorUpdateFailed(error.message);
  }
}
el("update-install").addEventListener("click", () => {
  if (failedSaves.length) {
    message("Restore or resolve failed translation drafts before updating the editor.", true);
    return;
  }
  guardNavigation(startEditorUpdate);
});
refresh().then(() => Promise.all([checkLatest(), refreshSourceVersions()])).catch(error => {
  el("app-loading").textContent = "The editor could not load: " + error.message +
    ". Reload the page to try again.";
  message(error.message, true);
});
