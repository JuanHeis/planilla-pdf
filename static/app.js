"use strict";

const byId = (id) => document.getElementById(id);
const pageImage = new Image();
pageImage.draggable = false;
byId("sheet").prepend(pageImage);
const ui = Object.fromEntries([
  "file-input", "drop-zone", "pick-file", "demo", "welcome", "editor", "editor-actions",
  "change-file", "export", "notice", "filename", "document-summary", "page-picker",
  "prev", "next", "sheet", "overlay", "preview-error", "placement-hint",
  "page-count", "field-list", "field-details", "field-name", "delete-field",
  "toggle-existing", "existing-count",
].map((id) => [id, byId(id)]));

const addButtons = [...document.querySelectorAll("[data-add]")];
const metrics = [...document.querySelectorAll("[data-metric]")];
const typeName = { text: "Texto", checkbox: "Casilla" };
const pageElements = new Map();

let pdf;
let pageIndex = 0;
let selected;
let placing;
let drag;
let changed = false;
let busy = false;
let sequence = 0;
let showExisting = true;
let appConfig = { commercial: false };
let serviceReady = false;

function track(event) {
  if (!appConfig.commercial) return;
  fetch("/api/events", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event }), keepalive: true,
  }).catch(() => {});
}

function markChanged() {
  changed = true;
  byId("learning-consent").checked = false;
  byId("learning-save").disabled = true;
}

function rememberPreparedDocument(job, fieldsSnapshot) {
  if (!pdf || pdf.documentId !== job || JSON.stringify(pdf.fields) !== fieldsSnapshot) return;
  if (appConfig.commercial) {
    const address = new URL(window.location.href);
    address.searchParams.set("job", job);
    address.searchParams.delete("verify_error");
    window.history.replaceState(null, "", address.pathname + address.search + address.hash);
  }
  changed = false;
}

