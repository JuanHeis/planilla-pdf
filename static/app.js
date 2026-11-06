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
  setBusy(true);
  notify("Buscando campos en el PDF…");
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

    pdf = { ...result, existingFields: result.existingFields ?? [], filename: fromDemo ? "solicitud-ejemplo.pdf" : file.name };
    pageIndex = 0;
    changed = false;
    setExistingVisibility(true);
    ui.welcome.hidden = true;
    ui.editor.hidden = false;
    ui["editor-actions"].hidden = false;
    ui.filename.textContent = pdf.filename;

    ui["page-picker"].replaceChildren(...pdf.pages.map((_, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = "Página " + (index + 1) + " de " + pdf.pages.length;
      return option;
    }));
    renderPage();
    ui.filename.focus({ preventScroll: true });
    if (pdf.existingFields.length) {
      const existingMessage = "Este PDF ya trae campos rellenables. Se ven en violeta y se conservan al descargar.";
      const warnings = pdf.warnings ?? [];
      notify([existingMessage, ...warnings].join(" "), warnings.length ? "warning" : "info");
    } else if (pdf.warnings?.length) {
      notify(pdf.warnings.join(" "), "warning");
    } else if (!pdf.fields.length) {
      notify("No se detectaron campos. Podés agregar texto o casillas manualmente.", "warning");
    } else {
      notify("Revisá los campos resaltados antes de descargar el PDF.");
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
  changed = true;
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
  changed = true;
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
  changed = true;
  renderFields();
  const nextControl = selected ? pageElements.get(String(selected.id)).row : addButtons[0];
  nextControl.focus({ preventScroll: true });
}

async function exportPdf() {
  if (!pdf || busy) return;
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
  changed = true;
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

