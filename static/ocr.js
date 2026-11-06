"use strict";

const el = (id) => document.getElementById(id);
const ui = {
  open: el("open-pdf"),
  file: el("pdf-file"),
  empty: el("ocr-empty"),
  notice: el("ocr-notice"),
  session: el("ocr-session"),
  document: el("ocr-document"),
  progress: el("review-progress"),
  prev: el("ocr-prev"),
  next: el("ocr-next"),
  page: el("ocr-page"),
  paper: el("ocr-paper"),
  image: new Image(),
  imageError: el("image-error"),
  overlay: el("ocr-overlay"),
  pageStatus: el("ocr-page-status"),
  filterLabel: el("filter-label"),
  filter: el("filter-suggestions"),
  reviewBody: el("ocr-review-body"),
  wordsMessage: el("ocr-words-message"),
  retry: el("retry-page"),
  table: el("ocr-table"),
  rows: el("ocr-rows"),
  draftStatus: el("ocr-draft-status"),
  save: el("save-page"),
  learningSummary: el("learning-summary"),
  import: el("import-learning"),
  export: el("export-learning"),
  learningFile: el("learning-file"),
};

ui.paper.prepend(ui.image);

const state = {
  session: null,
  page: 0,
  pages: new Map(),
  reviewedPages: new Set(),
  entries: new Map(),
  selection: null,
  busy: false,
};

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const details = await response.json().catch(() => ({}));
    throw new Error(details.error || "Planilla no pudo completar esta acción.");
  }
  return response;
}

function notify(message, error = false) {
  ui.notice.textContent = message;
  ui.notice.dataset.kind = error ? "error" : "info";
  ui.notice.hidden = !message;
}

function setBusy(busy) {
  state.busy = busy;
  ui.open.disabled = busy;
  ui.import.disabled = busy;
  ui.export.disabled = busy;
  ui.page.disabled = busy;
  ui.prev.disabled = busy || state.page === 0;
  ui.next.disabled = busy || state.page >= (state.session?.pages.length ?? 0) - 1;
  ui.retry.disabled = busy;
  updateReview();
}

function textFor(page, word) {
  return page.draft.get(word.id) ?? word.text;
}

function correctionsFor(page) {
  return page.words.flatMap((word) => {
    const text = textFor(page, word);
    return text === word.original ? [] : [{ id: word.id, text }];
  });
}

function updateReview() {
  const page = state.pages.get(state.page);
  ui.save.disabled = state.busy || !page || (page.reviewed && !page.draft.size);
  ui.reviewBody.inert = state.busy;
  if (state.session) {
    ui.progress.textContent = state.reviewedPages.size + " de " + state.session.pages.length + " páginas confirmadas";
  }
  if (!page) {
    ui.draftStatus.textContent = "";
    return;
  }
  const count = correctionsFor(page).length;
  ui.save.textContent = page.draft.size ? "Guardar correcciones" :
    page.reviewed ? "Página confirmada" :
    count ? "Confirmar correcciones" : "Confirmar página sin cambios";
  ui.draftStatus.textContent = page.draft.size ? "Cambios sin guardar" : "";
  ui.pageStatus.textContent = page.reviewed ? "Página confirmada" : "Pendiente de confirmar";
}

function showLearning(learning) {
  if (!learning) return;
  const correction = learning.corrections === 1 ? " corrección guardada" : " correcciones guardadas";
  const review = learning.reviews === 1 ? " página confirmada" : " páginas confirmadas";
  ui.learningSummary.textContent = learning.corrections + correction + " · " + learning.reviews + review;
}

function showWordsMessage(message, retry = false) {
  ui.wordsMessage.textContent = message;
  ui.wordsMessage.hidden = !message;
  ui.retry.hidden = !retry;
}

function showPreview() {
  const page = state.session.pages[state.page];
  ui.imageError.hidden = true;
  ui.paper.hidden = false;
  ui.image.hidden = true;
  ui.paper.style.aspectRatio = page.width + " / " + page.height;
  ui.image.alt = "Página " + (state.page + 1) + " del PDF";
  ui.image.src = "/api/page/" + encodeURIComponent(state.session.documentId) + "/" + state.page;
}

function createPage(payload) {
  if (payload.reviewed) state.reviewedPages.add(state.page);
  else state.reviewedPages.delete(state.page);
  return { words: payload.words, reviewed: payload.reviewed, draft: new Map() };
}

