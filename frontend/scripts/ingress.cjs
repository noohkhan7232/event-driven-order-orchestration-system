/* eslint-disable @typescript-eslint/no-require-imports -- Node preload must be CommonJS, including standalone startup. */
// Runs before Next in dev, next start and the generated standalone server.
// Derive identity at the Node socket boundary; never trust arbitrary browser XFF.
const http = require("node:http");
const { isIP } = require("node:net");
function normalizeIP(value) {
  const text = typeof value === "string" ? value.trim() : "";
  return text.startsWith("::ffff:") && isIP(text.slice(7)) === 4
    ? text.slice(7)
    : text;
}
function sanitize(req, env = process.env) {
  const peer = normalizeIP(req.socket.remoteAddress);
  const trusted = (env.BFF_TRUSTED_PROXY_IPS || "").split(",").map(normalizeIP);
  const incoming = String(req.headers["x-forwarded-for"] || "")
    .split(",")
    .map(normalizeIP);
  let ip = trusted.includes(peer) && incoming.length ? incoming.at(-1) : peer;
  if (
    env.BFF_ALLOW_TEST_IP === "true" &&
    ["local", "test"].includes(env.DASHBOARD_ENV) &&
    isIP(String(req.headers["x-dashboard-test-ip"] || ""))
  )
    ip = String(req.headers["x-dashboard-test-ip"]);
  delete req.headers["x-dashboard-test-ip"];
  delete req.headers.forwarded;
  delete req.headers["x-real-ip"];
  delete req.headers["x-dashboard-client-ip"];
  req.headers["x-forwarded-for"] = isIP(ip) ? ip : "unknown";
  req.headers["x-dashboard-client-ip"] = isIP(ip) ? ip : "unknown";
}
if (
  process.env.BFF_ALLOW_TEST_IP === "true" &&
  !["local", "test"].includes(process.env.DASHBOARD_ENV)
)
  throw new Error("Test IP override requires DASHBOARD_ENV=local or test");
const emit = http.Server.prototype.emit;
http.Server.prototype.emit = function (event, ...args) {
  if (event === "request") sanitize(args[0]);
  return emit.call(this, event, ...args);
};
module.exports = { sanitize };
