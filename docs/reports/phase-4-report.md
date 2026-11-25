# Phase 4 dashboard — tasks 4.1–4.4

25 November 2026, Asia/Calcutta. Branch: `phase-4-dashboard`, based on
`main` at `f8208b8`. Tasks **4.1–4.3 are complete**. Task **4.4 is implemented
except for stored suspension reasons**, which the backend does not record. The UI
states that limitation; the contract gap is explicit in A8/B6. Tasks 4.5–4.7 remain outside this session.
This is one current-state report, including foundation evidence.

# Part A — Architecture and operations

## A1. Architecture

```mermaid
flowchart LR
  Browser[Browser: forms and guarded workspace] -->|same-origin /bff requests| Next[Next App Router and BFF]
  Socket[Node HTTP socket boundary] -->|overwrite untrusted IP headers| Next
  Next -->|server-side Bearer and one visitor XFF| API[FastAPI: existing authority]
  API --> DB[(Existing tenant database)]
  Contract[docs/openapi.json] --> Generator[openapi-typescript]
  Generator --> Types[Committed TypeScript types]
  Types --> Next
```

`frontend/` is an independent npm project. It uses Next App Router, React,
strict TypeScript, Tailwind, owned shadcn-style Radix/CVA components, TanStack
Query, React Hook Form and Zod. English UI strings live in `src/lib/strings.ts`.
Formatting helpers use `en-IN` currency grouping and local dates with an IST
tooltip. Orders, catalog, warehouse stock, owner settings and platform controls are implemented.

```mermaid
sequenceDiagram
  participant B as Browser
  participant N as Next BFF
  participant A as API
  B->>N: POST /bff/auth/login with CSRF signals
  N->>A: POST /api/v1/auth/login
  A-->>N: JWT
  N-->>B: httpOnly cookie and JSON ok; no JWT in JSON
  B->>N: Guarded page or /bff/api request
  N->>A: Bearer JWT; GET auth/me
  A-->>N: Authoritative user/role
  N->>A: Scoped request, or GET tenants/me for shell
  A-->>N: Scoped response
  N-->>B: Safe shell or forwarded API status/body
```

Backend additions are read-only: `GET /api/v1/tenants/me` for store metadata,
`GET /api/v1/orders/{order_id}/activity` for a safe staff history/refund projection,
paginated `GET /api/v1/inventory/warehouses` for scoped warehouse choices,
and an optional escaped name/slug search parameter on the platform tenant list.
There are no migrations or changes to payment, refund, shipping, inventory or
order business behavior. UI mutations call the existing APIs through the BFF.

## A2. Security model

| Threat | Mitigation | Verification |
|---|---|---|
| JWT exposed to application JavaScript | BFF-only login; httpOnly cookie; no token in login JSON or browser storage | Cookie/unit tests and real auth flow |
| Stale session/role | Middleware checks presence only; server layout and protected BFF requests ask API `auth/me` | Session/role tests; API remains authoritative |
| Cross-site mutations | Custom `X-Requested-With: seller-dashboard`, exact configured Origin, and `Sec-Fetch-Site: same-origin` required | Login/register/logout/proxy rejection tests and real HTTP proof |
| Open proxy or credential confusion | Fixed `API_INTERNAL_URL`; `/api/v1/` added server-side; explicit business-prefix allow-list; browser Bearer/API-key/cookie headers are not forwarded | Path/header tests |
| Hop-by-hop propagation | Positive header allow-lists, also excluding fields named by Connection | Request and response regression |
| Large bodies or streams | Auth body 16 KiB; general request 1 MiB; response 2 MiB; SSE byte-counted while streaming | Fixed/chunked body and SSE cap tests |
| Spoofed visitor identity | Preload overwrites forwarding/test headers at the Node socket boundary; only exact configured proxy peers may supply visitor XFF | Ingress controls and real two-visitor login/tracking proof |
| Test IP override left enabled | Off by default; requires explicit local/test environment; rejected at startup outside those environments | Ingress tests and final runtime inspection |
| Script injection/clickjacking | Per-request CSP nonce, self-only connections, no unsafe-inline script permission, no HTML injection, frame-ancestors none | Real nonce/header test; escaped-toast test |
| Cache data crossing login/logout | Successful auth changes clear the shared query cache; API responses/pages use no-store | Cache isolation regression |
| Generated integration secret persists after disclosure | Plain async writes, safe read projections, component-only disclosure, saved gate, hide/unmount and late-response protection | Cache/storage/lifetime tests and real show-once flows |
| Owner/platform action shown to wrong role | Server page gates, BFF guards and existing API authority | Owner/platform route matrix,24 BFF cases and real vendor/platform proof |
| Secret or tracking-capability URLs in application logs | No credential logging; incoming Next request URL logging disabled; auth traces disabled | Source/configuration review; synthetic test output only |

Cookie: `oos_session`, path `/`, httpOnly, SameSite Lax, maximum age 900 seconds.
Secure is enabled except for the explicit localhost/loopback development hosts.
The API can reject a JWT earlier; 401 clears the cookie and the client navigates
to login. Server-rendered expiry uses `/bff/auth/expired` to clear it before the
login redirect. Logout clears the browser cookie; it does not add backend JWT
revocation behavior. The backend's normal JWT expiry remains authoritative.
The expiry GET rechecks the API: it preserves a valid/refreshed session rather
than offering an unprotected GET logout. Confirmed invalid or absent sessions clear.

Production CSP is generated in `src/proxy.ts` because its nonce must be fresh
and forwarded into rendering. Static security headers are set in `next.config.ts`:
nosniff, DENY, no-referrer and disabled camera/microphone/geolocation. Development
allows unsafe-eval for Next tooling; production does not. Styles use the same
nonce; Radix's stylesheet helper receives it through `get-nonce`. Toasts use CSS
classes without runtime stylesheet injection. No `dangerouslySetInnerHTML` is used.

**Trust boundary:** `scripts/ingress.cjs` must be preloaded for every Node HTTP
server. The dev/start scripts and Docker entry point do this. The default uses
the socket peer, normalizing IPv4-mapped IPv6. An exact `BFF_TRUSTED_PROXY_IPS`
peer may contribute the rightmost XFF address; configure that private ingress to
overwrite visitor headers. All untrusted forwarding headers are replaced before
Next sees the request. Missing/invalid ingress identity fails BFF requests closed.
API Compose trust is one hop and its host port is loopback-bound. Do not expose
that API directly to untrusted callers with `TRUSTED_PROXY_COUNT=1`.

The test-only `X-Dashboard-Test-IP` simulates distinct visitors behind local Docker
NAT. It is honored only with both `BFF_ALLOW_TEST_IP=true` and
`DASHBOARD_ENV=local|test`, and is never forwarded to the API. It is not a production
identity mechanism. Deployed reverse proxies/CDNs and direct HTTPS Node servers
have not been verified; serverless launchers that bypass the preload are unsupported.