function displayPdf(result, filename) {
  pdf = { ...result, existingFields: result.existingFields ?? [], filename };
  pageIndex = 0;
  changed = false;
  setExistingVisibility(true);
  ui.welcome.hidden = true;
  ui.editor.hidden = false;
  ui["editor-actions"].hidden = false;
  ui.filename.textContent = filename;
  byId("download-original").hidden = !appConfig.commercial || !pdf.existingFields.length;
  byId("learning-consent").checked = false;
  byId("learning-save").disabled = true;
  byId("learning-status").textContent = "";
  const learned = pdf.learning?.changes || [];
  const applied = learned.filter((item) => item.applied).length;
  byId("learning-mode").textContent = pdf.learning?.mode === "conservative"
    ? (applied ? "Se aplicaron " + applied + " ajustes aprendidos. Revisalos junto con los demás campos." : "Los ajustes aprendidos se aplican cuando hay evidencia suficiente. Este resultado sigue necesitando tu revisión.")
    : "El aprendizaje está en observación: registra ejemplos revisados y evalúa patrones sin modificar la detección actual.";
  ui["page-picker"].replaceChildren(...pdf.pages.map((_, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = "Página " + (index + 1) + " de " + pdf.pages.length;
    return option;
  }));
  renderPage();
  ui.filename.focus({ preventScroll: true });
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(svg.namespaceURI, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function notify(message = "", kind = "info") {
  ui.notice.dataset.kind = kind;
  ui.notice.textContent = message;
  ui.notice.hidden = !message;
}

function setBusy(nextBusy) {
  busy = nextBusy;
  if (busy) drag = undefined;
  for (const control of [ui["pick-file"], ui.demo, ui["change-file"], ui.export]) {
    control.disabled = busy;
  }
  if (!busy && pdf) updateSummary();
}

async function checkedFetch(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const details = await response.json().catch(() => ({}));
    throw new Error(details.error || "La aplicación no pudo completar la operación.");
  }
  return response;
}

function mayReplace() {
  return !busy && (!changed || window.confirm("¿Querés abrir otro PDF? Se perderán los cambios de este archivo."));
}

async function openPdf(file, fromDemo = false) {
  await serviceConfigPromise;
  if (!serviceReady) return;
  setBusy(true);
  notify(appConfig.commercial ? "Buscando campos en el PDF… Todavía no se consumió tu descarga gratis." : "Buscando campos en el PDF…");
  try {
    if (fromDemo) {
      file = await (await checkedFetch("/demo.pdf")).blob();
    }
    const response = await checkedFetch("/api/detect", {
      method: "POST",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    });
    const result = await response.json();
    if (!result.pages?.length) throw new Error("El archivo no contiene páginas para mostrar.");

    displayPdf(result, fromDemo ? "solicitud-ejemplo.pdf" : file.name);
    track("editor_open");
    if (pdf.existingFields.length) {
      const existingMessage = appConfig.commercial
        ? "Este PDF ya trae campos rellenables. Los ves en violeta. Podés descargar el original gratis. Esta descarga no consume una conversión."
        : "Este PDF ya trae campos rellenables. Se ven en violeta y se conservan al descargar.";
      const warnings = pdf.warnings ?? [];
      notify([existingMessage, ...warnings].join(" "), warnings.length ? "warning" : "info");
    } else if (pdf.warnings?.length) {
      notify(pdf.warnings.join(" "), "warning");
    } else if (!pdf.fields.length) {
      notify("No se detectaron campos. Podés agregar texto o casillas manualmente.", "warning");
    } else {
      notify(appConfig.commercial ? "Revisá cada página y corregí lo que haga falta. Preparar y revisar todavía no consume tu descarga gratis." : "Revisá los campos resaltados antes de descargar el PDF.");
    }
  } catch (error) {
    notify(error.message === "Failed to fetch" ? "No hay conexión con la aplicación. Volvé a iniciarla e intentá de nuevo." : error.message, "error");
  } finally {
    setBusy(false);
  }
}

function fieldLabel(field) {
  return field.name || typeName[field.type];
}

function updateSummary() {
  const pages = pdf.pages.length;
  const fields = pdf.fields.length;
  const existing = pdf.existingFields.length;
  ui["document-summary"].textContent = pages + (pages === 1 ? " página · " : " páginas · ")
    + fields + (fields === 1 ? " campo nuevo" : " campos nuevos")
    + (existing ? " · " + existing + (existing === 1 ? " campo del original" : " campos del original") : "");
  ui.export.disabled = busy;
}

function setPlacing(type) {
  placing = type;
  for (const button of addButtons) {
    button.setAttribute("aria-pressed", String(button.dataset.add === placing));
  }
  ui.overlay.classList.toggle("is-placing", Boolean(placing));
  ui.overlay.tabIndex = placing ? 0 : -1;
  ui.overlay.setAttribute("aria-label", placing ? "Área para agregar campo. Presioná Enter para colocarlo en el centro." : "Campos de la página");
  ui["placement-hint"].hidden = !placing;
  ui["placement-hint"].textContent = placing
    ? "Elegí la ubicación del campo en la hoja. Esc para cancelar."
    : "";
}

function renderPage() {
  selected = undefined;
  setPlacing(undefined);
  const page = pdf.pages[pageIndex];
  ui["page-picker"].value = String(pageIndex);
  ui.prev.disabled = pageIndex === 0;
  ui.next.disabled = pageIndex === pdf.pages.length - 1;
  ui.sheet.style.aspectRatio = page.width + " / " + page.height;
  ui.sheet.hidden = false;
  ui.sheet.toggleAttribute("data-loading", true);
  ui["preview-error"].hidden = true;
  pageImage.alt = "Vista previa de la página " + (pageIndex + 1);
  pageImage.src = "/api/page/" + encodeURIComponent(pdf.documentId) + "/" + pageIndex;
  ui.sheet.parentElement.scrollTop = 0;
  renderFields();
}

function renderFields() {
  const fields = pdf.fields.filter((field) => field.page === pageIndex);
  const existing = pdf.existingFields.filter((field) => field.page === pageIndex);
  const overlay = document.createDocumentFragment();
  const list = document.createDocumentFragment();
  pageElements.clear();

  for (const field of existing) {
    const marker = document.createElement("span");
    marker.className = "original-field";
    marker.setAttribute("aria-hidden", "true");
    positionMarker(marker, field);
    overlay.append(marker);
  }

  for (const field of fields) {
    const marker = document.createElement("button");
    marker.type = "button";
    marker.className = "overlay-field";
    marker.dataset.field = String(field.id);
    if (field.type === "checkbox") marker.append(icon("check"));

    const handle = document.createElement("span");
    handle.className = "resize-handle";
    handle.setAttribute("aria-hidden", "true");
    marker.append(handle);
    overlay.append(marker);

    const row = document.createElement("button");
    row.type = "button";
    row.dataset.field = String(field.id);
    const label = document.createElement("span");
    row.append(icon(field.type === "checkbox" ? "check" : "text"), label);
    list.append(row);
    pageElements.set(String(field.id), { field, marker, row, label });
    updateField(field);
  }

  if (!fields.length) {
    const empty = document.createElement("p");
    empty.textContent = "Todavía no hay campos nuevos en esta página.";
    list.append(empty);
  }
  ui.overlay.replaceChildren(overlay);
  ui["field-list"].replaceChildren(list);
  ui["page-count"].textContent = fields.length;
  ui["toggle-existing"].hidden = !existing.length;
  ui["existing-count"].textContent = "Mostrar " + existing.length
    + (existing.length === 1 ? " campo del original" : " campos del original");
  updateSummary();
  selectField(selected);
}

function positionMarker(marker, field) {
  const page = pdf.pages[pageIndex];
  marker.style.left = field.x / page.width * 100 + "%";
  marker.style.top = field.y / page.height * 100 + "%";
  marker.style.width = field.width / page.width * 100 + "%";
  marker.style.height = field.height / page.height * 100 + "%";
}

function updateField(field) {
  const { marker, label } = pageElements.get(String(field.id));
  positionMarker(marker, field);
  marker.setAttribute("aria-label", typeName[field.type] + " · " + fieldLabel(field));
  marker.title = fieldLabel(field);
  label.textContent = fieldLabel(field);
}

function setExistingVisibility(visible) {
  showExisting = visible;
  ui.overlay.classList.toggle("hide-original-fields", !visible);
  ui["toggle-existing"].setAttribute("aria-pressed", String(visible));
}

function updateMetrics(field) {
  for (const input of metrics) {
    input.value = String(Math.round(field[input.dataset.metric] * 100) / 100);
  }
}

function selectField(field, reveal = false) {
  selected = field;
  for (const entry of pageElements.values()) {
    const active = entry.field === selected;
    entry.marker.setAttribute("aria-pressed", String(active));
    entry.row.setAttribute("aria-pressed", String(active));
  }
  ui["field-details"].hidden = !field;
  if (!field) return;
  ui["field-name"].value = field.name || "";
  updateMetrics(field);
  if (reveal) pageElements.get(String(field.id)).marker.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function clamp(value, min, max) {
  return Math.min(Math.max(min, value), max);
}

function moveField(field, x, y) {
  const page = pdf.pages[pageIndex];
  field.x = clamp(x, 0, Math.max(0, page.width - field.width));
  field.y = clamp(y, 0, Math.max(0, page.height - field.height));
  finishGeometry(field);
}

function resizeField(field, width, height) {
  const page = pdf.pages[pageIndex];
  field.width = clamp(width, 4, Math.max(4, page.width - field.x));
  field.height = clamp(height, 4, Math.max(4, page.height - field.y));
  finishGeometry(field);
}

function finishGeometry(field) {
  markChanged();
  updateField(field);
  updateMetrics(field);
}

function addField(x, y) {
  const page = pdf.pages[pageIndex];
  const isText = placing === "text";
  const width = Math.min(isText ? 120 : 14, page.width);
  const height = Math.min(isText ? 20 : 14, page.height);
  sequence += 1;
  selected = {
    id: "manual-" + sequence,
    page: pageIndex,
    x: clamp(x, 0, page.width - width),
    y: clamp(y, 0, page.height - height),
    width,
    height,
    type: placing,
    name: (isText ? "Texto " : "Casilla ") + sequence,
  };
  pdf.fields.push(selected);
  markChanged();
  setPlacing(undefined);
  renderFields();
  pageElements.get(String(selected.id)).marker.focus({ preventScroll: true });
}

function removeSelected() {
  if (!selected) return;
  const pageFields = [...pageElements.values()].map((entry) => entry.field);
  const index = pageFields.indexOf(selected);
  pdf.fields.splice(pdf.fields.indexOf(selected), 1);
  selected = pageFields[index + 1] || pageFields[index - 1];
  markChanged();
  renderFields();
  const nextControl = selected ? pageElements.get(String(selected.id)).row : addButtons[0];
  nextControl.focus({ preventScroll: true });
}

async function exportPdf() {
  await serviceConfigPromise;
  if (!serviceReady) return;
  if (!pdf || busy) return;
  if (appConfig.commercial) {
    setBusy(true);
    try {
      await window.PlanillaCheckout.open(pdf);
    } finally {
      setBusy(false);
    }
    return;
  }
  setBusy(true);
  ui.export.querySelector("span").textContent = "Preparando PDF…";
  try {
    const response = await checkedFetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documentId: pdf.documentId, fields: pdf.fields }),
    });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = pdf.filename.replace(/\.pdf$/i, "") + "-rellenable.pdf";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    changed = false;
    notify("PDF listo. Abrí la copia descargada para completar los campos.");
  } catch (error) {
    notify(error.message, "error");
  } finally {
    ui.export.querySelector("span").textContent = "Descargar PDF";
    setBusy(false);
  }
}

