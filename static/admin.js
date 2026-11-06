"use strict";

const admin = Object.fromEntries([
  "admin-access", "admin-login", "admin-token", "admin-status", "admin-report",
  "admin-summary", "admin-funnel", "admin-daily", "admin-updated", "admin-refresh", "admin-logout", "admin-claims",
  "refund-dialog", "refund-details", "refund-status", "refund-confirm", "refund-cancel",
].map((id) => [id, document.getElementById(id)]));
let accessToken = "";
let loading = false;
let pendingClaim;
let refunding = false;
const amounts = new Intl.NumberFormat("es-AR", {
  style: "currency", currency: "ARS", maximumFractionDigits: 0,
});
const stages = [
  ["visit", "Visitas"],
  ["preview_ready", "PDFs analizados"],
  ["detection_failed", "Análisis fallidos"],
  ["corrected", "Trabajos con correcciones"],
  ["prepared", "Descargas preparadas"],
  ["prepare_failed", "Preparaciones fallidas"],
  ["email_requested", "Enlaces de correo solicitados"],
  ["email_verified", "Correos confirmados"],
  ["checkout_started", "Pagos iniciados"],
  ["payment_approved", "Pagos aprobados"],
  ["payment_rejected", "Pagos rechazados"],
  ["free_redeemed", "Conversiones gratuitas habilitadas"],
  ["download", "Descargas de conversiones"],
  ["original_download", "Descargas de originales rellenables"],
  ["claim_requested", "Reclamos recibidos"],
  ["payment_refunded", "Devoluciones confirmadas"],
];

function status(message, error = false) {
  admin["admin-status"].hidden = false;
  admin["admin-status"].textContent = message;
  admin["admin-status"].dataset.error = String(error);
}

async function call(path, body) {
  const options = { headers: { Authorization: "Bearer " + accessToken } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || "No pudimos abrir la actividad. Revisá la clave e intentá de nuevo.");
  return result;
}

function summary(label, value) {
  const box = document.createElement("div");
  const amount = document.createElement("dd");
  amount.textContent = value;
  const name = document.createElement("dt");
  name.textContent = label;
  box.append(amount, name);
  return box;
}

function claimRow(claim) {
  const row = document.createElement("article");
  row.className = "claim-row";
  const title = document.createElement("h3");
  title.textContent = claim.email || "Reclamo registrado";
  const reason = document.createElement("p");
  reason.className = "claim-reason";
  reason.textContent = claim.reason;
  const details = document.createElement("p");
  const labels = { requested: "Pendiente", open: "Pendiente", reviewing: "En revisión", refund_requested: "Devolución solicitada", refunded: "Devuelto", resolved: "Resuelto", closed: "Cerrado", rejected: "Cerrado" };
  details.textContent = "Estado " + (labels[claim.state] || "Registrado") + " · Orden " + (claim.provider_id || "sin orden")
    + " · " + new Date(claim.created * 1000).toLocaleString("es-AR");
  row.append(title, reason, details);
  if (["refund_requested", "refunded", "resolved", "closed"].includes(claim.state)) return row;

  const refund = document.createElement("button");
  refund.type = "button";
  refund.className = "button secondary";
  refund.textContent = "Solicitar devolución";
  refund.addEventListener("click", () => {
    if (loading || refunding) return;
    pendingClaim = claim;
    admin["refund-details"].textContent = "Vas a solicitar la devolución de la orden " + claim.provider_id + " para " + claim.email + ".";
    admin["refund-status"].hidden = true;
    admin["refund-dialog"].showModal();
  });
  row.append(refund);
  return row;
}

function render(metrics, claims) {
  const counts = metrics.counts || {};
  admin["admin-summary"].replaceChildren(
    summary("Cobros aprobados, antes de comisiones", amounts.format(metrics.grossApprovedARS || 0)),
    summary("Devoluciones confirmadas", amounts.format(metrics.refundedARS || 0)),
    summary("Reclamos pendientes", String(metrics.openClaims || 0)),
  );
  admin["admin-funnel"].replaceChildren(...stages.map(([key, label]) => {
    const row = document.createElement("tr");
    const text = document.createElement("td");
    text.textContent = label;
    const value = document.createElement("td");
    value.textContent = String(counts[key] || 0);
    row.append(text, value);
    return row;
  }));
  const days = new Map();
  for (const item of metrics.daily || []) {
    if (!days.has(item.day)) days.set(item.day, {});
    days.get(item.day)[item.event] = item.count;
  }
  admin["admin-daily"].replaceChildren(...[...days].sort(([a], [b]) => b.localeCompare(a)).map(([day, values]) => {
    const row = document.createElement("tr");
    for (const value of [day.split("-").reverse().join("/"), values.preview_ready || 0, values.free_redeemed || 0, values.payment_approved || 0]) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    return row;
  }));
  const entries = Array.isArray(claims) ? claims : (claims.claims || []);
  admin["admin-claims"].replaceChildren(...entries.map(claimRow));
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.textContent = "No hay reclamos para revisar.";
    admin["admin-claims"].append(empty);
  }
  admin["admin-updated"].textContent = "Actualizado " + new Date().toLocaleString("es-AR");
}

async function refresh() {
  if (loading) return;
  loading = true;
  admin["admin-refresh"].disabled = true;
  try {
    const [metrics, claims] = await Promise.all([
      call("/api/admin/metrics"), call("/api/admin/claims"),
    ]);
    render(metrics, claims);
    admin["admin-access"].hidden = true;
    admin["admin-report"].hidden = false;
    admin["admin-status"].hidden = true;
    window.dispatchEvent(new Event("planilla:admin-open"));
  } catch (error) {
    status(error.message, true);
  } finally {
    loading = false;
    admin["admin-refresh"].disabled = false;
  }
}

admin["admin-login"].addEventListener("submit", (event) => {
  event.preventDefault();
  accessToken = admin["admin-token"].value.trim();
  admin["admin-token"].value = "";
  refresh();
});
admin["admin-refresh"].addEventListener("click", refresh);
admin["refund-cancel"].addEventListener("click", () => {
  if (!refunding) admin["refund-dialog"].close();
});
admin["refund-confirm"].addEventListener("click", async () => {
  if (!pendingClaim || refunding) return;
  const claim = pendingClaim;
  refunding = true;
  admin["refund-confirm"].disabled = true;
  admin["refund-cancel"].disabled = true;
  admin["refund-status"].hidden = false;
  admin["refund-status"].dataset.error = "false";
  admin["refund-status"].textContent = "Solicitando la devolución a Mercado Pago…";
  try {
    const result = await call("/api/admin/claims/" + encodeURIComponent(claim.id) + "/refund", {});
    admin["refund-dialog"].close();
    pendingClaim = undefined;
    await refresh();
    status(result.message || (result.state === "refunded"
      ? "Mercado Pago confirmó la devolución. El plazo de acreditación depende del medio de pago."
      : "La devolución fue solicitada. Estamos esperando la confirmación de Mercado Pago."));
  } catch (error) {
    admin["refund-status"].dataset.error = "true";
    admin["refund-status"].textContent = error.message;
  } finally {
    refunding = false;
    admin["refund-confirm"].disabled = false;
    admin["refund-cancel"].disabled = false;
  }
});
admin["admin-logout"].addEventListener("click", () => {
  accessToken = "";
  window.dispatchEvent(new Event("planilla:admin-close"));
  admin["admin-access"].hidden = false;
  admin["admin-report"].hidden = true;
  admin["admin-summary"].replaceChildren();
  admin["admin-claims"].replaceChildren();
  admin["admin-status"].hidden = true;
  admin["admin-token"].focus();
});