Owner settings use the existing BFF authority and API mutations. Query functions
project safe metadata through allow-listed Zod schemas before caching. Delivery
payloads and unknown fields are dropped. API-key and webhook create/rotate replies
are handled by plain async calls, never TanStack mutation/query caches. The only
secret copy lives in component memory and its dialog: acknowledgement gates Done,
and navigation/unmount, pagehide, hidden-tab and session-change events forget it.
Generation tickets reject responses arriving after leaving and returning. Copy
uses the Clipboard API with generic success/error feedback. No secret is put into
storage, URLs, server-rendered props or logs. Credential fields are password inputs,
blank on render; save/failure/navigation clears them. Switching tabs while filling
unfinished Razorpay fields preserves input so credentials can be copied from the
provider dashboard. Replacing credentials requires all three fresh values.

Endpoint and delivery snapshots poll every 15s; server mutations invalidate scoped
queries. API key expiry labels refresh every 30s. Failed jobs poll every 15s. The
server performs DNS/SSRF checks; browser hints do not claim to replace that boundary.
The delivery table becomes labeled rows/cards below 1024px. Replay confirmation
explains technical-only reopening and the existing refund/state/duplicate conflicts.

Verification snippets reproduce backend HMAC-SHA256 over timestamp + dot + exact
raw body, using the full signing secret, `X-OOS-Signature: t=...,v1=...` and matching
`X-OOS-Timestamp`. They reject malformed headers and timestamps outside a receiver
policy of ±300s, and compare in constant time. Only parse JSON after success and
durably deduplicate its signed envelope ID. Delivery/event HTTP header values are
not authenticated by that MAC and must not be used alone as replay authority.

## A3. Screen and route inventory

| Route | Access | Backing API | States/actions |
|---|---|---|---|
|`/login`, `/tenants/register`|Public|BFF auth/login and tenants/register|Validated forms, optional invite, closed registration, quota countdown|
|`/dashboard`|Tenant admin/vendor|auth/me, tenants/me|Guarded workspace/store identity|
|`/orders`|Tenant admin/vendor|orders GET, orders/stream|Server pagination, status/local-date/exact-reference filters; direct UUID lookup; Live/polling, loading/empty/errors|
|`/orders/new`|Tenant admin/vendor|orders POST|SKU/quantity lines, optional buyer/reference, prepaid/COD; retry-stable idempotency key; creates a real store order|
|`/orders/[id]`|Tenant admin/vendor|orders/{id}, activity, stream|Buyer/items, payment expiry/provider status/refunds, saga and shipment observations; confirm cancel/tracking/events/rotation; copy link|
|`/catalog`|Tenant admin/vendor|products GET/POST/PATCH|Prefix search, active filter, pagination, create, confirmed archive/unarchive|
|`/catalog/[id]`|Tenant admin/vendor|products/{id} GET/PATCH|Name/description/price/active; immutable SKU and stored currency; clean missing-record error|
|`/inventory`|Tenant admin/vendor|inventory GET/POST/adjustments; warehouses GET/POST|Paged stock and warehouses, low-stock warning, required adjustment reason, local/backend negative-stock checks, inline duplicate errors|
|`/payments`|Tenant owner only|settings/payments/razorpay GET/PUT/DELETE|Write-only replacement credentials, masked ID/status, inbound URL/copy/setup guide, disconnect, missing/config errors|
|`/api-keys`|Tenant owner only|api-keys GET/POST/DELETE|Scoped create/optional expiry, show-once saved gate, metadata list, confirmed revoke|
|`/webhooks`|Tenant owner only|webhook-endpoints CRUD/rotate-secret/test/deliveries/replay|Event choices, URL guidance/server SSRF errors, show-once secret, ping, delivery table/filter/paging, replay, disabled/re-enable, copyable Python/Node verification|
|`/notifications`|Tenant owner only|settings/notifications GET/PATCH|Persisted notification opt-in toggle|
|`/failed-jobs`, `/failed-jobs/[id]`|Tenant owner only|failed-jobs GET/detail/replay/discard|Paged status/task filters, safe detail, confirmed recovery, explicit 409 explanations|
|`/platform`|Platform admin only|admin/tenants GET?q&status; suspend/activate POST|Search/status/paging, confirmed state change; unavailable suspension reason explained|
|`/access-denied`, `/suspended`, `/rate-limited`|Explanatory|Session/error outcomes|Customer denial, paused store, bounded retry countdown|
|`/bff/auth/logout`, `/bff/auth/expired`|Session boundary|Cookie/API recheck|Same-origin POST logout; expiry GET preserves a valid/refreshed session|
|`/bff/api/[...path]`|Existing staff/owner/platform policy; public tracking reads exempt|Allow-listed API|Status/quota forwarding, CSRF and role guards, bounded bodies and SSE|
|`/health`|Public operational probe|Frontend process|Does not assert API readiness|

The shell enables every implemented workspace section appropriate to the role. Vendor accounts cannot proxy owner settings, keys, webhooks or failed
jobs; customer accounts cannot enter the seller workspace. Platform-only users
are redirected away from tenant screens. API tenant isolation remains authoritative.

Stock is a desktop semantic table and becomes labeled rows below 1024px. Product
and order lists use responsive cards. Warehouse choices follow the visible warehouse
page; change that page before opening Add inventory to select another group.

## A4. Live updates

```mermaid
flowchart LR
  Client[Fetch SSE hook] -->|Last-Event-ID| BFF[Bounded streaming BFF]
  BFF -->|server Bearer and visitor IP| API[API order timeline stream]
  API --> BFF --> Decoder[Frame decoder and dedupe]
  Decoder --> Invalidate[Tenant-scoped query invalidation]
  Invalidate --> Snapshot[Authoritative order/activity GET]
  Poll[15 second polling backstop] --> Snapshot
```

`useOrderStream` uses fetch, never a JavaScript bearer token. It handles fragmented
LF/CRLF frames and heartbeats, keeps 64-bit cursors as strings/BigInt, deduplicates
4096 recent IDs, and accepts late lower-ID observations. Events invalidate scoped
snapshots rather than writing event payloads over newer order state. Invalidation
keys begin with tenant identity. Initial reads and the activity projection establish
state/history when a stream starts without an old cursor.

Disconnects reconnect after 1s with exponential backoff capped at 30s, resuming with
Last-Event-ID. Partial frames are discarded between responses; malformed frames
never enter the cache. Frames are bounded at 64KiB. The BFF still caps each stream
at 2MiB and awaits upstream cancellation; the hook resumes after closure. Cleanup
aborts the request and cancels/releases the reader. Cursors are component-memory
only, with no local/session storage. HTTP401/403 lead to the session screens.

Live means an accepted open event-stream response. A reconnecting label explains
the 15s polling fallback; polling also remains a backstop while connected. Long-lived
half-open connections, reconnect storms and many-tab load are not soak-tested.
The BFF headers deadline remains20s, not a total SSE/body deadline.

The activity API returns actual order transitions and shipment observations, plus
latest persisted payment and refund statuses/timestamps. It does not manufacture
historical provider transitions that the backend has no event table for. Raw audit
payloads, checkout payloads, refund errors and free-form event diagnostics are excluded.

