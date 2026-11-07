"use strict";

window.PlanillaCheckout = (() => {
  const element = (id) => document.getElementById(id);
  const dialog = element("download-dialog");
  const nodes = Object.fromEntries([
    "download-title", "download-status", "download-detail", "download-price", "download-email-form",
    "download-email", "download-email-button", "download-free", "download-pay", "download-file",
    "download-retry", "download-claim-form", "download-claim-reason", "download-claim-button",
    "download-contact", "download-success", "download-close", "download-edit",
    "download-test-notice", "recovery-test-notice", "download-test-mailbox", "recovery-test-mailbox",
    "recovery-dialog", "recovery-form", "recovery-email", "recovery-status", "recovery-send",
    "recovery-jobs", "recovery-job-list", "recovery-other",
  ].map((id) => [id, element(id)]));
  let config = {};
  let jobId;
  let filename = "formulario-rellenable.pdf";
  let current;
  let waiting = false;
  let timer;
  let downloaded = false;
  let generation = 0;
  let pendingPreparation = false;
  let draftPdf;
  const money = (amount) => new Intl.NumberFormat("es-AR", {
    style: "currency", currency: "ARS", maximumFractionDigits: 0,
  }).format(amount);
  const testMode = () => config.testMode === true;
  const testMail = () => testMode() && config.testEmailEnabled === true;
  const emailReady = () => config.emailConfigured === true || testMail();
  const freeDownloadedDetail = "Podés volver a descargar este mismo PDF mientras siga disponible. Esta descarga no consume otra conversión. Abrí la copia descargada para completar los campos.";
  const stateTitles = {
    payment_pending: "Pago pendiente",
    payment_rejected: "Pago no aprobado",
    expired: "Este trabajo venció",
    refunded: "Devolución confirmada",
  };

  async function request(url, data) {
    const response = await fetch(url, data === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "No pudimos completar este paso. Intentá de nuevo.");
    return result;
  }

  function cancelPoll() {
    window.clearTimeout(timer);
    timer = undefined;
  }

  function setWaiting(value) {
    waiting = value;
    for (const control of dialog.querySelectorAll("button, input, textarea")) {
      if (control !== nodes["download-close"] && control !== nodes["download-edit"]) control.disabled = value;
    }
    dialog.setAttribute("aria-busy", String(value));
  }

  function message(text, kind = "info") {
    nodes["download-status"].textContent = text;
    nodes["download-status"].dataset.kind = kind;
  }

  function showState(status) {
    current = status;
    const state = status.state;
    const ready = ["download_ready", "payment_approved"].includes(state);
    nodes["download-title"].textContent = ready ? "Tu PDF está listo para descargar" : stateTitles[state] || "Último paso para descargar";
    message(status.message || "Revisá las opciones para descargar tu PDF.",
      ["payment_rejected", "expired", "refunded"].includes(state) ? "warning" : "info");
    nodes["download-detail"].textContent = "";
    nodes["download-email-form"].hidden = !emailReady() || !["needs_email", "email_pending"].includes(state);
    nodes["download-test-mailbox"].hidden = !testMail() || !["needs_email", "email_pending"].includes(state);
    nodes["download-free"].hidden = state !== "free_available";
    const resumePayment = state === "payment_pending" && Boolean(status.checkoutUrl);
    nodes["download-pay"].hidden = config.paymentsConfigured !== true || (!["payment_required", "payment_rejected"].includes(state) && !resumePayment);
    nodes["download-file"].hidden = !ready;
    nodes["download-retry"].hidden = !["needs_preparation", "email_pending", "payment_pending", "payment_rejected"].includes(state);
    nodes["download-price"].hidden = !["payment_required", "payment_rejected"].includes(state);
    nodes["download-claim-form"].hidden = !status.canClaim || Boolean(status.claimStatus);
    nodes["download-email-button"].textContent = testMail()
      ? state === "email_pending" ? "Generar otro enlace de prueba" : "Generar enlace de prueba"
      : state === "email_pending" ? "Reenviar enlace al correo" : "Enviar enlace para confirmar";
    nodes["download-success"].hidden = !downloaded;
    nodes["download-contact"].hidden = !config.supportEmail;
    if (config.supportEmail) nodes["download-contact"].href = "mailto:" + config.supportEmail;
    const amount = status.price?.amount ?? config.price ?? 2990;
    nodes["download-price"].textContent = (testMode() ? "Importe de prueba: " : "") + money(amount) + " por este PDF · Hasta " + (config.maxPages ?? 5) + " páginas";
    nodes["download-pay"].textContent = testMode()
      ? resumePayment ? "Continuar el pago de prueba" : "Probar pago de " + money(amount) + " en Mercado Pago"
      : resumePayment ? "Continuar el pago en Mercado Pago" : "Pagar " + money(amount) + " con Mercado Pago";
    if (status.email && !nodes["download-email"].value) nodes["download-email"].value = status.email;
    if (state === "needs_email") {
      nodes["download-detail"].textContent = testMail()
        ? "Ingresá un correo para identificar esta prueba. El enlace de confirmación aparecerá en la bandeja privada del administrador; no llegará a tu casilla. Este paso no consume tu descarga gratis."
        : "Confirmá tu correo para recuperar este trabajo después. No necesitás crear una contraseña. Este paso todavía no consume tu descarga gratis.";
    } else if (state === "email_pending") {
      if (testMail()) message("Enlace de prueba generado. Tu gratuito no se consumió.");
      nodes["download-detail"].textContent = testMail()
        ? "Abrí la bandeja de correos de prueba del panel privado de administración y seguí el enlace de confirmación. Después volvé a este trabajo y comprobá el estado. No se envió un correo real."
        : "Abrí el enlace que te enviamos y volvé a esta página. Si no lo encontrás, revisá Correo no deseado. Todavía no se consumió tu descarga gratis.";
    } else if (state === "free_available") {
      nodes["download-detail"].textContent = "La primera conversión de hasta " + (config.freeMaxPages ?? 2) + " páginas es gratis. Se habilita al pulsar Descargar gratis. Si la conexión falla, podés reintentar este mismo trabajo.";
    } else if (state === "payment_required") {
      const pageCount = draftPdf?.pages?.length ?? 0;
      const freePageLimit = config.freeMaxPages ?? 2;
      const freeExplanation = !status.quotaConsumed && pageCount > freePageLimit
        ? "Este PDF tiene " + pageCount + " páginas. La descarga gratis cubre hasta " + freePageLimit + " páginas y sigue disponible para otro formulario. "
        : "";
      nodes["download-detail"].textContent = freeExplanation + "Podés volver al editor antes de pagar. El precio incluye volver a corregir este trabajo durante 24 horas. El archivo descargable se conserva por 7 días.";
    } else if (state === "payment_pending") {
      nodes["download-detail"].textContent = testMode()
        ? "Estamos esperando el resultado del pago de prueba de Mercado Pago. Comprobá el estado para actualizarlo. Si cerraste el checkout, podés continuar con la misma orden. No se puede preparar otra versión mientras esté pendiente."
        : "Estamos esperando la confirmación de Mercado Pago. Si ya pagaste, esperá y comprobá el estado. Si cerraste Mercado Pago antes de terminar, podés continuar con la misma orden. Mientras el pago esté pendiente, no se puede preparar otra versión del PDF.";
    } else if (state === "payment_rejected") {
      nodes["download-detail"].textContent = testMode()
        ? "Mercado Pago no aprobó este pago de prueba. El motivo aparece arriba. Podés comprobar el estado o iniciar un nuevo intento con otro resultado de prueba."
        : "Mercado Pago no confirmó este pago. Revisá el medio de pago e intentá nuevamente. Si ves un cargo en tu cuenta, comprobá el estado antes de volver a pagar.";
    } else if (ready) {
      if (status.grantKind === "original") {
        nodes["download-detail"].textContent = "El original ya era rellenable. Podés descargarlo gratis. Esta descarga no consume una conversión.";
      } else if (["free_pending", "free"].includes(status.grantKind)) {
        message(status.quotaConsumed ? "Este trabajo ya usó tu descarga gratis." : "Tu descarga gratis está lista. Todavía no se consumió.");
        nodes["download-detail"].textContent = status.quotaConsumed
          ? freeDownloadedDetail
          : "Se usará al iniciar la descarga del PDF. Si la conexión falla, podés reintentar este mismo trabajo sin consumir otra conversión.";
      } else {
        nodes["download-detail"].textContent = "Podés volver a descargar este mismo trabajo sin pagar otra vez mientras siga disponible. Abrí la copia descargada para completar los campos.";
      }
    } else if (state === "expired") {
      nodes["download-detail"].textContent = testMode()
        ? "El plazo de este trabajo de prueba terminó y sus archivos se borraron. Podés abrir el PDF otra vez para iniciar otro ensayo."
        : "El plazo de este trabajo terminó y sus archivos se borraron. Podés abrir el PDF otra vez. Si ya habías pagado y necesitás ayuda, escribinos con el comprobante.";
    } else if (state === "refunded") {
      nodes["download-detail"].textContent = testMode()
        ? "Mercado Pago confirmó la devolución de prueba. No se movió dinero real."
        : "Mercado Pago confirmó la devolución. El plazo de acreditación depende de tu medio de pago.";
    }
    if (!emailReady() && ["needs_email", "email_pending"].includes(state)) {
      message("La confirmación por correo todavía no está habilitada. Tu gratuito no se consumió.", "warning");
      nodes["download-detail"].textContent = "Podés volver al editor para seguir revisando y corrigiendo los campos. Por ahora no podemos enviar el enlace necesario para habilitar esta descarga.";
    } else if (config.paymentsConfigured !== true && ["payment_required", "payment_rejected", "payment_pending"].includes(state)) {
      message("Los pagos todavía no están habilitados. Tu trabajo está guardado.", "warning");
      nodes["download-detail"].textContent = state === "payment_pending"
        ? "Si ya intentaste pagar, comprobá el estado antes de volver a hacerlo. Tu trabajo conserva el mismo estado mientras esperamos la confirmación."
        : "Podés volver al editor para seguir revisando y corrigiendo. Esta descarga necesita un pago y por ahora no podemos iniciarlo.";
    }
    if (status.claimStatus) {
      nodes["download-detail"].textContent += " Tu reclamo quedó registrado. Podés revisar su estado desde el enlace de recuperación de este trabajo.";
    }
    cancelPoll();
    if (["payment_pending", "email_pending"].includes(state) && dialog.open) {
      timer = window.setTimeout(refresh, 4000);
    }
  }

  async function action(callback) {
    if (waiting) return;
    setWaiting(true);
    try {
      await callback();
    } catch (error) {
      message(error.message === "Failed to fetch" ? "Se cortó la conexión. Tus cambios siguen en el editor. Comprobá el estado y volvé a intentar." : error.message, "error");
      nodes["download-retry"].hidden = false;
    } finally {
      setWaiting(false);
    }
  }

  async function refresh() {
    if (!jobId || !dialog.open) return;
    if (pendingPreparation && draftPdf) {
      await open(draftPdf, true);
      return;
    }
    await action(async () => showState(await request("/api/jobs/" + encodeURIComponent(jobId) + "/status")));
  }

  async function open(pdf, prepare = true) {
    if (waiting) {
      if (!dialog.open) dialog.showModal();
      return;
    }
    generation += 1;
    current = undefined;
    draftPdf = pdf;
    pendingPreparation = prepare;
    const attempt = generation;
    cancelPoll();
    jobId = pdf.documentId;
    const preparedFields = JSON.stringify(pdf.fields);
    filename = (pdf.filename || "formulario.pdf").replace(/\.pdf$/i, "") + "-rellenable.pdf";
    downloaded = false;
    nodes["download-success"].hidden = true;
    nodes["download-email"].value = "";
    for (const id of ["download-email-form", "download-test-mailbox", "download-free", "download-pay", "download-file", "download-price", "download-retry", "download-claim-form"]) nodes[id].hidden = true;
    nodes["download-title"].textContent = "Preparando tu descarga";
    nodes["download-detail"].textContent = "Estamos comprobando que el PDF se pueda abrir y completar. Todavía no se consumió tu descarga gratis.";
    message("Preparando y comprobando el PDF…");
    if (!dialog.open) dialog.showModal();
    await action(async () => {
      const status = prepare
        ? await request("/api/prepare", { documentId: jobId, fields: JSON.parse(preparedFields) })
        : await request("/api/jobs/" + encodeURIComponent(jobId) + "/status");
      if (attempt !== generation) return;
      pendingPreparation = status.state === "needs_preparation";
      showState(status);
      if (prepare) window.dispatchEvent(new CustomEvent("planilla:prepared", {
        detail: { documentId: jobId, fields: preparedFields },
      }));
    });
  }

  nodes["download-email-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    action(async () => showState(await request("/api/jobs/" + encodeURIComponent(jobId) + "/email", {
      email: nodes["download-email"].value.trim(),
    })));
  });
  nodes["download-free"].addEventListener("click", () => action(async () => {
    showState(await request("/api/jobs/" + encodeURIComponent(jobId) + "/free", {}));
    if (["download_ready", "payment_approved"].includes(current.state)) await downloadFile();
  }));
  nodes["download-pay"].addEventListener("click", () => action(async () => {
    message(testMode() ? "Abriendo el pago de prueba en Mercado Pago…" : "Abriendo Mercado Pago…");
    const result = current.state === "payment_pending" && current.checkoutUrl
      ? current
      : await request("/api/jobs/" + encodeURIComponent(jobId) + "/checkout", {});
    const target = new URL(result.checkoutUrl);
    if (target.protocol !== "https:") throw new Error("No pudimos abrir el pago. Comprobá el estado e intentá de nuevo.");
    window.location.assign(target.href);
  }));
  async function downloadFile() {
    message("Descargando tu PDF…");
    const response = await fetch(current.downloadUrl || "/api/jobs/" + encodeURIComponent(jobId) + "/download");
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.error || "No pudimos iniciar la descarga. Podés reintentar este mismo trabajo.");
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    downloaded = true;
    const freeDownload = ["free_pending", "free"].includes(current.grantKind);
    if (freeDownload) nodes["download-detail"].textContent = freeDownloadedDetail;
    nodes["download-success"].hidden = false;
    nodes["download-success"].textContent = current.emailVerified
      ? testMail() ? "Guardá la copia en tu equipo. Para recuperar esta prueba, generá un enlace y abrilo desde la bandeja privada del administrador." : "Guardá la copia en tu equipo. También podés recuperarla con el enlace de tu correo."
      : "Guardá la copia en tu equipo para volver a usarla cuando la necesites.";
    message(freeDownload ? "Tu PDF fue descargado. Este trabajo ya usó tu descarga gratis." : "Tu PDF fue descargado. Podés abrirlo para completar los campos.");
    window.dispatchEvent(new CustomEvent("planilla:downloaded", { detail: { recoveryAvailable: Boolean(current.emailVerified) } }));
  }
  nodes["download-file"].addEventListener("click", () => action(downloadFile));
  nodes["download-retry"].addEventListener("click", refresh);
  nodes["download-claim-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    action(async () => {
      const result = await request("/api/jobs/" + encodeURIComponent(jobId) + "/claim", {
        reason: nodes["download-claim-reason"].value.trim(),
      });
      if (result.state) showState(result);
      else {
        nodes["download-claim-form"].hidden = true;
        message("Tu reclamo quedó registrado. Podés revisar su estado desde el enlace de recuperación de este trabajo.");
      }
    });
  });
  nodes["download-close"].addEventListener("click", () => dialog.close());
  nodes["download-edit"].addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", cancelPoll);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && dialog.open && current && ["payment_pending", "email_pending"].includes(current.state)) refresh();
  });

  async function openRecovery(explanation) {
    nodes["recovery-status"].textContent = explanation || "Buscando los trabajos disponibles de esta sesión…";
    nodes["recovery-jobs"].hidden = true;
    nodes["recovery-other"].hidden = true;
    nodes["recovery-form"].hidden = !emailReady();
    nodes["recovery-job-list"].replaceChildren();
    if (!nodes["recovery-dialog"].open) nodes["recovery-dialog"].showModal();
    try {
      const { jobs = [] } = await request("/api/jobs");
      if (!jobs.length) {
        nodes["recovery-status"].textContent = emailReady()
          ? explanation || (testMail() ? "Ingresá el correo de esta prueba. El enlace de recuperación aparecerá en la bandeja privada del administrador." : "Ingresá el correo que usaste para descargar o comprar.")
          : (explanation ? explanation + " " : "No hay trabajos disponibles en esta sesión. ") + "La recuperación por correo todavía no está habilitada.";
        return;
      }
      const labels = {
        download_ready: "Listo para descargar", payment_approved: "Pago aprobado", free_available: "Gratis disponible",
        payment_required: "Pendiente de pago", payment_pending: "Pago pendiente", payment_rejected: "Pago no aprobado",
        needs_preparation: "Campos por revisar", needs_email: "Confirmar correo", email_pending: "Confirmar correo", refunded: "Pago devuelto",
      };
      const dateFormat = new Intl.DateTimeFormat("es-AR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      for (const job of jobs) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = "/?job=" + encodeURIComponent(job.jobId);
        const date = new Date(typeof job.created === "number" ? job.created * 1000 : job.created);
        const description = document.createElement("span");
        description.textContent = dateFormat.format(date) + " · " + job.pages + (job.pages === 1 ? " página" : " páginas");
        const state = document.createElement("small");
        state.textContent = (labels[job.state] || "Trabajo disponible") + " · Abrir trabajo";
        const expiry = document.createElement("small");
        expiry.textContent = "Disponible hasta " + dateFormat.format(new Date(job.expiresAt));
        link.append(description, state, expiry);
        item.append(link);
        nodes["recovery-job-list"].append(item);
      }
      nodes["recovery-jobs"].hidden = false;
      nodes["recovery-other"].hidden = !emailReady();
      nodes["recovery-form"].hidden = true;
      nodes["recovery-status"].textContent = explanation || "Abrí un trabajo para revisar sus campos o descargarlo. Cada uno muestra hasta cuándo sigue disponible.";
    } catch {
      nodes["recovery-status"].textContent = emailReady()
        ? explanation || "No pudimos consultar esta sesión. Podés recuperar tus trabajos con el correo que usaste."
        : "No pudimos consultar tus trabajos. Cerrá esta ventana e intentá de nuevo. La recuperación por correo todavía no está habilitada.";
    }
  }
  element("open-recovery").addEventListener("click", () => openRecovery());
  nodes["recovery-other"].addEventListener("click", () => {
    nodes["recovery-form"].hidden = false;
    nodes["recovery-other"].hidden = true;
    nodes["recovery-email"].focus();
  });
  element("recovery-close").addEventListener("click", () => nodes["recovery-dialog"].close());
  nodes["recovery-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    nodes["recovery-send"].disabled = true;
    nodes["recovery-status"].textContent = "Buscando tus trabajos…";
    try {
      await request("/api/recovery", { email: nodes["recovery-email"].value.trim() });
      nodes["recovery-status"].textContent = testMail()
        ? "Si hay trabajos disponibles con ese correo, generamos un enlace en la bandeja privada del administrador para recuperarlos. No se envió un correo real."
        : "Si hay trabajos disponibles con ese correo, te enviamos un enlace para recuperarlos. Revisá también Correo no deseado.";
    } catch (error) {
      nodes["recovery-status"].textContent = error.message;
    } finally {
      nodes["recovery-send"].disabled = false;
    }
  });

  return { configure(value) {
    config = value;
    nodes["download-test-notice"].hidden = !testMode();
    nodes["recovery-test-notice"].hidden = !testMail();
    nodes["download-test-mailbox"].hidden = !testMail() || !["needs_email", "email_pending"].includes(current?.state);
    nodes["recovery-test-mailbox"].hidden = !testMail();
    nodes["recovery-send"].textContent = testMail() ? "Generar enlace de recuperación de prueba" : "Enviar enlace de recuperación";
  }, open, refresh, openRecovery };
})();
