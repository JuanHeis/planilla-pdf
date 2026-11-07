"use strict";

// Behavioral checks for the sandbox UI; no browser, network or real credentials.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..");

class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.hidden = false; this.value = ""; this.attrs = {}; }
  addEventListener(name, handler) { this.events[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelectorAll() { return []; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() {}
  remove() {}
  focus() {}
}

function surface(file, script, extras = {}) {
  const html = fs.readFileSync(path.join(root, "static", file), "utf8");
  const nodes = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  const listeners = {};
  const window = {
    location: { origin: "https://planilla.test" },
    setTimeout: () => 1, clearTimeout() {},
    addEventListener(name, handler) { listeners[name] = handler; },
    dispatchEvent(event) { return listeners[event.type]?.(event); },
  };
  const context = vm.createContext({
    window, document: { getElementById: (id) => nodes[id], createElement: (tag) => new Element(tag), body: new Element("body"), addEventListener() {} },
    URL, Intl, Date, Number, JSON, Boolean, String, Error,
    Event: class { constructor(type) { this.type = type; } },
    CustomEvent: class { constructor(type, detail) { this.type = type; this.detail = detail?.detail; } },
    ...extras,
  });
  vm.runInContext(fs.readFileSync(path.join(root, "static", script), "utf8"), context);
  return { nodes, window, listeners, context };
}

function childrenOf(element, tag) {
  return element.children.flatMap((child) => child instanceof Element ? [...(child.tag === tag ? [child] : []), ...childrenOf(child, tag)] : []);
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

async function checkoutChecks() {
  let state = "needs_email";
  const titles = { payment_pending: "Pago pendiente", payment_rejected: "Pago no aprobado", expired: "Este trabajo venció", refunded: "Devolución confirmada" };
  const status = () => ({ state, message: "Mensaje del servidor", price: { amount: 2990 } });
  const ui = surface("index.html", "checkout.js", { fetch: async () => ({ ok: true, json: async () => status() }) });
  const pdf = { documentId: "synthetic-job", fields: [], pages: [{}, {}, {}] };
  const config = { commercial: true, testMode: true, testEmailEnabled: true, emailConfigured: false, paymentsConfigured: true };
  ui.window.PlanillaCheckout.configure(config);
  await ui.window.PlanillaCheckout.open(pdf, false);
  assert.equal(ui.nodes["download-email-form"].hidden, false, "The test sink enables the confirmation form");
  assert.equal(ui.nodes["download-test-notice"].hidden, false);
  assert.equal(ui.nodes["download-test-mailbox"].hidden, false);
  assert.equal(ui.nodes["recovery-test-mailbox"].hidden, false);
  assert.match(ui.nodes["download-detail"].textContent, /no llegará a tu casilla/);
  assert.equal(ui.nodes["download-title"].textContent, "Último paso para descargar");
  for (const next of ["email_pending", "payment_required", "payment_pending", "payment_rejected", "expired", "refunded"]) {
    state = next;
    await ui.window.PlanillaCheckout.open(pdf, false);
    assert.doesNotMatch(ui.nodes["download-detail"].textContent, /Correo no deseado|cargo en tu cuenta|acreditación depende/);
    assert.equal(ui.nodes["download-file"].hidden, true, "Pending or unsuccessful tests never offer a file");
    assert.equal(ui.nodes["download-test-mailbox"].hidden, next !== "email_pending", "The mailbox link is limited to the email step");
    if (next === "email_pending") assert.match(ui.nodes["download-status"].textContent, /Enlace de prueba generado/);
    if (next === "payment_required") assert.match(ui.nodes["download-pay"].textContent, /Probar pago/);
    if (titles[next]) assert.equal(ui.nodes["download-title"].textContent, titles[next]);
  }
  state = "payment_approved";
  await ui.window.PlanillaCheckout.open(pdf, false);
  assert.equal(ui.nodes["download-file"].hidden, false);
  assert.equal(ui.nodes["download-title"].textContent, "Tu PDF está listo para descargar");
  ui.window.PlanillaCheckout.configure({ ...config, testMode: false, testEmailEnabled: false, emailConfigured: true });
  state = "email_pending";
  await ui.window.PlanillaCheckout.open(pdf, false);
  assert.equal(ui.nodes["download-test-notice"].hidden, true);
  assert.equal(ui.nodes["download-test-mailbox"].hidden, true);
  assert.equal(ui.nodes["recovery-test-mailbox"].hidden, true);
  assert.match(ui.nodes["download-detail"].textContent, /Correo no deseado/);
  for (const [next, title] of Object.entries(titles)) {
    state = next;
    await ui.window.PlanillaCheckout.open(pdf, false);
    assert.equal(ui.nodes["download-title"].textContent, title, "The heading states the actual result in production too");
    assert.equal(ui.nodes["download-file"].hidden, true);
  }
  ui.window.PlanillaCheckout.configure({ ...config, testMode: false, testEmailEnabled: true });
  assert.equal(ui.nodes["recovery-test-mailbox"].hidden, true, "A lone test-email flag cannot expose the test navigation");
}

async function freeReadyChecks() {
  for (const testMode of [false, true]) {
    let status;
    const ui = surface("index.html", "checkout.js", { fetch: async (url) => url.endsWith("/download")
      ? { ok: true, blob: async () => new Blob(["synthetic PDF"]) }
      : { ok: true, json: async () => status } });
    const pdf = { documentId: "synthetic-free-job", fields: [], pages: [{}] };
    ui.window.PlanillaCheckout.configure({ commercial: true, testMode, testEmailEnabled: testMode, emailConfigured: true, paymentsConfigured: true });
    status = { state: "download_ready", grantKind: "free_pending", quotaConsumed: false, message: "Tu PDF está listo, sin otro cobro." };
    await ui.window.PlanillaCheckout.open(pdf, false);
    assert.match(ui.nodes["download-status"].textContent, /Todavía no se consumió/);
    assert.match(ui.nodes["download-detail"].textContent, /al iniciar la descarga/);
    assert.doesNotMatch(ui.nodes["download-detail"].textContent, /pagar|cobro/);
    await ui.nodes["download-file"].events.click();
    assert.match(ui.nodes["download-status"].textContent, /fue descargado/);
    assert.match(ui.nodes["download-status"].textContent, /ya usó tu descarga gratis/);
    assert.match(ui.nodes["download-detail"].textContent, /no consume otra conversión/);
    assert.doesNotMatch(ui.nodes["download-detail"].textContent, /Se usará/);
    status = { state: "download_ready", grantKind: "free", quotaConsumed: true, message: "Tu PDF está listo, sin otro cobro." };
    await ui.window.PlanillaCheckout.open(pdf, false);
    assert.match(ui.nodes["download-status"].textContent, /ya usó tu descarga gratis/);
    assert.match(ui.nodes["download-detail"].textContent, /no consume otra conversión/);
    assert.doesNotMatch(ui.nodes["download-status"].textContent + ui.nodes["download-detail"].textContent, /pagar|cobro|Todavía no se consumió/);
    status = { state: "download_ready", grantKind: "paid", quotaConsumed: false, message: "Tu PDF está listo, sin otro cobro." };
    await ui.window.PlanillaCheckout.open(pdf, false);
    assert.equal(ui.nodes["download-status"].textContent, status.message, "Paid backend message stays unchanged");
    assert.match(ui.nodes["download-detail"].textContent, /sin pagar otra vez/);
  }
}

async function mailboxChecks() {
  const messages = [
    { to: "qa@example.com", subject: "<img src=x onerror=alert(1)>", text: "Mensaje de prueba", created: 1, verificationUrl: "https://planilla.test/verify?token=synthetic-only" },
    { verificationUrl: "https://other.test/verify?token=synthetic-only" },
    { verificationUrl: "javascript:alert(1)" },
    { verificationUrl: "https://planilla.test/static/admin.html?token=synthetic-only" },
    { verificationUrl: "https://other@planilla.test/verify?token=synthetic-only" },
  ];
  let pendingMailbox;
  const ui = surface("admin.html", "adminextras.js", {
    accessToken: "synthetic-admin",
    call: async (url) => {
      if (url === "/api/admin/test-mailbox") return pendingMailbox || { enabled: true, retentionSeconds: 900, messages };
      if (url.startsWith("/api/admin/usage")) return { month: "2026-11", totals: {}, costs: {} };
      if (url === "/api/admin/field-learning") return { autoApply: false, stats: {} };
      throw new Error("Unexpected fake request");
    },
  });
  const open = () => ui.listeners["planilla:admin-open"]({ detail: { testMode: true, testEmailEnabled: true } });
  open();
  await flush();
  const links = childrenOf(ui.nodes["test-mailbox-list"], "a");
  assert.equal(links.length, 1, "Only a same-origin verification link can open");
  assert.equal(links[0].href, "https://planilla.test/verify?token=synthetic-only");
  assert.equal(links[0].rel, "noopener noreferrer");
  assert.equal(childrenOf(ui.nodes["test-mailbox-list"], "h3")[0].textContent, messages[0].subject, "Untrusted email content stays text");
  assert.match(ui.nodes["test-mailbox-retention"].textContent, /15 minutos/);
  let deliver;
  pendingMailbox = new Promise((resolve) => { deliver = resolve; });
  ui.nodes["test-mailbox-refresh"].events.click();
  ui.context.accessToken = "";
  ui.listeners["planilla:admin-close"]();
  deliver({ enabled: true, messages });
  await flush();
  assert.equal(ui.nodes["test-mailbox-section"].hidden, true);
  assert.equal(ui.nodes["test-mailbox-list"].children.length, 0, "A late response cannot restore links after logout");
  pendingMailbox = undefined;
  ui.context.accessToken = "another-synthetic-admin";
  open();
  await flush();
  assert.equal(ui.nodes["test-mailbox-section"].hidden, false);
  assert.equal(childrenOf(ui.nodes["test-mailbox-list"], "a").length, 1);
  ui.listeners["planilla:admin-open"]({ detail: { testMode: false, testEmailEnabled: false } });
  assert.equal(ui.nodes["test-mailbox-section"].hidden, true, "Production has no test mailbox UI");
  assert.equal(ui.nodes["test-mailbox-list"].children.length, 0);
  pendingMailbox = new Promise((resolve) => { deliver = resolve; });
  open();
  ui.listeners["planilla:admin-open"]({ detail: { testMode: false, testEmailEnabled: false } });
  deliver({ enabled: true, messages });
  await flush();
  assert.equal(ui.nodes["test-mailbox-list"].children.length, 0, "A late response cannot restore mail after leaving test mode");
}

async function adminLogoutChecks() {
  let pendingRefund;
  let pendingMetrics;
  const response = (value) => ({ ok: true, json: async () => value });
  const ui = surface("admin.html", "admin.js", { fetch: async (url) => {
    if (url === "/api/config") return response({ testMode: true, testEmailEnabled: true });
    if (url === "/api/admin/metrics") return pendingMetrics || response({});
    if (url === "/api/admin/claims") return response([{ id: "synthetic-claim", email: "qa@example.com", state: "requested", created: 1, provider_id: "ORDTST-synthetic", reason: "Synthetic review" }]);
    if (url.endsWith("/refund")) return pendingRefund;
    throw new Error("Unexpected fake request");
  } });
  const login = () => {
    ui.nodes["admin-token"].value = "synthetic-admin";
    ui.nodes["admin-login"].events.submit({ preventDefault() {} });
  };
  login();
  await flush();
  assert.equal(ui.nodes["admin-report"].hidden, false);
  assert.equal(ui.nodes["admin-test-mode"].hidden, false);
  const refund = childrenOf(ui.nodes["admin-claims"], "button")[0];
  assert.equal(refund.textContent, "Solicitar devolución de prueba");
  refund.events.click();
  assert.match(ui.nodes["refund-explanation"].textContent, /No se mueve dinero real/);
  let deliverRefund;
  pendingRefund = new Promise((resolve) => { deliverRefund = resolve; });
  ui.nodes["refund-confirm"].events.click();
  ui.nodes["admin-logout"].events.click();
  deliverRefund(response({ state: "refunded", message: "Late refund result" }));
  await flush();
  assert.equal(ui.nodes["admin-report"].hidden, true);
  assert.equal(ui.nodes["refund-dialog"].open, false);
  assert.equal(ui.nodes["refund-details"].textContent, "");
  assert.equal(ui.nodes["admin-status"].hidden, true, "A refund response after logout cannot reopen or announce private data");
  login();
  await flush();
  let deliverMetrics;
  pendingMetrics = new Promise((resolve) => { deliverMetrics = resolve; });
  ui.nodes["admin-refresh"].events.click();
  ui.nodes["admin-logout"].events.click();
  deliverMetrics(response({}));
  await flush();
  assert.equal(ui.nodes["admin-report"].hidden, true, "A late refresh cannot reopen the report");
}

(async () => {
  await checkoutChecks();
  await freeReadyChecks();
  await mailboxChecks();
  await adminLogoutChecks();
  console.log("Sandbox UI checks passed: test/prod copy, email readiness, entitlement display, scoped links and logout races.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