## A5. Configuration and builds

All application configuration below is server-only. No NEXT_PUBLIC variable is
needed and upstream credentials must never be added to a client-exposed variable.

| Variable | Default/meaning |
|---|---|
| API_INTERNAL_URL | `http://api:8000`; origin only, no credentials/path; host development example uses 127.0.0.1:18000 |
| APP_ORIGIN | `http://localhost:3000`; exact browser origin for CSRF, redirects and cookie policy |
| FRONTEND_PORT | Compose host 3000, fixed 127.0.0.1 bind; override with matching APP_ORIGIN |
| DASHBOARD_ENV | Compose local; set production for a deployed environment |
| BFF_ALLOW_TEST_IP | false; local/test fixture switch only |
| BFF_TRUSTED_PROXY_IPS | Empty; comma-separated exact trusted socket peer addresses |
| E2E_FRONTEND_URL | Test runner localhost:3000; match the actual browser origin |
| API_PORT | Existing host override, used as 18000 on this Windows machine |
| PUBLIC_BASE_URL | Existing backend default localhost:8000; configure the publicly reachable HTTPS API origin for Razorpay inbound webhook URLs. The UI preserves the API-provided URL |
| PUBLIC_TRACKING_BASE_URL | Existing backend setting; match the reachable API host/port for copied API links until the 4.5 buyer UI exists |
| TRUSTED_PROXY_COUNT | API Compose environment 1 for the BFF hop |
| NEXT_TELEMETRY_DISABLED | 1 in Docker; use 1 for local/CI verification |
| CREDENTIALS_ENCRYPTION_KEY | Existing backend-only Fernet key ring, required for owner provider/webhook writes; never a frontend variable |
| E2E_API_URL / E2E_SINK_URL | Operations test loopback defaults 127.0.0.1:18000 /127.0.0.1:18090 |
| WEBHOOK_SINK_PORT | Existing dev-tools override; operations proof uses 18090 |
| WEBHOOK_ALLOW_INSECURE_TARGETS / WEBHOOK_EXTRA_ALLOWED_PORTS | Backend defaults false/[]; scoped local proof overridestrue/[8080], restored afterward |

The multi-stage Dockerfile uses exact Node 22.23.5, npm ci, standalone output,
copied static assets, a non-root node user and a healthcheck. Dev/start and Docker
use the same socket sanitizer. `npm start` launches the generated standalone server
after copying its static assets, not a custom Next rendering server.

Builds use supported webpack mode, one Next build worker and a 1 GiB Docker builder
heap to fit the observed 8 GB Windows/Docker host. These affect build resources,
not API business behavior. API reload watches only `app/`, avoiding frontend files.
Generated API types are committed with LF line endings; Prettier accepts platform
line endings elsewhere. Existing MP4 LFS attributes are preserved.

The frontend CI job selects Node 22.23.5 and performs npm ci, API-type freshness,
lint, strict typecheck, unit tests and production build. The existing backend jobs
remain unchanged. ESLint uses current core/TypeScript/Next/React Hooks flat rules;
it does not depend on the legacy peer-constrained Next preset. Exact dependency
versions and integrity hashes are in package.json/package-lock.json.
Typecheck runs Next typegen before tsc, so a clean checkout does not need a
previous build's next-env declarations. Those generated Next files are ignored;
the separate OpenAPI types remain committed and freshness-checked.