function invalidateCleanPages(except = -1) {
  for (const [index, page] of state.pages) {
    if (index !== except && !page.draft.size) state.pages.delete(index);
  }
}

async function readPage(index) {
  state.page = index;
  state.selection = null;
  ui.page.value = String(index);
  ui.filter.checked = false;
  ui.reviewBody.scrollTop = 0;
  ui.paper.parentElement.scrollTop = 0;
  showPreview();
  if (state.pages.has(index)) {
    renderWords();
    setBusy(false);
    return;
  }
  setBusy(true);
  ui.table.hidden = true;
  ui.filterLabel.hidden = true;
  ui.overlay.replaceChildren();
  ui.pageStatus.textContent = "Leyendo la página…";
  showWordsMessage("El reconocimiento puede tardar unos segundos.");
  try {
    const path = "/api/ocr/" + encodeURIComponent(state.session.documentId) + "/" + index;
    const payload = await (await request(path)).json();
    state.pages.set(index, createPage(payload));
    renderWords();
  } catch (error) {
    ui.pageStatus.textContent = "Lectura pendiente";
    showWordsMessage(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function openPdf(file) {
  if (Array.from(state.pages.values()).some((page) => page.draft.size)
    && !window.confirm("Hay correcciones sin guardar. ¿Querés abrir otro PDF igual?")) return;
  setBusy(true);
  notify("Abriendo " + file.name + "…");
  try {
    const payload = await (await request("/api/ocr", {
      method: "POST",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    })).json();
    state.session = payload;
    state.pages.clear();
    state.reviewedPages.clear();
    ui.document.textContent = file.name;
    ui.page.replaceChildren(...payload.pages.map((_, index) => new Option(
      "Página " + (index + 1) + " de " + payload.pages.length, String(index)
    )));
    ui.empty.hidden = true;
    ui.session.hidden = false;
    notify((payload.warnings ?? []).join(" "));
    await readPage(0);
    ui.document.focus({ preventScroll: true });
  } catch (error) {
    notify(error.message, true);
  } finally {
    setBusy(false);
  }
}

function selectWord(id, focusInput = false) {
  if (state.selection !== null) {
    const prior = state.entries.get(state.selection);
    prior?.row.classList.remove("is-selected");
    prior?.marker.removeAttribute("aria-current");
  }
  state.selection = id;
  const entry = state.entries.get(id);
  if (!entry) return;
  entry.row.classList.add("is-selected");
  entry.marker.setAttribute("aria-current", "true");
  if (focusInput) {
    if (entry.row.hidden) {
      ui.filter.checked = false;
      filterRows();
    }
    entry.row.scrollIntoView({ block: "nearest" });
    entry.input.focus({ preventScroll: true });
  }
}

function setText(word, value) {
  const page = state.pages.get(state.page);
  if (value === word.text) page.draft.delete(word.id);
  else page.draft.set(word.id, value);
  const entry = state.entries.get(word.id);
  entry.marker.classList.toggle("is-edited", value !== word.original);
  updateReview();
}

function filterRows() {
  const page = state.pages.get(state.page);
  if (!page) return;
  for (const word of page.words) {
    const matches = (word.suggested && word.suggested !== word.original) || textFor(page, word) !== word.original;
    state.entries.get(word.id).row.hidden = ui.filter.checked && !matches;
  }
}

function renderWords() {
  const page = state.pages.get(state.page);
  const size = state.session.pages[state.page];
  const rows = document.createDocumentFragment();
  const markers = document.createDocumentFragment();
  state.entries.clear();
  let hasSuggestion = false;
  for (const word of page.words) {
    const value = textFor(page, word);
    const suggested = word.suggested && word.suggested !== word.original;
    hasSuggestion ||= Boolean(suggested);
    const marker = document.createElement("button");
    marker.type = "button";
    marker.tabIndex = -1;
    marker.className = "ocr-word";
    marker.classList.toggle("is-suggested", Boolean(suggested));
    marker.classList.toggle("is-edited", value !== word.original);
    marker.setAttribute("aria-label", "Corregir " + word.original);
    marker.style.left = (word.x / size.width * 100) + "%";
    marker.style.top = (word.y / size.height * 100) + "%";
    marker.style.width = (word.width / size.width * 100) + "%";
    marker.style.height = (word.height / size.height * 100) + "%";
    marker.addEventListener("click", () => selectWord(word.id, true));
    markers.append(marker);

    const row = document.createElement("tr");
    row.insertCell().textContent = word.original;
    const suggestion = row.insertCell();
    const input = document.createElement("input");
    input.className = "ocr-correction";
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.maxLength = 256;
    input.value = value;
    input.setAttribute("aria-label", "Corrección de " + word.original);
    input.addEventListener("focus", () => selectWord(word.id));
    input.addEventListener("input", () => setText(word, input.value));
    row.insertCell().append(input);
    if (suggested) {
      const use = document.createElement("button");
      use.type = "button";
      use.className = "ocr-apply";
      use.textContent = word.suggested;
      use.setAttribute("aria-label", "Usar sugerencia " + word.suggested);
      use.addEventListener("click", () => {
        input.value = word.suggested;
        setText(word, input.value);
        selectWord(word.id, true);
      });
      suggestion.append(use);
    } else {
      suggestion.textContent = "Sin sugerencia";
    }
    rows.append(row);
    state.entries.set(word.id, { row, marker, input });
  }
  ui.overlay.replaceChildren(markers);
  ui.rows.replaceChildren(rows);
  ui.table.hidden = !page.words.length;
  ui.filterLabel.hidden = !hasSuggestion;
  if (!hasSuggestion) ui.filter.checked = false;
  showWordsMessage(page.words.length ? "" : "No se reconocieron palabras en esta página. Podés revisar la imagen antes de confirmar.");
  filterRows();
  selectWord(state.selection);
  updateReview();
}

async function savePage() {
  const page = state.pages.get(state.page);
  if (!page) return;
  setBusy(true);
  notify("");
  try {
    const payload = await (await request("/api/ocr/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        documentId: state.session.documentId,
        page: state.page,
        corrections: correctionsFor(page),
      }),
    })).json();
    const scroll = ui.reviewBody.scrollTop;
    state.pages.set(state.page, createPage(payload));
    invalidateCleanPages(state.page);
    renderWords();
    ui.reviewBody.scrollTop = scroll;
    showLearning(payload.learning);
    notify(payload.words.some((word) => word.text !== word.original)
      ? "Página confirmada. Las correcciones quedaron guardadas."
      : "Página confirmada sin cambios.");
  } catch (error) {
    notify(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function importLearning(file) {
  if (Array.from(state.pages.values()).some((page) => page.draft.size)) {
    notify("Guardá las correcciones pendientes antes de importar un ZIP.", true);
    return;
  }
  setBusy(true);
  notify("Importando aprendizaje…");
  try {
    const learning = await (await request("/api/ocr/learning/import", {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: file,
    })).json();
    showLearning(learning);
    if (state.session) {
      invalidateCleanPages();
      if (!state.pages.has(state.page)) await readPage(state.page);
    }
    notify("Aprendizaje importado. Las sugerencias se actualizaron.");
  } catch (error) {
    notify(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function exportLearning() {
  setBusy(true);
  notify("");
  try {
    const response = await request("/api/ocr/learning/export");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "planilla-aprendizaje-ocr.zip";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify("Aprendizaje descargado. Incluye las correcciones que ya guardaste.");
  } catch (error) {
    notify(error.message, true);
  } finally {
    setBusy(false);
  }
}

ui.open.addEventListener("click", () => ui.file.click());
ui.file.addEventListener("change", () => {
  const file = ui.file.files[0];
  ui.file.value = "";
  if (file) openPdf(file);
});
ui.prev.addEventListener("click", () => readPage(state.page - 1));
ui.next.addEventListener("click", () => readPage(state.page + 1));
ui.page.addEventListener("change", () => readPage(Number(ui.page.value)));
ui.retry.addEventListener("click", () => readPage(state.page));
ui.save.addEventListener("click", savePage);
ui.filter.addEventListener("change", filterRows);
ui.image.addEventListener("load", () => { ui.image.hidden = false; });
ui.image.addEventListener("error", () => {
  ui.paper.hidden = true;
  ui.imageError.hidden = false;
});
ui.import.addEventListener("click", () => ui.learningFile.click());
ui.learningFile.addEventListener("change", () => {
  const file = ui.learningFile.files[0];
  ui.learningFile.value = "";
  if (file) importLearning(file);
});
ui.export.addEventListener("click", exportLearning);
window.addEventListener("beforeunload", (event) => {
  if (!Array.from(state.pages.values()).some((page) => page.draft.size)) return;
  event.preventDefault();
  event.returnValue = true;
});
request("/api/ocr/learning")
  .then((response) => response.json())
  .then(showLearning)
  .catch((error) => { ui.learningSummary.textContent = error.message; });