ui["pick-file"].addEventListener("click", () => ui["file-input"].click());
ui["change-file"].addEventListener("click", () => ui["file-input"].click());
ui["file-input"].addEventListener("change", (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file && mayReplace()) openPdf(file);
});
ui.demo.addEventListener("click", () => {
  if (mayReplace()) openPdf(null, true);
});
ui.export.addEventListener("click", exportPdf);
byId("learning-consent").addEventListener("change", () => {
  byId("learning-save").disabled = busy || !byId("learning-consent").checked;
});
byId("learning-save").addEventListener("click", async () => {
  if (!pdf || busy || !byId("learning-consent").checked) return;
  const snapshot = JSON.stringify(pdf.fields);
  const job = pdf.documentId;
  setBusy(true);
  byId("learning-save").disabled = true;
  byId("learning-status").textContent = "Comparando tus ajustes con la detección inicial…";
  try {
    const response = await checkedFetch("/api/field-learning/review", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documentId: job, fields: JSON.parse(snapshot), reviewed: true, contribute: true }),
    });
    const result = await response.json();
    if (pdf.documentId !== job) return;
    const changes = result.learning.changes;
    byId("learning-status").textContent = "Revisión guardada. Campos conservados: " + changes.approved
      + "; movidos: " + changes.moved + "; redimensionados: " + changes.resized + "; quitados: " + changes.deleted
      + "; agregados: " + changes.added + ". " + (result.autoApply ? "Los patrones consistentes pueden ayudar en otros formularios." : "Se evaluarán los patrones antes de activar ajustes automáticos.");
    if (result.saved) rememberPreparedDocument(job, snapshot);
    byId("learning-consent").checked = false;
  } catch (error) {
    byId("learning-status").textContent = error.message + " Tu gratuito no se consumió por este paso.";
  } finally {
    setBusy(false);
    byId("learning-save").disabled = !byId("learning-consent").checked;
  }
});
byId("learning-export").addEventListener("click", () => {
  if (!appConfig.commercial) window.location.assign("/api/field-learning/export");
});
byId("learning-import-label").addEventListener("click", () => {
  if (!appConfig.commercial && !busy) byId("learning-import").click();
});
byId("learning-import").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file || appConfig.commercial || busy) return;
  if (file.size > 2 * 1024 * 1024) {
    byId("learning-status").textContent = "El ZIP debe pesar hasta 2 MB.";
    return;
  }
  setBusy(true);
  try {
    await checkedFetch("/api/field-learning/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file });
    byId("learning-status").textContent = "Patrones importados. Se usarán al abrir el próximo formulario cuando tengan evidencia suficiente.";
  } catch (error) {
    byId("learning-status").textContent = error.message;
  } finally { setBusy(false); }
});
ui["toggle-existing"].addEventListener("click", () => setExistingVisibility(!showExisting));

