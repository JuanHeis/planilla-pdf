"use strict";

(() => {
  const ids = ["usage-period-form", "usage-month", "usage-status", "usage-data", "usage-period-note",
    "usage-summary", "usage-actual-form", "usage-actual", "usage-detail", "usage-operations", "usage-limitations",
    "learning-status", "learning-data", "learning-mode", "learning-summary", "learning-guidance", "learning-toggle",
    "learning-export", "learning-confirm", "learning-confirm-text", "learning-confirm-apply", "learning-confirm-cancel",
    "learning-import-form", "learning-import", "test-mailbox-section", "test-mailbox-refresh",
    "test-mailbox-status", "test-mailbox-retention", "test-mailbox-list"];
  const nodes = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
  const number = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 3 });
  const dollars = new Intl.NumberFormat("es-AR", { style: "currency", currency: "USD", maximumFractionDigits: 5 });
  let active = false;
  let activeToken = "";
  let epoch = 0;
  let usageBusy = false;
  let learningBusy = false;
  let learning;
  let pendingMode;
  let reportedMonth;
  let mailboxBusy = false;
  let mailboxVersion = 0;
  let testEmailEnabled = false;
  nodes["usage-month"].value = new Date().toISOString().slice(0, 7);

  function notice(id, text, error = false) {
    nodes[id].hidden = false;
    nodes[id].dataset.error = String(error);
    nodes[id].textContent = text;
  }

  function rows(id, data) {
    nodes[id].replaceChildren(...data.map((values) => {
      const row = document.createElement("tr");
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      return row;
    }));
  }

  const usd = (value, empty = "Sin datos") => typeof value === "number" && Number.isFinite(value) ? dollars.format(value) : empty;
  const quantity = (value) => number.format(Number.isFinite(value) ? value : 0);
  const current = (stamp) => active && stamp === epoch && Boolean(accessToken) && accessToken === activeToken;

  function mailboxWaiting(value) {
    mailboxBusy = value;
    nodes["test-mailbox-refresh"].disabled = value;
    nodes["test-mailbox-section"].setAttribute("aria-busy", String(value));
  }

  function clearMailbox() {
    mailboxVersion += 1;
    nodes["test-mailbox-section"].hidden = true;
    nodes["test-mailbox-list"].replaceChildren();
    nodes["test-mailbox-retention"].textContent = "";
    nodes["test-mailbox-status"].textContent = "";
    mailboxWaiting(false);
  }

  function verificationLink(value) {
    try {
      const url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin || url.pathname !== "/verify" || !url.searchParams.get("token") || url.username || url.password) return;
      return url;
    } catch { return; }
  }

  function renderMailbox(result) {
    if (result.enabled !== true) {
      clearMailbox();
      return;
    }
    const messages = Array.isArray(result.messages) ? result.messages.slice(0, 100) : [];
    const dateFormat = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    nodes["test-mailbox-list"].replaceChildren(...messages.map((item) => {
      const row = document.createElement("li");
      const title = document.createElement("h3");
      title.textContent = item.subject || "Correo de prueba";
      const meta = document.createElement("p");
      meta.className = "admin-hint test-mailbox-meta";
      const created = new Date(Number(item.created) * 1000);
      meta.textContent = (item.to || "Destinatario de prueba") + " · " + (Number.isFinite(created.getTime()) ? dateFormat.format(created) : "Fecha no disponible");
      row.append(title, meta);
      const target = verificationLink(item.verificationUrl);
      if (target) {
        const link = document.createElement("a");
        link.className = "button secondary";
        link.textContent = "Abrir enlace de prueba";
        link.href = target.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.setAttribute("aria-label", "Abrir enlace de prueba para " + (item.to || "este destinatario") + " en otra pestaña");
        row.append(link);
      } else {
        const explanation = document.createElement("p");
        explanation.className = "admin-hint";
        explanation.textContent = "No abrimos este enlace porque no corresponde a esta dirección del servicio. Revisá la dirección configurada para las pruebas.";
        row.append(explanation);
      }
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Ver mensaje";
      const text = document.createElement("p");
      text.className = "test-mailbox-text";
      text.textContent = item.text || "Sin texto disponible.";
      details.append(summary, text);
      row.append(details);
      return row;
    }));
    const minutes = Number.isFinite(result.retentionSeconds) && result.retentionSeconds > 0 ? Math.ceil(result.retentionSeconds / 60) : 15;
    nodes["test-mailbox-retention"].textContent = "La bandeja conserva los mensajes durante " + minutes + " minutos. Si un enlace venció o ya se usó, generá otro desde la descarga o la recuperación.";
    notice("test-mailbox-status", messages.length
      ? "Bandeja de prueba actualizada. Abrí el enlace del correo que estás probando."
      : "Todavía no hay correos de prueba. Generá un enlace desde la descarga o la recuperación y después actualizá esta bandeja.");
  }

  async function refreshMailbox() {
    if (!active || !testEmailEnabled || mailboxBusy) return;
    const stamp = epoch;
    const version = ++mailboxVersion;
    const currentMailbox = () => current(stamp) && testEmailEnabled && version === mailboxVersion;
    mailboxWaiting(true);
    notice("test-mailbox-status", "Consultando los correos de prueba…");
    try {
      const result = await call("/api/admin/test-mailbox");
      if (currentMailbox()) renderMailbox(result);
    } catch (error) {
      if (currentMailbox()) notice("test-mailbox-status", error.message + " Podés actualizar la bandeja para reintentar.", true);
    } finally {
      if (currentMailbox()) mailboxWaiting(false);
    }
  }

  nodes["test-mailbox-refresh"].addEventListener("click", refreshMailbox);

  function usageWaiting(value) {
    usageBusy = value;
    for (const form of [nodes["usage-period-form"], nodes["usage-actual-form"]]) {
      for (const control of form.querySelectorAll("input, button")) control.disabled = value;
    }
    nodes["usage-data"].setAttribute("aria-busy", String(value));
  }

  function learningWaiting(value) {
    learningBusy = value;
    for (const control of nodes["learning-data"].querySelectorAll("input, button")) control.disabled = value;
    if (!value && learning && !learning.autoApply && eligible(learning.stats) === 0) nodes["learning-toggle"].disabled = true;
    nodes["learning-data"].setAttribute("aria-busy", String(value));
  }

  function renderUsage(report) {
    const totals = report.totals || {};
    const costs = report.costs || {};
    reportedMonth = report.month;
    nodes["usage-month"].value = report.month;
    nodes["usage-actual"].value = typeof costs.actualMonthlyUSD === "number" ? costs.actualMonthlyUSD.toFixed(2) : "";
    rows("usage-summary", [
      ["PDFs distintos con actividad medida", quantity(totals.documents)],
      ["Páginas de esos PDF", quantity(totals.pages)],
      ["Gasto real cargado para el mes", usd(costs.actualMonthlyUSD, "Todavía no cargaste la factura")],
      ["Costo medio completo por PDF", usd(costs.fullyAllocatedPerPdfUSD, totals.documents ? "Cargá el gasto real para calcularlo" : "Necesita PDF medidos en el mes")],
      ["CPU estimada del mes — costo parcial", usd(costs.cpuEstimateUSD)],
      ["Mínimo Hobby de USD 5 repartido por PDF — escenario", usd(costs.hobbyBaselinePerPdfUSD, "Sin un período completo o sin PDF medidos")],
    ]);
    const since = report.measuredSince ? new Intl.DateTimeFormat("es-AR", { dateStyle: "medium", timeZone: "UTC" }).format(new Date(report.measuredSince)) : null;
    nodes["usage-period-note"].textContent = "Período " + report.month + " en UTC. "
      + (since ? "Medición disponible desde " + since + "." : "Todavía no hay operaciones medidas.")
      + (report.retentionMayBePartial ? " El detalle de este mes puede estar incompleto por la retención de 90 días; no se calcula un costo medio completo." : "");
    const labels = { detect: "Analizar PDF", render: "Generar vista previa", prepare: "Preparar descarga", download: "Entregar archivo" };
    rows("usage-operations", (report.operations || []).map((item) => [labels[item.operation] || item.operation,
      quantity(item.requests), quantity(item.failed), quantity(item.cpuSeconds), quantity(item.elapsedSeconds)]));
    if (!(report.operations || []).length) rows("usage-operations", [["Sin operaciones en este mes", "0", "0", "0", "0"]]);
    nodes["usage-detail"].textContent = quantity(totals.analysisAttempts) + " intentos de análisis, " + quantity(totals.failedAnalyses)
      + " fallidos. " + quantity(totals.operations) + " operaciones medidas en total. Los tiempos y la CPU incluyen reintentos y fallos registrados.";
    nodes["usage-limitations"].textContent = "La estimación de CPU no incluye RAM, almacenamiento, tráfico, correo, comisiones ni impuestos. "
      + "El costo medio con factura reparte el gasto que ingresaste; no mide el costo marginal exacto de un archivo. "
      + "Los registros de operaciones se conservan 90 días y los importes mensuales 24 meses.";
    nodes["usage-data"].hidden = false;
    notice("usage-status", totals.documents ? "Mediciones del mes actualizadas." : "Todavía no hay PDF medidos en este mes. Podés consultar otro período.");
  }

  async function refreshUsage() {
    if (!active || usageBusy) return;
    const stamp = epoch;
    usageWaiting(true);
    notice("usage-status", "Consultando las mediciones del mes…");
    try {
      const report = await call("/api/admin/usage?month=" + encodeURIComponent(nodes["usage-month"].value));
      if (current(stamp)) renderUsage(report);
    } catch (error) {
      if (current(stamp)) notice("usage-status", error.message + " Podés volver a consultar el mes.", true);
    } finally {
      if (current(stamp)) usageWaiting(false);
    }
  }

  const eligible = (stats = {}) => (stats.eligibleAdjustments || 0) + (stats.eligibleSuppressions || 0);

  function renderLearning(result) {
    learning = result;
    const stats = result.stats || {};
    const ready = eligible(stats);
    rows("learning-summary", [
      ["Revisiones confirmadas", quantity(stats.reviews)],
      ["Diseños distintos revisados", quantity(stats.layouts)],
      ["Patrones observados", quantity(stats.patterns)],
      ["Patrones elegibles para ajustes", quantity(stats.eligibleAdjustments)],
      ["Patrones elegibles para quitar campos de baja confianza", quantity(stats.eligibleSuppressions)],
      ["Campos agregados manualmente en revisiones", quantity(stats.addedFieldsReviewed)],
      ["Fuentes de aprendizaje importadas", quantity(stats.importedSources)],
      ["Versión del modelo", stats.modelVersion || "Sin versión disponible"],
    ]);
    nodes["learning-mode"].textContent = result.autoApply
      ? "Ajustes automáticos activos para los patrones que cumplan las condiciones de seguridad."
      : "Modo de evaluación: aprende de las revisiones, pero no cambia automáticamente la detección.";
    nodes["learning-guidance"].textContent = ready
      ? "Hay patrones con evidencia suficiente para el piloto. Antes de activar los ajustes, compará resultados sobre formularios nuevos y revisá los falsos positivos. Siempre se conserva la edición manual."
      : "Todavía no hay patrones con evidencia suficiente para aplicar cambios. Necesita revisiones coherentes de varios diseños distintos; repetir el mismo formulario no demuestra generalización.";
    nodes["learning-guidance"].textContent += " Los campos añadidos ayudan a medir omisiones, pero este piloto no inventa campos nuevos sin una propuesta geométrica.";
    nodes["learning-toggle"].textContent = result.autoApply ? "Desactivar ajustes automáticos" : "Activar ajustes automáticos";
    nodes["learning-data"].hidden = false;
    nodes["learning-confirm"].hidden = true;
    pendingMode = undefined;
    learningWaiting(false);
    notice("learning-status", stats.reviews || stats.importedSources ? "Estado del aprendizaje actualizado." : "Todavía no hay revisiones o aprendizaje importado. El piloto comienza sin patrones aprendidos.");
  }

  async function refreshLearning() {
    if (!active || learningBusy) return;
    const stamp = epoch;
    learningWaiting(true);
    notice("learning-status", "Consultando el aprendizaje…");
    try {
      const result = await call("/api/admin/field-learning");
      if (current(stamp)) renderLearning(result);
    } catch (error) {
      if (current(stamp)) notice("learning-status", error.message + " Podés actualizar la actividad para reintentar.", true);
    } finally {
      if (current(stamp)) learningWaiting(false);
    }
  }

  nodes["usage-period-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    refreshUsage();
  });
  nodes["usage-actual-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!active || usageBusy) return;
    if (reportedMonth !== nodes["usage-month"].value) {
      notice("usage-status", "Consultá el mes seleccionado antes de guardar su gasto.", true);
      return;
    }
    const stamp = epoch;
    const amount = nodes["usage-actual"].valueAsNumber;
    if (!Number.isFinite(amount) || amount < 0 || amount > 1_000_000) {
      notice("usage-status", "Ingresá un importe válido en USD entre 0 y 1.000.000.", true);
      return;
    }
    usageWaiting(true);
    notice("usage-status", "Guardando el gasto real del mes…");
    try {
      const result = await call("/api/admin/usage/actual", { month: reportedMonth, totalUSD: amount });
      if (current(stamp)) {
        renderUsage(result);
        notice("usage-status", "Gasto guardado. El promedio usa ese importe y los PDF distintos con actividad medida.");
      }
    } catch (error) {
      if (current(stamp)) notice("usage-status", error.message + " El importe sigue en el formulario para reintentar.", true);
    } finally {
      if (current(stamp)) usageWaiting(false);
    }
  });
  nodes["learning-toggle"].addEventListener("click", () => {
    if (!active || learningBusy || !learning) return;
    pendingMode = !learning.autoApply;
    nodes["learning-confirm-text"].textContent = pendingMode
      ? "¿Activar los ajustes automáticos del piloto? Solo se aplicarán patrones elegibles a documentos nuevos. La evidencia acumulada no garantiza una detección completa y todavía habrá que revisar el resultado."
      : "¿Desactivar los ajustes automáticos? Se conservará lo aprendido y continuará el modo de evaluación. Los trabajos ya abiertos no se modifican.";
    nodes["learning-confirm"].hidden = false;
    nodes["learning-confirm-apply"].focus();
  });
  nodes["learning-confirm-cancel"].addEventListener("click", () => {
    nodes["learning-confirm"].hidden = true;
    pendingMode = undefined;
    nodes["learning-toggle"].focus();
  });
  nodes["learning-confirm-apply"].addEventListener("click", async () => {
    if (!active || learningBusy || typeof pendingMode !== "boolean") return;
    const stamp = epoch;
    learningWaiting(true);
    notice("learning-status", "Guardando el modo de aprendizaje…");
    try {
      const result = await call("/api/admin/field-learning/settings", { autoApply: pendingMode });
      if (current(stamp)) renderLearning(result);
    } catch (error) {
      if (current(stamp)) notice("learning-status", error.message + " El modo no se pudo confirmar; volvé a intentar.", true);
    } finally {
      if (current(stamp)) learningWaiting(false);
    }
  });
  nodes["learning-export"].addEventListener("click", async () => {
    if (!active || learningBusy) return;
    const stamp = epoch;
    learningWaiting(true);
    notice("learning-status", "Preparando el ZIP de aprendizaje…");
    try {
      const response = await fetch("/api/admin/field-learning/export", { headers: { Authorization: "Bearer " + accessToken } });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.error || "No pudimos preparar el aprendizaje.");
      }
      const blob = await response.blob();
      if (!current(stamp)) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "Planilla-aprendizaje-campos-" + new Date().toISOString().slice(0, 10) + ".zip";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      notice("learning-status", "Aprendizaje descargado. El ZIP contiene estadísticas de patrones, sin textos ni PDF.");
    } catch (error) {
      if (current(stamp)) notice("learning-status", error.message + " Podés volver a descargarlo.", true);
    } finally {
      if (current(stamp)) learningWaiting(false);
    }
  });
  nodes["learning-import-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!active || learningBusy) return;
    const file = nodes["learning-import"].files[0];
    if (!file || file.size > 2 * 1024 * 1024 || !file.name.toLowerCase().endsWith(".zip")) {
      notice("learning-status", "Elegí un ZIP de aprendizaje de Planilla de hasta 2 MB.", true);
      return;
    }
    const stamp = epoch;
    learningWaiting(true);
    notice("learning-status", "Validando e incorporando el aprendizaje…");
    try {
      const response = await fetch("/api/admin/field-learning/import", { method: "POST",
        headers: { Authorization: "Bearer " + accessToken, "Content-Type": "application/zip" }, body: file });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || "No pudimos importar ese aprendizaje.");
      if (!current(stamp)) return;
      // The import endpoint may return the full state or the learner's statistics.
      renderLearning(result.stats ? result : { autoApply: learning?.autoApply || false, stats: result });
      nodes["learning-import"].value = "";
      notice("learning-status", "Aprendizaje incorporado. Revisá los patrones disponibles antes de activar cambios automáticos.");
    } catch (error) {
      if (current(stamp)) notice("learning-status", error.message + " Conservá el archivo y revisá su formato antes de reintentar.", true);
    } finally {
      if (current(stamp)) learningWaiting(false);
    }
  });
  window.addEventListener("planilla:admin-open", (event) => {
    if (!active || activeToken !== accessToken) {
      active = true;
      activeToken = accessToken;
      epoch += 1;
      mailboxWaiting(false);
      usageWaiting(false);
      learningWaiting(false);
    }
    const mailboxEnabled = event.detail?.testMode === true && event.detail?.testEmailEnabled === true;
    if (testEmailEnabled !== mailboxEnabled) clearMailbox();
    testEmailEnabled = mailboxEnabled;
    nodes["test-mailbox-section"].hidden = !testEmailEnabled;
    if (testEmailEnabled) refreshMailbox();
    refreshUsage();
    refreshLearning();
  });
  window.addEventListener("planilla:admin-close", () => {
    active = false;
    activeToken = "";
    epoch += 1;
    learning = undefined;
    reportedMonth = undefined;
    pendingMode = undefined;
    testEmailEnabled = false;
    clearMailbox();
    nodes["usage-data"].hidden = true;
    nodes["learning-data"].hidden = true;
    nodes["learning-confirm"].hidden = true;
    for (const id of ["usage-summary", "usage-operations", "learning-summary"]) nodes[id].replaceChildren();
    nodes["usage-actual"].value = "";
    nodes["learning-import"].value = "";
    usageWaiting(false);
    learningWaiting(false);
    notice("usage-status", "Las mediciones se consultan al abrir la actividad.");
    notice("learning-status", "El aprendizaje se consulta al abrir la actividad.");
  });
})();
