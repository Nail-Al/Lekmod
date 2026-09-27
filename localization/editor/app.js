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
  ["translator_note", "Translator note"]
];
const developerColumns = [["key", "Key"], ["kind", "Operation"],
  ["text", "English text"], ["characters", "Characters"],
  ["english_edited_at", "English edited"]];
const copyFields = new Set(["key", "vanilla_en_US", "vanilla_target",
  "lekmod_en_US", "lekmod_target", "text"]);
let meta, prefs, locales = {}, chosen = null, offset = 0, total = 0;
let visible = new Set(), widths = {}, searchTimer, requestId = 0;
let toastTimer;
let inLogs = false;
function logUI(name) { api("/api/event", {name}).catch(() => {}); }

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
  const toast = el("toast");
  toast.textContent = value;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 5500 : 3200);
}
function dialogMessage(value, error = false) {
  el("settings-status").textContent = value;
  el("settings-status").style.color = error ? "#a22d24" : "#246a39";
}
async function preference(values) {
  const result = await api("/api/preferences", values);
  prefs = result.preferences;
}
function developer() { return prefs.mode === "developer"; }
function activeColumns() { return developer() ? developerColumns : translatorColumns; }
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
  el("undo").disabled = !state.undo_available;
  el("redo").disabled = !state.redo_available;
}
function renderConnections() {
  const project = meta.project;
  el("source-badge").textContent = project
    ? "Source: " + (meta.included_source ? "included " : "project ") + meta.release
    : "Source: disconnected";
  el("source-badge").classList.toggle("missing", !project);
  const game = meta.game;
  const selected = game.mods.find(item => item.name === game.selected_mod);
  const matches = selected && project && selected.version.toLowerCase() ===
    project.version.toLowerCase() && selected.release.toLowerCase() === meta.release.toLowerCase();
  el("game-badge").textContent = selected ? "Game: " + selected.name +
    (matches ? " · matched" : " · version mismatch")
    : game.path ? "Game: no installed Lekmod selected" : "Game: disconnected";
  el("game-badge").classList.toggle("missing", !matches);
  el("game-apply").disabled = !matches || !meta.ready;
  el("workspace").hidden = !meta.ready || inLogs;
  el("no-source").hidden = meta.ready || inLogs;
  el("connection-error").textContent = meta.connection_error || "";
}
function updateBaselineNotice() {
  const notice = el("baseline-notice");
  if (developer()) { notice.hidden = true; return; }
  const locale = el("locale").value, counts = meta.vanilla_counts;
  notice.hidden = false;
  if (!Object.keys(counts).length) {
    notice.textContent = "Original vanilla sentences are unavailable. Import the matching snapshot in Settings to compare them.";
  } else if (!counts[locale]) {
    notice.textContent = "The pinned vanilla snapshot contains no " + locale +
      " entries. An empty vanilla cell does not mean the game has no translation.";
  } else {
    notice.hidden = true;
  }
}
function changeMode() {
  el("mode").textContent = developer() ? "Developer" : "Translator";
  el("locale-label").hidden = developer();
  el("category-label").hidden = developer();
  el("key-tools").hidden = !developer();
  el("grammar").hidden = developer();
  el("note-help").hidden = developer();
  el("run-checks").hidden = !developer();
  el("text-heading").textContent = developer() ? "English source text" : "My translation text";
  el("text-help").textContent = developer()
    ? "Saved in primary.xml; changes make previous translations stale."
    : "Saved in the language CSV; the project's game XML is rebuilt.";
  el("save").textContent = developer() ? "Save English and rebuild" : "Save and apply";
  el("rename-key").disabled = !chosen || !developer();
  updateBaselineNotice();
  offset = 0;
  visible = new Set(prefs.visible_columns.length ? prefs.visible_columns
    : [...translatorColumns, ...developerColumns].map(item => item[0]));
  if (!developer() && !prefs.visible_columns.length) {
    if (!Object.keys(meta.vanilla_counts).length) {
      visible.delete("vanilla_en_US"); visible.delete("vanilla_en_US_characters");
      visible.delete("vanilla_target");
    } else if (!meta.vanilla_counts[el("locale").value]) {
      visible.delete("vanilla_target");
    }
  }
  renderColumnChoices();
  load();
}
function categories() {
  const select = el("category");
  select.replaceChildren();
  for (const name of locales[el("locale").value] || []) {
    const option = document.createElement("option");
    option.value = name; option.textContent = name.replace(/_/g, " ");
    select.append(option);
  }
  select.value = (locales[el("locale").value] || []).includes(prefs.category)
    ? prefs.category : (locales[el("locale").value] || [])[0] || "";
  updateBaselineNotice();
}
function renderColumnChoices() {
  const container = el("column-choices");
  container.replaceChildren();
  for (const [field, title] of activeColumns()) {
    const label = document.createElement("label");
    label.className = "inline";
    const check = document.createElement("input");
    check.type = "checkbox"; check.checked = visible.has(field);
    check.addEventListener("change", async () => {
      check.checked ? visible.add(field) : visible.delete(field);
      await preference({visible_columns: Array.from(visible)});
      logUI("columns-changed");
      renderTable(window.currentRows || []);
    });
    label.append(check, document.createTextNode(title));
    container.append(label);
  }
}
function colWidth(field) { return widths[field] || (field === "key" ? 255 : 245); }
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
    th.textContent = title;
    const handle = document.createElement("span");
    handle.className = "resizer";
    handle.setAttribute("role", "separator");
    handle.title = "Drag to resize";
    handle.addEventListener("pointerdown", event => {
      event.preventDefault(); event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
      const start = event.clientX, original = colWidth(field);
      function move(pointer) {
        widths[field] = Math.max(100, Math.min(1500, original + pointer.clientX - start));
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
        td.textContent = value;
        if (value) td.title = value;
      }
      tr.append(td);
    }
    tr.addEventListener("click", () => selectRow(row, tr));
    body.append(tr);
  }
  tableWidth(cols);
}
function tableWidth(cols) {
  el("table").style.width = cols.reduce((sum, [field]) => sum + colWidth(field), 0) + "px";
}
function selectRow(row, tr) {
  logUI("row-selected");
  chosen = row;
  document.querySelectorAll("tbody tr").forEach(item => item.classList.remove("selected"));
  tr.classList.add("selected");
  const locale = el("locale").value;
  el("selected").textContent = row.key + (developer() ? " · English source" : " · " + locale);
  el("translation").disabled = false;
  el("save").disabled = false;
  el("rename-key").disabled = !developer();
  for (const field of ["gender", "plurality", "note"]) el(field).disabled = developer();
  if (el("prefill").checked) {
    el("translation").value = developer() ? row.text
      : row.translation || row.lekmod_target || "";
  } else {
    el("translation").value = "";
  }
  setGrammar("gender", row.translation_gender || "");
  setGrammar("plurality", row.translation_plurality || "");
  el("note").value = row.translator_note || "";
  countText();
  if (developer()) {
    el("context").textContent = "English edited: " + (row.english_edited_at || "unknown") +
      ". Existing translations become stale when this text changes. New keys need a gameplay reference.";
    el("token-help").hidden = true;
  } else {
    el("context").textContent = "Type: " + row.classification + " · status: " +
      row.translation_status + " · English edited: " + (row.english_edited_at || "unknown") +
      " · translation edited: " + (row.translation_updated_at || "unknown") +
      (row.translation_status === "stale"
        ? " · Game text falls back to English until reviewed." :
        row.translation_status === "applied" ? " · Applied to the project XML; game install is separate." : "");
    let tokens = {};
    try { tokens = JSON.parse(row.required_format_tokens); } catch (error) {}
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
  chosen = null;
  el("save").disabled = true;
  el("rename-key").disabled = true;
  el("translation").disabled = true;
  for (const field of ["gender", "plurality", "note", "gender-custom", "plurality-custom"])
    el(field).disabled = true;
  el("selected").textContent = "Select a row";
  const args = new URLSearchParams({q: el("search-input").value, offset});
  if (!developer()) {
    args.set("locale", el("locale").value);
    args.set("category", el("category").value);
  }
  try {
    const result = await api((developer() ? "/api/primary?" : "/api/rows?") + args);
    if (sequence !== requestId) return;
    total = result.total;
    renderTable(result.rows);
    el("count").textContent = total ? (offset + 1) + "–" + Math.min(offset + 60, total) +
      " of " + total : "No rows";
    el("prev").disabled = offset === 0;
    el("next").disabled = offset + 60 >= total;
  } catch (error) { message(error.message, true); }
}
function fillSettings() {
  el("project-path").value = prefs.project_path;
  el("game-path").value = prefs.game_path || meta.game.path;
  el("snapshot-url").value = prefs.snapshot_url || "";
  el("snapshot-password").value = "";
  const select = el("game-mod");
  select.replaceChildren(new Option("Select an installed version", ""));
  for (const mod of meta.game.mods) {
    select.append(new Option(mod.name + (mod.version ? " (" + mod.version + ")" : ""), mod.name));
  }
  select.value = prefs.game_mod || meta.game.selected_mod;
  dialogMessage(meta.game.error || "");
  api("/api/versions").then(result => {
    const select = el("project-version");
    select.replaceChildren();
    for (const item of result.versions) {
      const label = item.version + (item.date ? " · " + item.date : "") +
        (item.supported ? " · compatible" : " · needs migration");
      const option = new Option(label, item.version);
      option.disabled = !item.supported;
      select.append(option);
    }
  }).catch(error => dialogMessage(error.message, true));
}
async function refresh() {
  meta = await api("/api/meta");
  prefs = meta.preferences;
  locales = meta.locales;
  widths = {...prefs.column_widths};
  el("wrap").checked = prefs.wrap;
  el("table").classList.toggle("nowrap", !prefs.wrap);
  el("prefill").checked = prefs.prefill;
  el("editor-version").textContent = "v" + (meta.editor_version || "unknown");
  renderConnections();
  const select = el("locale");
  select.replaceChildren();
  for (const locale of Object.keys(locales)) select.append(new Option(locale, locale));
  select.value = locales[prefs.locale] ? prefs.locale : Object.keys(locales)[0] || "";
  categories();
  updateHistory(meta);
  if (meta.ready) changeMode();
  if (!prefs.onboarded && !el("settings-dialog").open) {
    fillSettings(); el("settings-dialog").showModal();
  }
}
el("settings-button").addEventListener("click", () => {
  logUI("settings-open");
  fillSettings(); el("settings-dialog").showModal();
});
el("settings-close").addEventListener("click", () => el("settings-dialog").close());
el("project-browse").addEventListener("click", async () => {
  try { const result = await api("/api/browse", {kind: "project"}); if (result.path) el("project-path").value = result.path; }
  catch (error) { dialogMessage(error.message, true); }
});
el("project-download").addEventListener("click", async () => {
  const button = el("project-download");
  button.disabled = true;
  try {
    await api("/api/download-project", {version: el("project-version").value});
    dialogMessage("Downloading the compatible source into a new folder…");
    for (let attempt = 0; attempt < 180; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const state = await api("/api/download-status");
      if (state.state === "error") throw new Error(state.error);
      if (state.state === "complete") {
        el("project-path").value = state.path;
        dialogMessage("Source ready. Click Save connections to use it.");
        return;
      }
    }
    throw new Error("Download is still running. Open Settings again to check it.");
  } catch (error) { dialogMessage(error.message, true); }
  finally { button.disabled = false; }
});
el("game-browse").addEventListener("click", async () => {
  try { const result = await api("/api/browse", {kind: "game"}); if (result.path) el("game-path").value = result.path; }
  catch (error) { dialogMessage(error.message, true); }
});
el("detect-game").addEventListener("click", async () => {
  try { const found = await api("/api/detect-game", {}); el("game-path").value = found.path; dialogMessage(found.path ? "Game found." : "Game not found automatically."); }
  catch (error) { dialogMessage(error.message, true); }
});
el("inspect-game").addEventListener("click", async () => {
  try {
    const result = await api("/api/inspect-game", {path: el("game-path").value});
    const select = el("game-mod");
    select.replaceChildren(new Option("Select an installed version", ""));
    for (const mod of result.mods) select.append(new Option(mod.name + " (" + (mod.version || "unknown") + ")", mod.name));
    if (result.mods.length === 1) select.value = result.mods[0].name;
    dialogMessage(result.mods.length ? "Found " + result.mods.length + " installed Lekmod version(s)." :
      "This is a game installation, but no Lekmod DLC is installed.");
  } catch (error) { dialogMessage(error.message, true); }
});
el("settings-save").addEventListener("click", async () => {
  try {
    const result = await api("/api/connect", {project_path: el("project-path").value,
      game_path: el("game-path").value, game_mod: el("game-mod").value});
    el("settings-dialog").close();
    if (result.restart) {
      message("Switching project… the editor will reconnect.");
      for (let attempt = 0; attempt < 60; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        try { await refresh(); message("Connected to the selected project."); break; } catch (error) {}
      }
    } else { await refresh(); message("Connections saved."); }
  } catch (error) { dialogMessage(error.message, true); }
});
el("snapshot-import").addEventListener("click", async () => {
  const file = el("snapshot-file").files[0];
  if (!file) { dialogMessage("Choose a .json.gz file first.", true); return; }
  try {
    const response = await fetch("/api/snapshot", {method: "POST",
      headers: {"X-Editor-Token": token, "Content-Type": "application/octet-stream"}, body: file});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    dialogMessage("Snapshot matches the shared baseline. Reloading comparison rows…");
    await refresh();
  } catch (error) { dialogMessage(error.message, true); }
});
el("snapshot-cloud").addEventListener("click", async () => {
  const password = el("snapshot-password").value;
  try {
    dialogMessage("Downloading and verifying the encrypted snapshot…");
    const result = await api("/api/snapshot-cloud", {
      url: el("snapshot-url").value.trim(), password});
    el("snapshot-password").value = "";
    await refresh();
    dialogMessage("Snapshot verified against this project's reference.");
    message("Vanilla comparison is available for locales in the snapshot.");
  } catch (error) {
    el("snapshot-password").value = "";
    dialogMessage(error.message, true);
  }
});
el("mode").addEventListener("click", async () => {
  try {
    await preference({mode: developer() ? "translator" : "developer"});
    changeMode();
    logUI("mode-switch"); message("Switched to " + (developer() ? "Developer" : "Translator") + " mode.");
  } catch (error) {
    message(error.message + " Connect a full compatible project in Settings.", true);
  }
});
el("locale").addEventListener("change", async () => {
  categories(); offset = 0;
  await preference({locale: el("locale").value, category: el("category").value});
  if (!meta.vanilla_counts[el("locale").value]) visible.delete("vanilla_target");
  renderColumnChoices(); load();
});
el("category").addEventListener("change", async () => {
  offset = 0; await preference({category: el("category").value}); load();
});
el("search-input").addEventListener("input", () => {
  clearTimeout(searchTimer); searchTimer = setTimeout(() => { offset = 0; load(); }, 230);
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
    countText();
  }
});
el("prefill-keep").addEventListener("click", () => {
  el("prefill-dialog").close(); message("Auto-fill off; your text was kept.");
});
el("prefill-clear").addEventListener("click", () => {
  el("translation").value = ""; countText(); el("prefill-dialog").close();
  message("Auto-fill off; edit box cleared.");
});
for (const name of ["gender", "plurality"]) el(name).addEventListener("change", () => {
  const custom = el(name + "-custom");
  custom.hidden = el(name).value !== "__custom__";
  custom.disabled = custom.hidden;
  if (!custom.hidden) custom.focus();
});
el("translation").addEventListener("input", countText);
el("prev").addEventListener("click", () => { offset -= 60; logUI("page-changed"); load(); });
el("next").addEventListener("click", () => { offset += 60; logUI("page-changed"); load(); });
async function openLogs() {
  inLogs = true;
  logUI("logs-open");
  el("workspace").hidden = true;
  el("no-source").hidden = true;
  el("logs-view").hidden = false;
  try {
    const result = await api("/api/logs");
    el("logs-list").textContent = result.events.map(event =>
      event.at + "  " + event.action + "  " + event.result).join("\n") || "No actions recorded yet.";
  } catch (error) { message(error.message, true); }
}
el("logs-button").addEventListener("click", openLogs);
el("logs-back").addEventListener("click", () => {
  inLogs = false;
  el("logs-view").hidden = true;
  renderConnections();
});
el("logs-download").addEventListener("click", () => {
  logUI("logs-download");
  location.href = "/api/logs/download";
  message("Action log downloaded; you can attach it when reporting a problem.");
});
el("columns-button").addEventListener("click", () => el("columns-dialog").showModal());
el("columns-close").addEventListener("click", () => el("columns-dialog").close());
el("exports-button").addEventListener("click", () => el("exports-dialog").showModal());
el("exports-close").addEventListener("click", () => el("exports-dialog").close());
el("save").addEventListener("click", async () => {
  if (!chosen) return;
  el("save").disabled = true; message("Saving and validating…");
  try {
    const result = developer()
      ? await api("/api/primary", {index: chosen.index, key: chosen.key,
          old_text: chosen.text, text: el("translation").value})
      : await api("/api/translate", {locale: el("locale").value,
          category: el("category").value, key: chosen.key,
          source_fingerprint: chosen.source_fingerprint,
          translation: el("translation").value, translation_gender: grammarValue("gender"),
          translation_plurality: grammarValue("plurality"), translator_note: el("note").value});
    updateHistory(result); await load();
    if (developer()) {
      try {
        const checked = await api("/api/check", {});
        message("English saved and rebuilt. " + checked.summary);
      } catch (error) { message("English saved and rebuilt; checks failed: " + error.message, true); }
    } else {
      message(result.applied_to_game === false ? "Saved. XML generation is Off in config.json." :
        "Saved to project CSV and generated game XML. Use Apply to installed game for a local test.");
    }
  } catch (error) { message(error.message, true); el("save").disabled = false; }
});
for (const name of ["undo", "redo"]) el(name).addEventListener("click", async () => {
  try { const result = await api("/api/" + name, {}); updateHistory(result); await load();
    message(name === "undo" ? "Last saved change undone." : "Saved change restored.");
  } catch (error) { message(error.message, true); updateHistory(await api("/api/meta")); }
});
el("create-key").addEventListener("click", async () => {
  try {
    const result = await api("/api/create-primary", {key: el("new-key").value.trim(),
      text: el("translation").value});
    el("new-key").value = ""; el("search-input").value = result.key;
    updateHistory(result); offset = 0; await load();
    message("Text key created. Add a gameplay reference to use it in game.");
  } catch (error) { message(error.message, true); }
});
el("rename-key").addEventListener("click", async () => {
  if (!chosen) return;
  try {
    const result = await api("/api/rename-primary", {index: chosen.index,
      key: chosen.key, new_key: el("new-key").value.trim()});
    el("search-input").value = result.key; el("new-key").value = "";
    updateHistory(result); offset = 0; await load();
    message("Unreferenced text key renamed.");
  } catch (error) { message(error.message, true); }
});
el("game-apply").addEventListener("click", async () => {
  try {
    const result = await api("/api/apply-game", {});
    message(result.changed ? "Installed game XML updated. Backup: " + result.backup +
      ". Restart Civilization V to test." : "Installed game XML already matches this project.");
  } catch (error) { message(error.message, true); }
});
el("run-checks").addEventListener("click", async () => {
  message("Running configured checks…");
  try { const result = await api("/api/check", {});
    message(result.summary);
  } catch (error) { message(error.message, true); }
});
el("share").addEventListener("click", () => {
  logUI("translation-export");
  location.href = "/api/export?locale=" + encodeURIComponent(el("locale").value);
  message("Translation ZIP download started. Send it to a developer for review.");
});
el("gamexml").addEventListener("click", () => {
  logUI("xml-export");
  location.href = "/api/game-xml";
  message("Generated XML download started. This is one file, not a complete mod.");
});
async function checkLatest() {
  logUI("update-check");
  el("update-status").textContent = "Checking published releases…";
  try {
    const info = await api("/api/editor-latest");
    el("update-link").href = info.release_url;
    el("update-link").hidden = false;
    el("update-status").textContent = info.available
      ? "Editor v" + info.latest + " is available (current v" + info.current + ")."
      : "Editor v" + info.current + " is up to date.";
    el("update-install").disabled = !info.available || !info.can_auto_update;
    el("update-badge").hidden = !info.available;
    if (info.available && !info.can_auto_update)
      el("update-status").textContent += " Source checkouts update with Git.";
  } catch (error) { el("update-status").textContent = "Update check unavailable: " + error.message; }
}
el("update-check").addEventListener("click", checkLatest);
el("update-install").addEventListener("click", async () => {
  el("update-install").disabled = true;
  dialogMessage("Downloading and checking the editor update…");
  try {
    const result = await api("/api/editor-update", {});
    el("settings-dialog").close();
    message("Editor v" + result.version + " staged. This window will close; the new editor will open.");
  } catch (error) {
    el("update-install").disabled = false;
    dialogMessage(error.message, true);
  }
});
refresh().then(checkLatest).catch(error => message(error.message, true));