for (const eventType of ["dragenter", "dragover"]) {
  ui["drop-zone"].addEventListener(eventType, (event) => {
    event.preventDefault();
    if (!busy) ui["drop-zone"].classList.add("is-over");
  });
}
ui["drop-zone"].addEventListener("dragleave", (event) => {
  if (!ui["drop-zone"].contains(event.relatedTarget)) ui["drop-zone"].classList.remove("is-over");
});
ui["drop-zone"].addEventListener("drop", (event) => {
  event.preventDefault();
  ui["drop-zone"].classList.remove("is-over");
  const file = event.dataTransfer.files[0];
  if (file && mayReplace()) openPdf(file);
});

ui["page-picker"].addEventListener("change", (event) => {
  pageIndex = Number(event.target.value);
  renderPage();
});
ui.prev.addEventListener("click", () => {
  pageIndex -= 1;
  renderPage();
});
ui.next.addEventListener("click", () => {
  pageIndex += 1;
  renderPage();
});
pageImage.addEventListener("error", () => {
  ui.sheet.hidden = true;
  ui["preview-error"].hidden = false;
});
pageImage.addEventListener("load", () => {
  ui.sheet.hidden = false;
  ui.sheet.toggleAttribute("data-loading", false);
  ui["preview-error"].hidden = true;
});