Reference behavior was checked against the official
[Next nonce guide](https://nextjs.org/docs/app/guides/content-security-policy),
[standalone output guide](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)
and npm registry metadata. The current linter choice avoids the EOL line shown in
[ESLint's support table](https://eslint.org/version-support/).

## A6. Accessibility and responsive approach

Forms have explicit labels, autocomplete hints, linked validation errors and live
error/countdown notices. Buttons have useful touch targets and visible keyboard
focus. The app has a skip link, mobile Radix drawer with focus management and
Escape dismissal, desktop sidebar, semantic user menu and loading/empty/error states.
Long user/store names are constrained to avoid horizontal overflow. Reduced-motion
preferences disable transitions/animations. Auxiliary text and input borders were
darkened after the real contrast regression; no axe assertion was relaxed.

Labels remain stable when validation errors appear; errors are linked by aria-describedby.
Ordinary dialogs use Radix focus management and Escape dismissal; show-once dialogs
require saved acknowledgement before dismissal (leaving/hiding still clears secrets).
Cancellations roll back
optimistic state after failure and always refetch authoritative snapshots.

Actual browser coverage and counts are in Part B. Comprehensive screen/device,
keyboard and contrast verification beyond the selected checks remains 4.6 work.

## A7. Runbook

From the repository root, with Docker Desktop running:

```powershell
$env:API_PORT='18000'
$env:FRONTEND_PORT='3000'
$env:APP_ORIGIN='http://localhost:3000'
$env:DASHBOARD_ENV='local'
$env:BFF_ALLOW_TEST_IP='false'
docker compose up --build -d
docker compose ps -a
# Open http://localhost:3000
```

If Windows reserves a port, change its supported override and APP_ORIGIN together.
Port 13000 became unavailable after a Docker restart during verification; socket
errors are not application assertion passes. Do not widen loopback binds to work
around a Windows reservation. If the app rejects CSRF, check the actual browser
origin and APP_ORIGIN. If BFF calls fail closed, use the npm scripts or documented
Docker entry, not an un-preloaded Next command. Check API reachability separately
from the frontend healthcheck. Inspect trusted peer configuration after ingress changes.

For host development, select Node 22.23.5, run npm ci in frontend, and create
`.env.local` from its example only if that private file does not already exist.
Point API_INTERNAL_URL at the running host API. Use npm run dev, or npm run build
then npm start. Never overwrite an existing private environment file.

```powershell
cd frontend
npm ci
npm run gen:api
npm run gen:api:check
npm run lint
npm run typecheck
npm run test
npm run build
```

For the local IP proof, enable BFF_ALLOW_TEST_IP only for the frontend in local mode,
recreate it, set E2E_FRONTEND_URL to APP_ORIGIN and run npm run e2e. Use a finally
block to restore the flag to false and recreate the frontend even after a failure.
Tests create synthetic stores and use random disposable passwords; they do not use
real buyers/provider credentials. Auth traces are disabled. Synthetic store history
is retained rather than deleting audit/business data.

For the operations browser proof, `frontend/e2e/operations.spec.ts` expects the
local real API, frontend and `webhook-sink` dev-tools service. It seeds only a new
`operations-*` tenant and scoped synthetic users/jobs using its Python fixture;
finally it suspends/deactivates those fixtures and credentials/endpoints. It never
deletes audit history. It refuses non-loopback test service origins and disables
screenshots/traces so failed secret-dialog steps cannot persist generated keys.
The payment setup URL comes from backend PUBLIC_BASE_URL; localhost defaults are
for local tests, not a provider-reachable deployment. Configure its public HTTPS
origin before copying it into Razorpay.
No actual Razorpay request is made: dummy configuration is disconnected before
placing orders. Sink requests are local-only HTTP using the explicit backend test flag.

A disposable local override must supply an in-memory Fernet key (reuse an existing
configured key if present), `WEBHOOK_ALLOW_INSECURE_TARGETS=true`,
`WEBHOOK_EXTRA_ALLOWED_PORTS=[8080]`, and optionally interval 1 to api/worker/
integrations-worker/scheduler/outbox-relay. Use `${TASK44_CREDENTIAL_KEY}` in the
ignored override, never its literal value. Set WEBHOOK_SINK_PORT 18090 and the local
BFF test-IP flag, start the dev-tools sink, wait for API `/api/v1/system/health`
and frontend `/health`, then run `npm --prefix frontend run e2e` with Node 22.
A finally block must restore the original encryption configuration and backend
insecure-target/port/interval defaults, BFF flag false, all core workers, and stop
the optional sink. The proof script's fixture guard is not a substitute for using
a disposable local environment. Its generated key must not replace a deployment's
persistent encryption key. `credentials_unavailable` means fix backend key storage;
`payment_not_configured` means connect this store's provider. Never paste secrets
into logs or issue reports. Lost API keys must be revoked/recreated; lost webhook
secrets must be rotated and the receiver updated.

## A8. Deferred work and limits

Tasks 4.5 buyer tracking UI,
4.6 comprehensive all-screen quality and 4.7 full proof/screenshots/README remain
outside scope. No checkout JS, billing, team management, password reset, marketing,
dark mode, analytics or broader localization was added.

Suspension reasons are not recorded by the existing API, so 4.4 cannot display a
saved reason without an out-of-scope backend behavior change. The platform screen
explicitly says it is unavailable. All other selected settings/operations flows
are implemented and verified.

Stored product currency cannot be patched by the backend. New products start active;
the edit toggle and archive/unarchive actions change that status afterward. Search
by UUID uses the existing single-order endpoint; list date/status/reference filters
apply to paginated reference searches. Product search follows the API's prefix
matching. These are contract limits, not new backend behavior.

Copied tracking links currently address the existing public API projection. The
buyer-facing /track UI belongs to4.5. An API_PORT override also needs a matching
backend PUBLIC_TRACKING_BASE_URL in the deployment override; copying preserves the
API-provided URL. The browser proof does not follow that link (F5 is deferred).
Payment/refund history limitations are in A4.
The activity response has no pagination and remains subject to the BFF2MiB cap.
Warehouse choices are paginated rather than silently assuming a tenant has <=20.

Mutation authority stays in the API. Stock reduction is checked locally and again
by the existing locked backend adjustment. A transport-failed unchanged order
submission reuses its idempotency key; changed details get a fresh key. Native order
form fallback is POST, so buyer fields are not placed in a URL if JavaScript stops.

Production ingress/TLS, serverless launchers that bypass the socket preload,
multiple frontend replicas, cross-browser/assistive-technology matrices and soak
performance remain unverified. Successful auth requires JavaScript for CSRF headers;
its native POST fallback remains safely rejected. No live external provider is tested.

# Part B — Verification

## B1. Summary

Tasks 4.1–4.3 are complete; 4.4 has the explicit suspension-reason API gap
in A8/B6. All other selected 4.4 functionality is implemented. This session
implemented only 4.4: owner settings, API keys, outbound webhooks, notifications,
failed jobs and platform administration. Nothing was pushed or merged. The
report-only commit follows the implementation commits in B7.

Before edits, the report and git history were read and fresh baseline gates passed:
62 frontend tests/15 files, production image build, 617 SQLite passes/45 skips.

Frontend lint/format, strict typecheck, **131 tests/21 files**, production Docker
build and API-type freshness pass. Backend ruff/format/mypy/OpenAPI pass; SQLite
**621 passed/45 skipped**; PostgreSQL+Redis **665 passed/1 skipped**. Production
coverage is **97.24%**, all 18 required modules >=90%. All 6 real Docker browser
tests pass, with zero serious/critical axe violations on the checked pages. JavaScript 435,761 gzip bytes; CSS 5,203
(all-chunk totals). Runtime defaults and all core workers are restored.

## B2. Task table

| Task | Status | Evidence |
|---|---|---|
|4.1 foundation/BFF/client/layout/IP/Compose/CI|DONE|69883dc,4e3cb76, a52fbe1, b534437,3134310; foundation controls rerun|
|4.2 orders|DONE|ff393c4; filters/detail/actions/test-order/SSE;52 tests at its commit;613 backend passes then|
|4.3 catalog/inventory/warehouses|DONE|Implementation commits in B7; current suites rerun; F2–F4/F10|
|4.4 settings/integrations/operations|PARTIAL (API gap)|11f601b,4cce391; all owner/platform flows implemented/tested; only saved suspension reasons unavailable because the API does not record them|
|4.5 buyer tracking UI|SKIPPED|Existing public API only|
|4.6 comprehensive quality|SKIPPED|Selected page checks do not complete all-screen work|
|4.7 full e2e/screenshots/README|SKIPPED|Selected experiments and this cumulative report only|

## B3. Test evidence

Frontend: **131 tests across 21 files**, no skips. Foundation tests retain cookie,
CSRF, allow-list/traversal, hop-by-hop headers, bounded/chunked bodies, SSE cap and
cancellation, expiry/role/quota handling, session-cache clearing and escaped toasts.
New tests cover server order filters/pagination, bigint SSE cursors/fragmentation/
replay/reconnect/resume/unmount, major order states and known failure explanations,
confirmation/rollback, tracking/events/rotation, retry-stable idempotency keys,
product decimals/immutability/archive, warehouse/inventory forms and duplicate errors,
required stock reasons, local negative/overflow guards and backend negative_stock
handling. A calendar-date regression verifies that estimated-delivery dates localize
without shifting their day across timezones.

Task 4.4 adds 69 frontend cases across operations schemas/UI, secret lifetime,
owner/platform page guards, BFF role matrices and executable Node verification.
Tests assert no generated secret enters either query or mutation caches, localStorage
or sessionStorage; acknowledgement gates Done; copy feedback works; hiding/unmount
and late responses clear or reject disclosure. Form tests cover
scopes/expiry, cleared password inputs, SSRF error mapping, auto-disable/re-enable,
notification changes, platform actions and three recovery conflicts. 422 errors map
to the safe validation message without showing raw server detail. Both copyable
Python and Node samples pass 8 cases each against actual backend `signed_request`
output (Unicode raw body, tampering, ±300/301s, malformed/mismatched headers).

The platform search adds 4 parameterized role cases; they retain anonymous401,
non-platform403, q/status/pagination/literal wildcard semantics and exact safe output.
No existing backend/frontend tests were changed during4.4.

Backend adds three activity projection cases and four warehouse matrix cases.
These check tenant scope, staff/customer/key behavior, anonymous rejection, exact
safe fields, pagination/bounds and absence of seeded diagnostics/secrets. Existing
foreign-resource and route-registry assertions remain strict.

TDD/control evidence:

- Missing activity endpoint: three failures 404; after projection/isolation work 10 passed.
- Missing warehouse-list route: four failures 422 (dynamic UUID route); final matrix passes.
- New order/catalog modules: tests failed to resolve before implementation; final tests pass.
- Validation text changed a field's accessible label: a new stock test failed to find
  Quantity change after an error; fixed the shared component, preserving the assertion.
- An optimistic cancellation conflict restores the pre-action order and keeps confirmation open.

Real browser axe reports zero serious/critical violations on login, registration,
shell, catalog list/detail, inventory, orders list/detail, payments, API keys,
webhooks with deliveries, notifications, failed-job detail and platform tenant search. The same relevant pages
pass horizontal-overflow checks at 360/768/1280. Foundation drawer Escape/logout
checks remain passing. This is not an all-device/keyboard/assistive-technology audit.
Frontend coverage percentage is not measured. Fresh backend production coverage is recorded in B4.
Unit fetch is disabled unless mocked; browser tests call only the local Docker stack.

## B4. Raw gates and build inventory

The final Node 22.23.5 Alpine Docker builder ran lint, typecheck, all frontend tests
and the production build. API-type freshness
runs on Windows against repository OpenAPI outside the frontend build context.

```text
All matched files use Prettier code style!
Types generated successfully
Test Files  21 passed (21)
Tests  131 passed (131)
Compiled successfully; production frontend image Built
API types are fresh
All checks passed!
223 files already formatted
Success: no issues found in 179 source files
OpenAPI export is fresh
SQLite: 621 passed, 45 skipped in 522.44s
PostgreSQL+Redis: 665 passed, 1 skipped in 1232.87s (0:20:32)
TOTAL production: 97.24% (required >=90%)
PASS app/core/integration_guard.py: 100.00%
PASS app/providers/manual.py: 100.00%
PASS app/services/shipping.py: 100.00%
PASS app/api/v1/endpoints/shipments.py: 93.85%
PASS app/core/ssrf.py: 96.30%
PASS app/services/outbound_webhooks.py: 92.16%
PASS app/services/webhook_delivery.py: 97.78%
PASS app/api/v1/endpoints/webhook_endpoints.py: 100.00%
PASS app/schemas/webhooks.py: 100.00%
PASS app/notifications/channels.py: 93.51%
PASS app/notifications/service.py: 91.74%
PASS app/api/v1/endpoints/notification_settings.py: 96.30%
PASS app/services/tracking.py: 97.73%
PASS app/api/v1/endpoints/public_tracking.py: 100.00%
PASS app/services/sse.py: 100.00%
PASS app/api/v1/endpoints/streams.py: 100.00%
PASS app/schemas/failed_jobs.py: 100.00%
PASS app/api/v1/endpoints/failed_jobs.py: 100.00%
```

Next 16 webpack emits a route inventory rather than a per-route KB table:

```text
/ /_not-found /access-denied /bff/api/[...path]
/bff/auth/expired /bff/auth/login /bff/auth/logout /bff/auth/register
/catalog /catalog/[id] /dashboard /health /inventory /login
/orders /orders/[id] /orders/new /platform /rate-limited /suspended
/tenants/register /payments /api-keys /webhooks /notifications
/failed-jobs /failed-jobs/[id]
All app routes dynamic; Proxy middleware
```

| Client artifact | Files | Raw bytes | Sum of per-file gzip bytes |
|---|---:|---:|---:|
|JavaScript chunks|59|1,357,728|435,761|
|CSS|1|20,778|5,203|

Measured in the final production container, Node v22.23.5, UID1000.
These are all-chunk artifact totals, not first-load/wire performance. Five known
server-only markers are absent: API_INTERNAL_URL, BFF_ALLOW_TEST_IP,
BFF_TRUSTED_PROXY_IPS, CREDENTIALS_ENCRYPTION_KEY, oos_session. No production credential
is supplied to the frontend build.

Dependencies/lockfile are unchanged from 4.1: exact Next 16.4.6, React19.3.1,
TypeScript 5.9.3, ESLint 10.15.0, Vitest 5.1.1, Playwright 1.65.1, TanStack Query5.108.4,
RHF 7.92.0, Zod 4.6.7 and Tailwind 4.3.5. The24 November foundation clean install had
341 added/348 audited, zero vulnerabilities and npm ls --all exit0; that registry
audit was not rerun for this dependency-unchanged session. Foundation Windows
start/dev socket-preload smoke, negative/positive API freshness and production
refusal of the test-IP flag remain established4.1 evidence.

## B5. Real Docker browser proof and final runtime

Real local Docker frontend + API + broker/workers/PostgreSQL/Redis + webhook-sink;
Node 22 Playwright Chromium. No browser network mocks or real external providers.
All six tests pass on the final image, including the unchanged foundation and
catalog/order scenarios. Selected 4.4 flows use dummy write-only provider values
and scoped local technical-failure fixtures.

```text
PASS F2 warehouse/product/inventory UI; adjustment +5; negative result rejected; axe/responsive catalog/detail/inventory
PASS F3 order appears and reaches fulfillment on already-open Live list without manual refresh; recorded saga timeline
PASS F4 COD tracking -> shipped -> delivered; payment Collected
PASS F10 foreign order/product clean not-found; tenant lists isolated
PASS native no-JavaScript auth uses POST; CSRF rejection; no credentials in URL/response
PASS F1 foundation; a11y login/register/shell zero serious/critical; widths360/768/1280; httpOnly session; logout guarded
PASS F11: login and public tracking independently rate-limited per visitor THROUGH BFF; spoofed XFF stripped; visible countdown
PASS BFF CSP nonce/header/CSRF/path guards and retry screen on real server
PASS F8 write-only dummy Razorpay settings; masked readback; secrets cleared; inbound URL; disconnect; no provider call
PASS F6 show-once API key; saved gate; real scoped order call; revoke ->401; no browser storage
PASS F7 endpoint create/rotate; show-once secrets; local signed delivery; failure503; replay ->delivered; safe order event; remove
PASS F9 technical fixture replay ->fulfillment; duplicate409; discard; safe job detail
PASS F12 vendor pages+BFF denied; platform search/suspend/activate; owner suspended screen; axe/responsive settings/operations
6 passed (3.0m)
```

| Experiment | Result |
|---|---|
|F1|PASS registration/login/store shell/logout, guard, foundation axe/responsiveness|
|F2|PASS warehouse/product/inventory creation, +5 adjustment, negative rejection|
|F3|PASS prepaid order appears/progresses to fulfillment on an already-open Live list without manual refresh; persisted timeline|
|F4|PASS COD tracking edit, shipped, delivered, payment Collected|
|F5|NOT RUN buyer UI/link rotation end-to-end; staff rotation has unit proof|
|F6|PASS show-once API key/saved gate; real scoped order create; revoke401; no browser storage|
|F7|PASS endpoint create/rotate/remove, local signed delivery, HTTP503 observed, UI replay delivered, safe order event. Scheduled backoff browser proof not claimed|
|F8|PASS dummy Razorpay write-only settings, masked ID, cleared inputs, inbound URL/disconnect; no provider call|
|F9|PARTIAL full experiment: seeded technical failure replays to fulfillment, duplicate409, discard and safe detail pass; injected exhaustion/second-tenant browser case deferred|
|F10|PASS foreign order/product clean missing-record responses; both lists isolated|
|F11|PASS independent login/tracking visitors, spoof stripping, visible countdown|
|F12|PASS vendor owner-page404/BFF403, platform search, suspend/activate confirmation, owner suspended screen/recovery|

F11 uses invalid public tracking tokens and checks uniform404 before429, proving
API/BFF visitor limiting rather than buyer-page rendering. Synthetic visitor headers
are honored only with the local/test server flag; final default is false. Fixture
runs use distinct visitor identities so persistent registration quotas do not couple
independent experiments. No rate limits or assertions were disabled.

Final runtime inspection after restoring the original settings and unpausing workers:

```text
API trusted_proxy_count=1 insecure_webhook_targets=False extra_ports=[] delivery_interval=10.0 encryption_key_configured=False
v22.23.5 uid=1000 test_ip=false environment=local
API health HTTP 200
Frontend health HTTP 200
api running Up 22 minutes
frontend running Up 22 minutes (healthy)
integrations-worker running Up 22 minutes
mailpit exited Exited (255) 22 hours ago
migrate exited Exited (0) 22 minutes ago
outbox-relay running Up 22 minutes
postgres running Up 6 hours (healthy)
rabbitmq running Up 6 hours (healthy)
redis running Up 6 hours
scheduler running Up 22 minutes
webhook-sink exited Exited (137) 22 minutes ago
worker running Up 22 minutes
```

The local webhook-sink dev-tools service was exercised for signed delivery/failure/
replay and stopped afterward. Mailpit and real email were not exercised this session.
No live Razorpay/provider or external HTTP receiver was called.

## B6. Backend additions and gaps

| Read-only addition | Reason | Authority/projection |
|---|---|---|
|GET tenants/me (4.1)|Store name/metadata in shell|Active staff JWT; existing TenantRead; no selector/secrets|
|GET orders/{id}/activity (4.2)|Existing OrderRead lacked timeline, shipment observations and refunds|Existing OrderService scope/ownership, staff or scoped integration; explicit status/time/amount fields; no diagnostics/payloads|
|GET inventory/warehouses (4.3)|Creation existed but no list/choices|inventory:read, tenant filter, deterministic code/ID ordering, limit1–100/offset, explicit id/code/name/region|
|GET admin/tenants?q (4.4)|Existing platform list had no search|Existing platform-admin guard; escaped case-insensitive substring name/slug; status AND search; unchanged safe TenantRead/paging|

All four are documented in OpenAPI/generated types and tested for isolation and
safe output. No database migration, payment/shipping worker change or backend
business mutation was introduced.

Contract gaps/limits: product PATCH excludes currency/SKU; create defaults active.
Order list has no ID query, so exact UUID search uses the existing detail GET;
other list filters apply to reference queries. Payment/refund snapshots are not a
complete historical provider-event journal. Activity history has no pagination.
These limits are surfaced/documented rather than changing backend behavior.

Suspension reason is not present in TenantRead, accepted by suspend, or stored in
its audit payload. The UI displays “This API does not record a suspension reason.”
It does not invent a reason or add a backend write feature. Webhook list/create/
delivery responses lack complete OpenAPI response schemas; the UI has small safe
runtime projections based on the actual existing API, rather than caching unknown
payloads. No credentials are returned by the added tenant search.

Razorpay setup names were checked on 25 November 2026 against official documentation:
[dashboard webhooks](https://razorpay.com/docs/payments/dashboard/account-settings/webhooks/),
[official documentation source](https://raw.githubusercontent.com/razorpay/markdown-docs/master/payments/dashboard/account-settings/webhooks.md),
and [refund subscriptions](https://github.com/razorpay/markdown-docs/blob/master/payments/refunds/subscribe-to-webhooks.md).
Accounts & Settings → Webhooks → Add New Webhook, URL/Secret/Active Events, and
`payment.captured`, `payment.failed`, `refund.processed`, `refund.failed` match the
existing adapter. The guide uses those names and links the official instructions.
No real Razorpay configuration or provider call was performed.

## B7. Source diff and commits

Current implementation diff and log (`git diff --stat main...HEAD` and
`git log --oneline main..HEAD`) before this standalone report-only commit:

```text
.dockerignore                                     |    1 +
 .env.example                                      |    7 +
 .gitattributes                                    |    3 +
 .github/workflows/ci.yml                          |   21 +
 README.md                                         |   52 +
 app/api/v1/endpoints/admin.py                     |    6 +-
 app/api/v1/endpoints/inventory.py                 |   29 +-
 app/api/v1/endpoints/orders.py                    |   50 +-
 app/api/v1/endpoints/tenancy.py                   |   15 +-
 app/schemas/inventory.py                          |    9 +
 app/schemas/orders.py                             |   26 +
 app/tests/test_dashboard_activity.py              |   71 +
 app/tests/test_dashboard_tenant.py                |   79 +
 app/tests/test_dashboard_tenant_search.py         |   38 +
 app/tests/test_dashboard_warehouses.py            |   48 +
 app/tests/test_tenant_isolation.py                |    4 +
 docker-compose.yml                                |   21 +-
 docs/openapi.json                                 |  470 ++
 docs/reports/phase-4-report.md                    |  748 +++
 frontend/.dockerignore                            |    8 +
 frontend/.env.example                             |    7 +
 frontend/.gitignore                               |    8 +
 frontend/.npmrc                                   |    1 +
 frontend/.nvmrc                                   |    1 +
 frontend/.prettierignore                          |    7 +
 frontend/.prettierrc.json                         |    1 +
 frontend/AGENTS.md                                |    9 +
 frontend/Dockerfile                               |   20 +
 frontend/components.json                          |   19 +
 frontend/e2e/catalog-orders.spec.ts               |  213 +
 frontend/e2e/fixtures/operations.py               |  104 +
 frontend/e2e/foundation.spec.ts                   |  210 +
 frontend/e2e/operations.spec.ts                   |  527 ++
 frontend/eslint.config.mjs                        |   29 +
 frontend/next.config.ts                           |   26 +
 frontend/package-lock.json                        | 6458 +++++++++++++++++++++
 frontend/package.json                             |   64 +
 frontend/playwright.config.ts                     |   21 +
 frontend/postcss.config.mjs                       |    2 +
 frontend/scripts/generate-api.mjs                 |   15 +
 frontend/scripts/ingress.cjs                      |   42 +
 frontend/scripts/start.mjs                        |   26 +
 frontend/src/app/(app)/api-keys/page.tsx          |    6 +
 frontend/src/app/(app)/catalog/[id]/page.tsx      |   12 +
 frontend/src/app/(app)/catalog/page.tsx           |    8 +
 frontend/src/app/(app)/dashboard/page.tsx         |   18 +
 frontend/src/app/(app)/failed-jobs/[id]/page.tsx  |   10 +
 frontend/src/app/(app)/failed-jobs/page.tsx       |    6 +
 frontend/src/app/(app)/inventory/page.tsx         |    8 +
 frontend/src/app/(app)/layout.tsx                 |   15 +
 frontend/src/app/(app)/loading.tsx                |   13 +
 frontend/src/app/(app)/notifications/page.tsx     |    6 +
 frontend/src/app/(app)/orders/[id]/page.tsx       |   12 +
 frontend/src/app/(app)/orders/new/page.tsx        |    8 +
 frontend/src/app/(app)/orders/page.tsx            |    8 +
 frontend/src/app/(app)/payments/page.tsx          |    6 +
 frontend/src/app/(app)/platform/page.tsx          |    6 +
 frontend/src/app/(app)/webhooks/page.tsx          |    6 +
 frontend/src/app/(auth)/access-denied/page.tsx    |   13 +
 frontend/src/app/(auth)/layout.tsx                |   22 +
 frontend/src/app/(auth)/login/page.tsx            |   20 +
 frontend/src/app/(auth)/rate-limited/page.tsx     |   12 +
 frontend/src/app/(auth)/suspended/page.tsx        |    9 +
 frontend/src/app/(auth)/tenants/register/page.tsx |   11 +
 frontend/src/app/bff/api/[...path]/route.ts       |   18 +
 frontend/src/app/bff/auth/expired/route.ts        |   49 +
 frontend/src/app/bff/auth/login/route.ts          |    1 +
 frontend/src/app/bff/auth/logout/route.ts         |    1 +
 frontend/src/app/bff/auth/register/route.ts       |    1 +
 frontend/src/app/error.tsx                        |   12 +
 frontend/src/app/global-error.tsx                 |   15 +
 frontend/src/app/globals.css                      |  155 +
 frontend/src/app/health/route.ts                  |    3 +
 frontend/src/app/layout.tsx                       |   26 +
 frontend/src/app/page.tsx                         |    4 +
 frontend/src/components/api-keys.tsx              |  207 +
 frontend/src/components/auth-form.tsx             |  169 +
 frontend/src/components/catalog.tsx               |  288 +
 frontend/src/components/create-order.tsx          |  143 +
 frontend/src/components/empty-state.tsx           |   15 +
 frontend/src/components/error-notice.tsx          |   31 +
 frontend/src/components/failed-jobs.tsx           |  188 +
 frontend/src/components/inventory.tsx             |  401 ++
 frontend/src/components/notifications.tsx         |   86 +
 frontend/src/components/operations-ui.tsx         |  179 +
 frontend/src/components/order-detail.tsx          |  432 ++
 frontend/src/components/orders-list.tsx           |  139 +
 frontend/src/components/payment-settings.tsx      |  202 +
 frontend/src/components/platform-tenants.tsx      |  121 +
 frontend/src/components/providers.tsx             |   35 +
 frontend/src/components/rate-limit-notice.tsx     |   31 +
 frontend/src/components/shell.tsx                 |  149 +
 frontend/src/components/toasts.tsx                |   56 +
 frontend/src/components/ui/button.tsx             |   34 +
 frontend/src/components/webhooks.tsx              |  508 ++
 frontend/src/components/workspace.tsx             |  131 +
 frontend/src/lib/api/client.ts                    |   76 +
 frontend/src/lib/api/generated.ts                 | 3774 ++++++++++++
 frontend/src/lib/catalog.ts                       |   54 +
 frontend/src/lib/format.ts                        |   31 +
 frontend/src/lib/operations.ts                    |  141 +
 frontend/src/lib/order-stream.ts                  |  133 +
 frontend/src/lib/orders.ts                        |   79 +
 frontend/src/lib/roles.ts                         |   26 +
 frontend/src/lib/server/bff.ts                    |  348 ++
 frontend/src/lib/server/session.ts                |   50 +
 frontend/src/lib/server/workspace.ts              |   18 +
 frontend/src/lib/strings.ts                       |  607 ++
 frontend/src/lib/utils.ts                         |    3 +
 frontend/src/proxy.ts                             |   35 +
 frontend/tests/bff.test.ts                        |  335 ++
 frontend/tests/calendar-date.test.ts              |   17 +
 frontend/tests/catalog-ui.test.tsx                |  222 +
 frontend/tests/catalog.test.ts                    |   49 +
 frontend/tests/client.test.ts                     |   98 +
 frontend/tests/expiry.test.ts                     |   34 +
 frontend/tests/forms.test.tsx                     |   90 +
 frontend/tests/ingress.test.ts                    |   48 +
 frontend/tests/operations-ui.test.tsx             |  368 ++
 frontend/tests/operations.test.ts                 |  119 +
 frontend/tests/order-actions.test.tsx             |   92 +
 frontend/tests/order-stream.test.tsx              |   47 +
 frontend/tests/orders-ui.test.tsx                 |  164 +
 frontend/tests/orders.test.ts                     |   72 +
 frontend/tests/secret-lifetime.test.tsx           |   67 +
 frontend/tests/server-only.ts                     |    1 +
 frontend/tests/session-cache.test.tsx             |   28 +
 frontend/tests/session.test.ts                    |   69 +
 frontend/tests/settings-bff.test.ts               |   40 +
 frontend/tests/settings-routes.test.tsx           |   75 +
 frontend/tests/setup.ts                           |   15 +
 frontend/tests/toasts.test.tsx                    |   17 +
 frontend/tests/webhook-verification.test.ts       |   53 +
 frontend/tsconfig.json                            |   27 +
 frontend/vitest.config.ts                         |   21 +
 135 files changed, 21116 insertions(+), 8 deletions(-)
```

```text
4cce391 feat(dashboard): add owner settings and platform operations
11f601b feat(api): add safe platform tenant search
04e6ff2 docs: add phase 4 dashboard documentation and verification report
8fb54e1 feat(dashboard): add catalog and warehouse stock controls
a2a4baa feat(api): expose scoped warehouse list for dashboard
ff393c4 feat(dashboard): add orders and live saga workspace
3134310 docs: add phase 4 dashboard documentation and verification report
b534437 docs(frontend): retain generated Next.js agent guidance
a52fbe1 build(dashboard): wire compose service and frontend CI
4e3cb76 feat(dashboard): add secure BFF and seller foundation
69883dc feat(api): expose scoped store metadata for dashboard
```

No push, PR, merge or hosted deployment was performed. The report-only commit adds
this coherent verification record after those implementation commits.

## B8. Deviations and corrected verification attempts

Task 4.4 modifies no existing test. New files: `test_dashboard_tenant_search.py`
(scoped read-only search), `operations.test.ts` (validation/metadata/422 mapping),
`operations-ui.test.tsx` (settings/actions/caches), `secret-lifetime.test.tsx`
(show-once/copy/lifetime), `settings-routes.test.tsx` (page gates),
`settings-bff.test.ts` (proxy gates), `webhook-verification.test.ts` (copyable sample),
`e2e/operations.spec.ts` and its scoped Python fixture (local real API proof).

4.4 TDD/control failures: platform name search returned 3 records instead of 1 before
q was implemented; generic 422 mapped to unknown before safe validation mapping;
credential tab switching cleared unfinished inputs before the form lifetime fix.
All pass without loosening assertions. Initial build attempts exposed an optional
hint type mismatch, misplaced pending-state variable, unused test bindings and
formatting; these were corrected before the final green build. The local API was
not ready after service recreation, producing 503 at registration; the runner now
waits for explicit API/frontend health before testing. No failure is counted as a pass.
The all-browser run then exposed an existing mobile overflow: an unbroken mock
shipment tracking number could exceed a 360px panel. Its paragraph now wraps with
break-all; the existing responsive assertion is unchanged. The newly added F9
recovery poll initially expected uppercase, while the actual API enum is lowercase;
that new fixture assertion was corrected to the documented contract. Neither fix
adds an order feature or modifies an existing test.

Earlier 4.1–4.3 modified existing tests, with reasons:

- app/tests/test_tenant_isolation.py::test_tenant_resource_isolation_matrix:
  added a strict 404 assertion for a foreign order's new activity endpoint.
- app/tests/test_tenant_isolation.py::test_route_registry_has_no_unclassified_routes:
  classified the two new tenant GET routes; exact registry comparison is unchanged.

No existing frontend/browser assertion was weakened or removed. A final shared
formatting correction localizes date-only ETAs with an IST tooltip; its regression
protects against visitor-timezone day shifts. New order tests
wait for rendered server results rather than treating request initiation as completion.

frontend/playwright.config.ts now assigns a synthetic visitor to each fixture run;
the new multi-tenant helper gives each registering visitor a separate identity.
Repeated local runs had correctly exhausted the persistent 5/hour registration bucket.
The existing local-only IP gate supplies isolation; production limits and F11's
strict 429/independence checks remain intact. A new example.test registration fixture
was rejected by the existing validator and corrected to an example.com address.
A tracking PATCH took 5.26s during host load; the new browser test now explicitly
awaits that response and asserts 200 before checking dialog closure.

One baseline Vitest worker failed to start under host memory pressure; the unchanged
29-test foundation rerun passed after pausing this project's background workers.
A backend baseline process had loaded old routes before OpenAPI regeneration, causing
one freshness failure; that concurrent-edit mistake was corrected by running against
stable sources. Subsequent full SQLite and PostgreSQL suites pass. Neither failure
was hidden or addressed by loosening an assertion.

Contract adaptations are in A8/B6. Earlier 4.1 dependency decisions remain: supported
Node 22.23.5, TypeScript 5.9.3 for the generator peer, modern ESLint flat plugins rather
than the EOL/peer-constrained preset, CSS-only toasts for nonce CSP, exact pins/lockfile,
and retained framework-generated AGENTS guidance. No dependency change was needed here.

## B9. Newly discovered issues

No unresolved selected-task test failure remains. Windows port reservations and
8GB host memory pressure remain operational constraints; build/tests were serialized
and project workers temporarily paused, then restored. Do not widen loopback binds
to work around a reserved host port.

A very large lifetime activity history can exceed the existing 2MiB BFF response cap;
history pagination is a future API/UI gap. Product currency edits and a full payment
history journal are also backend contract gaps, not hidden client functionality.
The full buyer UI remains intentionally deferred.

## B10. NOT VERIFIED

- Tasks 4.5–4.7 in full; F5 buyer tracking rendering/link rotation, full screenshots
  and product README work. Full F9 injected-exhaustion/second-tenant browser scenario
  remains deferred; 4.4 uses scoped technical failure fixtures, with backend isolation tests.
- Automatic scheduled webhook backoff in the browser F7 run (forced 503 and manual
  replay are verified); full suspension-reason display requires a backend contract.
- Hosted GitHub Actions execution: configured/local equivalents pass; no push/run.
- Production deployment/TLS, real proxy/CDN identity, serverless/edge compatibility,
  multi-replica/multi-tab coherence, load/soak and full browser/device/assistive matrix.
- Real Razorpay/provider/email behavior; the optional credential test is skipped.
  SQLite skips PostgreSQL/Redis cases, which pass in that layer.
- Frontend coverage percentage, per-route first-load/wire/Brotli performance,
  all-screen4.6 accessibility and every adverse network/session-expiry browser scenario.
- A fresh dependency vulnerability audit on 25 November; dependencies are unchanged
  from the recorded 24 November clean audit.

## B11. Reviewer commands (PowerShell)

```powershell
git switch phase-4-dashboard
git log --oneline main..HEAD
$env:NEXT_TELEMETRY_DISABLED='1'
node --version # select22.23.5 first
cd frontend
npm ci
npm run gen:api:check
npm run lint
npm run typecheck
npm run test
npm run build
cd ..
$env:API_PORT='18000'
$env:FRONTEND_PORT='3000'
$env:APP_ORIGIN='http://localhost:3000'
$env:DASHBOARD_ENV='local'
docker compose build frontend
docker compose up -d frontend
$env:E2E_FRONTEND_URL=$env:APP_ORIGIN
$env:E2E_API_URL='http://127.0.0.1:18000'
$env:E2E_SINK_URL='http://127.0.0.1:18090'
# For all 6 tests, FIRST configure A7's scoped operations override, including
# the backend key/target/port settings and dev-tools sink. Wait for both health URLs.
# Keep original encryption configuration available for restoration.
try {
  $env:BFF_ALLOW_TEST_IP='true'
  docker compose up -d frontend
  npm --prefix frontend run e2e
} finally {
  $env:BFF_ALLOW_TEST_IP='false'
  # Use the original-key-only override here if the original key was set outside base Compose.
  docker compose up -d api frontend worker integrations-worker scheduler outbox-relay
  docker compose --profile dev-tools stop webhook-sink
}
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy app
.venv\Scripts\python scripts/export_openapi.py --check
$env:ENVIRONMENT='test'
$env:TEST_DATABASE_URL='sqlite:///./phase4_review_test.db'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
.venv\Scripts\python -m pytest -q
$env:TEST_DATABASE_URL='postgresql+psycopg://orders:orders@127.0.0.1:5432/orders_test'
$env:DATABASE_URL=$env:TEST_DATABASE_URL
$env:TEST_REDIS_URL='redis://127.0.0.1:6379/15'
.venv\Scripts\python -m pytest -q --cov=app --cov-report=json:.coverage.review.json --cov-fail-under=90
.venv\Scripts\python scripts/check_coverage.py .coverage.review.json
```

The browser config uses gated synthetic visitor IPs to isolate repeated Redis quotas.
Keep BFF_ALLOW_TEST_IP enabled only inside the local test try/finally.

Use a dedicated disposable PostgreSQL database ending in_test and matching host
port overrides. Leave optional provider credential variables unset for the local
suite. Never run the destructive backend fixture suite against the operational DB.