for (const button of addButtons) {
  button.addEventListener("click", () => {
    if (busy || ui.sheet.hidden || ui.sheet.hasAttribute("data-loading")) return;
    setPlacing(placing === button.dataset.add ? undefined : button.dataset.add);
    if (placing) {
      ui["placement-hint"].scrollIntoView({ block: "nearest" });
      ui.overlay.focus({ preventScroll: true });
    }
  });
}

ui["field-list"].addEventListener("click", (event) => {
  const button = event.target.closest("[data-field]");
  if (button) selectField(pageElements.get(button.dataset.field).field, true);
});
ui["field-name"].addEventListener("change", () => {
  if (!selected || busy) return;
  selected.name = ui["field-name"].value.trim() || typeName[selected.type];
  ui["field-name"].value = selected.name;
  updateField(selected);
  markChanged();
});
for (const input of metrics) {
  input.addEventListener("change", () => {
    if (!selected || busy) return;
    const value = input.valueAsNumber;
    if (!Number.isFinite(value)) {
      updateMetrics(selected);
      return;
    }
    const metric = input.dataset.metric;
    if (metric === "x") moveField(selected, value, selected.y);
    if (metric === "y") moveField(selected, selected.x, value);
    if (metric === "width") resizeField(selected, value, selected.height);
    if (metric === "height") resizeField(selected, selected.width, value);
  });
}
ui["delete-field"].addEventListener("click", () => {
  if (!busy) removeSelected();
});

ui.overlay.addEventListener("click", (event) => {
  if (busy) return;
  const rect = ui.overlay.getBoundingClientRect();
  const page = pdf.pages[pageIndex];
  if (placing) {
    const x = event.detail === 0 ? page.width / 2 : (event.clientX - rect.left) * page.width / rect.width;
    const y = event.detail === 0 ? page.height / 2 : (event.clientY - rect.top) * page.height / rect.height;
    addField(x, y);
    return;
  }
  const button = event.target.closest("[data-field]");
  selectField(button ? pageElements.get(button.dataset.field).field : undefined);
});

ui.overlay.addEventListener("pointerdown", (event) => {
  const button = event.target.closest("[data-field]");
  if (busy || !event.isPrimary || event.button !== 0 || !button || placing) return;
  const field = pageElements.get(button.dataset.field).field;
  selectField(field);
  button.focus({ preventScroll: true });
  button.setPointerCapture(event.pointerId);
  drag = {
    id: event.pointerId,
    field,
    resize: event.target.classList.contains("resize-handle"),
    startX: event.clientX,
    startY: event.clientY,
    x: field.x,
    y: field.y,
    width: field.width,
    height: field.height,
  };
});

ui.overlay.addEventListener("pointermove", (event) => {
  if (!drag || drag.id !== event.pointerId) return;
  const rect = ui.overlay.getBoundingClientRect();
  const page = pdf.pages[pageIndex];
  const dx = (event.clientX - drag.startX) * page.width / rect.width;
  const dy = (event.clientY - drag.startY) * page.height / rect.height;
  if (drag.resize) {
    resizeField(drag.field, drag.width + dx, drag.height + dy);
  } else {
    moveField(drag.field, drag.x + dx, drag.y + dy);
  }
});
for (const eventType of ["pointerup", "pointercancel", "lostpointercapture"]) {
  ui.overlay.addEventListener(eventType, (event) => {
    if (drag?.id === event.pointerId) drag = undefined;
  });
}

const arrows = {
  ArrowLeft: [-1, 0], ArrowUp: [0, -1], ArrowRight: [1, 0], ArrowDown: [0, 1],
};
ui.editor.addEventListener("keydown", (event) => {
  if (busy) return;
  if (event.key === "Escape" && placing) {
    const source = addButtons.find((button) => button.dataset.add === placing);
    setPlacing(undefined);
    source.focus();
    return;
  }
  if (placing && event.target === ui.overlay && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    addField(pdf.pages[pageIndex].width / 2, pdf.pages[pageIndex].height / 2);
    return;
  }
  const button = event.target.closest("[data-field]");
  const delta = arrows[event.key];
  if (!button || !delta || placing) return;
  event.preventDefault();
  const field = pageElements.get(button.dataset.field).field;
  if (selected !== field) selectField(field);
  const step = event.shiftKey ? 10 : 1;
  moveField(field, field.x + step * delta[0], field.y + step * delta[1]);
});

window.addEventListener("beforeunload", (event) => {
  if (changed) {
    event.preventDefault();
    event.returnValue = true;
  }
});



byId("download-original").addEventListener("click", async () => {
  if (!pdf || busy) return;
  setBusy(true);
  try {
    const response = await checkedFetch("/api/jobs/" + encodeURIComponent(pdf.documentId) + "/original");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = pdf.filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    notify("Descargaste el original que ya era rellenable. Esta descarga no consumió una conversión.");
  } catch (error) {
    notify(error.message, "error");
  } finally {
    setBusy(false);
  }
});
window.addEventListener("planilla:prepared", (event) => {
  rememberPreparedDocument(event.detail?.documentId, event.detail?.fields);
});
window.addEventListener("planilla:downloaded", (event) => {
  notify(event.detail?.recoveryAvailable
    ? "Tu PDF fue descargado. Podés recuperar este trabajo desde el enlace de tu correo."
    : "Tu PDF fue descargado. Guardá la copia en tu equipo para volver a usarla.");
});

async function initializeService() {
  serviceReady = false;
  setBusy(true);
  byId("local-ocr-link").hidden = true;
  byId("processing-place").textContent = "Comprobando el servicio antes de abrir tu PDF…";
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error("service_unavailable");
    const configuration = await response.json();
    if (typeof configuration.commercial !== "boolean") throw new Error("invalid_configuration");
    appConfig = configuration;
    window.PlanillaCheckout.configure(appConfig);
    serviceReady = true;
    setBusy(false);
    notify();
    if (!appConfig.commercial) {
      byId("local-ocr-link").hidden = false;
      byId("learning-export").hidden = false;
      byId("learning-import-label").hidden = false;
      byId("processing-place").textContent = "Tus archivos se procesan en esta computadora.";
      return;
    }
    byId("service-offer").hidden = false;
    const price = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 0 }).format(appConfig.price ?? 2990);
    byId("service-offer").textContent = "Revisar es gratis. Tu primera conversión de hasta " + (appConfig.freeMaxPages ?? 2)
      + " páginas también. Después, ARS " + price + " por PDF de hasta " + (appConfig.maxPages ?? 5) + " páginas.";
    const availability = byId("service-availability");
    if (appConfig.emailConfigured !== true) {
      availability.textContent = "Podés probar la detección y corregir los campos. La confirmación por correo todavía no está habilitada para nuevas descargas. Los originales ya rellenables y las descargas que ya tenés habilitadas siguen disponibles.";
      availability.hidden = false;
      byId("welcome-intro").textContent = "Subí tu formulario y revisá la detección de campos. Podés corregirlos manualmente.";
      byId("service-offer").hidden = true;
    } else if (appConfig.paymentsConfigured !== true) {
      availability.textContent = "Podés revisar y corregir tu PDF, y usar tu conversión gratuita si todavía la tenés disponible. Los pagos para otras conversiones todavía no están habilitados. Las descargas que ya tenés habilitadas siguen disponibles.";
      availability.hidden = false;
      byId("service-offer").textContent = "Revisar es gratis. La primera conversión de hasta " + (appConfig.freeMaxPages ?? 2) + " páginas también. Los pagos para otras conversiones todavía no están habilitados.";
    } else {
      availability.hidden = true;
    }
    byId("local-ocr-link").hidden = true;
    byId("open-recovery").hidden = false;
    byId("processing-place").textContent = "Tus archivos se procesan de forma privada y se borran al vencer el trabajo.";
    ui.export.querySelector("span").textContent = "Continuar a descarga";
    for (const link of document.querySelectorAll('.source-link')) link.href = appConfig.sourceUrl || "/source";
    const query = new URLSearchParams(window.location.search);
    const verifyError = query.get("verify_error");
    if (verifyError) {
      const explanation = verifyError === "expired"
        ? "El enlace de tu correo venció. Pedí uno nuevo para recuperar tu trabajo. No se consumió ninguna descarga por abrir este enlace."
        : "No pudimos abrir este enlace. Pedí uno nuevo con el correo que usaste. No se consumió ninguna descarga por abrir este enlace.";
      notify(explanation, "warning");
      window.PlanillaCheckout.openRecovery(explanation);
    }
    const job = query.get("job");
    if (!job) return;
    setBusy(true);
    notify("Recuperando tu trabajo…");
    try {
      const response = await checkedFetch("/api/jobs/" + encodeURIComponent(job) + "/metadata");
      const result = await response.json();
      displayPdf({ ...result, documentId: result.documentId || job }, result.filename || "formulario.pdf");
      notify("Recuperamos tu trabajo y sus campos. Estamos comprobando el estado de tu descarga.");
      await window.PlanillaCheckout.open(pdf, false);
      notify(pdf.existingFields.length
        ? "Este PDF ya era rellenable. Podés descargar el original gratis. Esta descarga no consume una conversión."
        : "Recuperamos tu trabajo y sus campos. Podés seguir corrigiendo o continuar con la descarga.");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setBusy(false);
    }
  } catch {
    serviceReady = false;
    setBusy(true);
    byId("processing-place").textContent = "Esperando conexión con el servicio.";
    notify("No pudimos comprobar el servicio. Tu PDF todavía no se envió y no se consumió ninguna descarga.", "error");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button secondary";
    retry.textContent = "Volver a intentar";
    retry.addEventListener("click", () => {
      retry.disabled = true;
      notify("Comprobando el servicio…");
      initializeService();
    });
    ui.notice.append(document.createTextNode(" "), retry);
  }
}
const serviceConfigPromise = initializeService();
