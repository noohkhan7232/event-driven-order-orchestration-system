# Phase 4 dashboard — task 4.1

24 November 2026, Asia/Calcutta. Branch: `phase-4-dashboard`, created from
`main` at `f8208b8` after a fast-forward pull. Scope is **4.1 only**. The
remaining Phase 4 screens and acceptance experiments are deferred.

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
tooltip. There are no order-management or settings forms in this task.

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

The only backend addition is `GET /api/v1/tenants/me`, needed for the store
name in the top bar. It has no tenant selector and returns the existing
`TenantRead` projection for an active staff JWT. Existing business operations,
schemas, migrations and payment/shipping workers are unchanged.

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

## A3. Screen and route inventory

| Route | Access | Backing operation | States |
|---|---|---|---|
| `/` | Public | Redirect to guarded workspace | Anonymous goes to login |
| `/login` | Public | BFF login → API auth/login | Validation, invalid credentials, pending, quota countdown, expired/registered notices |
| `/tenants/register` | Public | BFF register → API tenants/register | Name/email/password validation, optional slug/invite, registration_closed, pending |
| `/dashboard` | Tenant admin/vendor | API auth/me and tenants/me, server-side | Store/user shell, empty state, loading skeleton, error boundary |
| `/platform` | Platform admin only | API auth/me | Platform-only foundation placeholder |
| `/access-denied` | Public explanatory screen | No business call | Friendly customer-role message |
| `/suspended` | Public explanatory screen | Reached on tenant_suspended | Dedicated contact-administrator message |
| `/rate-limited` | Public explanatory screen | Server session 429 redirect | Bounded visible retry countdown and disabled retry button |
| `/bff/auth/logout` | Same-origin POST | Cookie clear only | Clears session and query cache |
| `/bff/auth/expired` | Expiry redirect | Cookie clear only | Login with expiry notice |
| `/bff/api/[...path]` | Staff/owner/platform policy, except public tracking reads | Allow-listed API operations | Status/quota forwarding, role denial, 401/403 handling, bounded JSON/SSE |
| `/health` | Public operational probe | Frontend process only | JSON health; does not assert backend readiness |

Admin navigation includes the planned seller sections. Vendor navigation hides
owner-sensitive sections; customer accounts cannot enter the app. Platform
accounts see only the platform section. Links for later tasks are visibly disabled
and are not implemented business screens. Tenant callers cannot proxy platform
operations; vendor callers cannot proxy owner settings, keys, webhooks or failed jobs.

## A4. Live updates

```mermaid
flowchart LR
  Client[Future fetch-based SSE consumer] -->|Last-Event-ID| BFF[Unbuffered bounded stream]
  BFF -->|visitor IP and server Bearer when private| API[Existing private/public SSE]
  API --> BFF --> Client
  Client -->|disconnect| Cancel[Await upstream cancellation]
```

The foundation forwards Accept and Last-Event-ID, preserves event-stream content
type, sets no-store/no-referrer and disables intermediary buffering. It reads on
demand, enforces the 2 MiB per-response cap and propagates cancellation before
the caller's cancellation completes. A capped stream ends with a transport error.
Ordinary bodies are bounded before returning. The fetch headers deadline is 20s;
this is not a claim of a total deadline for all streaming/body I/O.

Browser ordering/deduplication, reconnect/resume, Live indicators and polling
fallback are **not implemented** here; they belong to 4.2/4.5. Those consumers must
resume after disconnect or the stream cap rather than assuming an infinite stream.

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
| TRUSTED_PROXY_COUNT | API Compose environment 1 for the BFF hop |
| NEXT_TELEMETRY_DISABLED | 1 in Docker; use 1 for local/CI verification |

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

Actual browser coverage and counts are in Part B. Comprehensive screen/device,
keyboard and contrast verification for the unimplemented screens remains 4.6 work.

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

## A8. Deferred work and limits

4.2 orders, 4.3 catalog/inventory, 4.4 settings/operations, 4.5 buyer tracking,
4.6 comprehensive quality and 4.7 full proof/screenshots are not implemented in
this session. There is no checkout, billing, team invitation management, password
reset, marketing site, dark mode or broader localization. The existing backend
remains the authority; no client role claim can grant API privileges.
Successful auth submissions need JavaScript for the custom CSRF header. The native
fallback uses POST and is rejected safely; it never places credentials in a URL.

Production ingress/TLS, serverless deployment, multiple frontend replicas and a
cross-browser/device matrix are not established by local Docker proof. Future query
keys must still include tenant identity; cache clearing is not a replacement for
backend isolation. Full buyer tracking will also need its public-link configuration
and reconnect client work. BFF allow-list/role rules must be reviewed when new API
prefixes are introduced, and the real IP proof rerun after Next/ingress changes.

# Part B — Verification

## B1. Summary

Task 4.1 is DONE. Tasks 4.2–4.7 are skipped by this session's explicit scope.
No selected blocker remains. The report-only commit follows the four commits in
B7; the completed session therefore has five commits. Nothing was pushed, merged
or deployed to a hosted environment.

The real Docker frontend passes four browser tests, including the F1/F11 foundation
proofs. The final image runs as UID1000 on Node22.23.5. The local IP override is
restored to false. All core application services are running and migrate exited0.
Optional Mailpit/sink containers were already stopped after the Docker restart;
they are not required by 4.1 and are not claimed as verified here.

## B2. Task table

| Task | Status | Evidence/commits |
|---|---|---|
|4.1 foundation/BFF/client/layout/IP/Compose/CI|DONE|69883dc API;4e3cb76 frontend;a52fbe1 integration;b534437 generated framework guidance; final gates below|
|4.2 orders|SKIPPED|Outside this session|
|4.3 catalog/inventory/warehouses|SKIPPED|Outside this session|
|4.4 settings/integrations/operations|SKIPPED|Outside this session|
|4.5 public tracking UI|SKIPPED|Only its existing API path is proxied/rate-tested here|
|4.6 comprehensive quality|SKIPPED|Foundation checks do not complete all-screen quality work|
|4.7 full e2e/screenshots/docs|SKIPPED|Only the 4.1 report and foundation browser subset are completed|

## B3. Test evidence

Frontend: **29 unit/integration tests across8 files**, no skips. Tests cover cookie
flags/no token JSON, allow-list/traversal, both CSRF signals, fixed/chunked body limits,
SSE cap/cancellation, header stripping including Connection-named fields, status/quota
preservation, 401 handling, vendor/customer/platform gates, expiry/rate/suspension
navigation, query-cache clearing, API query/Headers semantics, forms/invite/error
mapping, countdown, locale helpers and escaped/CSP-compatible toasts.

Unit fetch is disabled by default unless explicitly mocked. The browser tests use
only the local frontend/API; no real provider, SMTP or external HTTP service is
called by these tests. Auth tracing is disabled. Passwords are random disposable
fixtures, never printed. Browser storage has no JWT; document.cookie is empty while
the session cookie is confirmed httpOnly by privileged test inspection.

Axe: zero serious/critical findings on registration, login and the authenticated
shell. The same browser flow verifies no horizontal overflow at360/768/1280,
including a long owner name, drawer open/Escape close and logout access removal.
This is not an all-page/device/browser certification. Frontend coverage was not
measured. Backend PostgreSQL production coverage is97.24%; all18 required modules
meet90%, and the modified tenancy endpoint module is100% covered.

Observed red-to-green controls:

| Control | Before | Final correction |
|---|---|---|
|Tenant metadata endpoint|4 failures:404|4 new matrix cases plus existing isolation controls:11 passed|
|Chunked request size|Expected413, got502|Request overflow separated from upstream response overflow|
|Backend forbidden code|Generic unknown error|Correct friendly auth mapping|
|SSE cap/cancellation|Oversized stream accepted; first transform delayed cancellation|Pull-based bounded stream awaits upstream cancel|
|Connection-named headers|Whitelisted hop field forwarded|Dynamic hop-name exclusion on both sides|
|Client URL/Headers options|2 failures|Validate normalized pathname, preserve query values and Headers entries|
|Session cache|Old fixture data remained after logout|Auth changes clear shared query cache|
|SSR throttling/metadata expiry|Generic error|Dedicated countdown and expiry redirect from either lookup|
|Expiry helper|2 failures: valid GET cleared session, no API recheck|Only invalid/absent sessions clear; valid/refreshed sessions survive|
|Native forms|2 failures: missing POST|Explicit POST/action and real no-JS rejection proof|
|Contrast|Axe footer ratio4.43 vs4.5|Darker auxiliary text/borders; assertion retained|
|Desktop overflow|Strict width assertion failed with long name|minmax(0,1fr) and min-width0; no overflow-hiding workaround|

## B4. Raw gates and build inventory

Final Docker RUN chain is `npm run lint && npm run typecheck && npm run test && npm run build`;
it exited0 on the exact pinned Node22 image. API freshness is checked separately
against repository docs, which are deliberately outside the frontend image context.

```text
All matched files use Prettier code style!
Types generated successfully
Test Files  8 passed (8)
Tests  29 passed (29)
Next.js 16.4.6 (webpack)
Compiled successfully; production image Built
Ruff: All checks passed!
Ruff format: passed
Mypy: Success: no issues found in 176 source files
OpenAPI export is fresh
SQLite: 610 passed, 45 skipped in 311.19s (0:05:11)
PostgreSQL+Redis: 654 passed, 1 skipped in 2016.93s (0:33:36)
Backend production:97.24%; all18 required modules >=90%
Compose controls:6 passed; environment uniqueness:1 passed
```

The final Windows production build also exits0. Dev and standalone startup smoke
results, API-type positive/negative freshness, exact pins and bundle sweep:

```text
PASS Windows npm run start: health200; API proxy404; socket preload; local loopback only
PASS Windows npm run dev: health200; API proxy404; socket preload; local loopback only
PASS generated API freshness rejects modified types
PASS generated API types restored and fresh
PASS production startup test-IP flag true: refused
PASS production startup test-IP flag false: starts
PASS exact direct dependency pins and lockfile agreement
PASS client bundle marker sweep: five server-only markers absent
```

Next16 emits a route inventory without a per-route KB table in this build mode.
The following is its real inventory; byte counts below are separately measured
artifact totals, not a claim about initial-page transfer size.

```text
Route (app)
┌ ƒ /
├ ƒ /_not-found
├ ƒ /access-denied
├ ƒ /bff/api/[...path]
├ ƒ /bff/auth/expired
├ ƒ /bff/auth/login
├ ƒ /bff/auth/logout
├ ƒ /bff/auth/register
├ ƒ /dashboard
├ ƒ /health
├ ƒ /login
├ ƒ /platform
├ ƒ /rate-limited
├ ƒ /suspended
└ ƒ /tenants/register


ƒ Proxy (Middleware)

ƒ  (Dynamic)  server-rendered on demand
```

| Artifact inventory | Files | Raw bytes | Sum of per-file gzip bytes |
|---|---:|---:|---:|
|All client JavaScript chunks|42|1,204,425|380,484|
|Client CSS|1|15,673|4,304|

Gzip uses deterministic local compression and is not a network waterfall or Brotli
measurement. The sweep checks API_INTERNAL_URL, BFF_ALLOW_TEST_IP,
BFF_TRUSTED_PROXY_IPS, CREDENTIALS_ENCRYPTION_KEY and oos_session are absent from
client JS. No real credential was supplied to the frontend build.

Exact selected versions (npm registry verified on24 November2026):

| Package | Pin |
|---|---|
|next|16.4.6|
|react|19.3.1|
|react-dom|19.3.1|
|@tanstack/react-query|5.108.4|
|react-hook-form|7.92.0|
|zod|4.6.7|
|tailwindcss|4.3.5|
|typescript|5.9.3|
|eslint|10.15.0|
|typescript-eslint|8.74.0|
|@next/eslint-plugin-next|16.4.6|
|eslint-plugin-react-hooks|7.1.1|
|prettier|3.10.1|
|openapi-typescript|7.13.0|
|vitest|5.1.1|
|@testing-library/react|16.3.3|
|@playwright/test|1.65.1|
|@axe-core/playwright|4.14.0|

The clean current dependency install reports341 added/348 audited and0 vulnerabilities;
`npm ls --all` exits0. Initial TypeScript7 conflicted with the generator's5.x peer;
verified5.9.3 is used. The legacy Next ESLint preset was replaced with compatible
current plugins rather than shipping EOL ESLint9 or forcing peer dependencies.

## B5. Real Docker browser proof and final runtime

```text
PASS native no-JavaScript auth uses POST; CSRF rejection; no credentials in URL/response
PASS F1 foundation; a11y login/register/shell zero serious/critical; widths360/768/1280; httpOnly session; logout guarded
PASS F11: login and public tracking independently rate-limited per visitor THROUGH BFF; spoofed XFF stripped; visible countdown
PASS BFF CSP nonce/header/CSRF/path guards and retry screen on real server
  4 passed (33.3s)
```

| Experiment | Result in this scope |
|---|---|
|F1|PASS: UI register/login/store shell/logout and guard; also foundation axe/width checks|
|F11|PASS: per-visitor login and public tracking limits through BFF; visible countdown|
|F2–F10|NOT RUN: later-task UI is not implemented|
|F12|NOT RUN as a real multi-role/suspended browser experiment; foundation role/session/BFF unit controls pass|
|Additional foundation checks|PASS: nonce/static headers/CSRF/path/retry page; no-JS POST keeps credentials out of URLs|

F11 uses invalid tracking capabilities and expects the API's uniform404 before
quota exhaustion. It proves route-level per-visitor limiting, not the unimplemented
buyer page. Local test IPs are explicitly gated; a constant spoofed client XFF does
not merge the two independent visitor buckets.

Final default-state inspection (raw):

```text
Node v22.23.5; test_ip=false; environment=local
1000
API trusted_proxy_count=1
127.0.0.1:3000
SERVICE               STATE     STATUS
api                   running   Up 5 hours
frontend              running   Up 4 hours (healthy)
integrations-worker   running   Up 7 seconds
mailpit               exited    Exited (255) 6 hours ago
migrate               exited    Exited (0) 8 seconds ago
outbox-relay          running   Up 8 seconds
postgres              running   Up 5 hours (healthy)
rabbitmq              running   Up 5 hours (healthy)
redis                 running   Up 5 hours
scheduler             running   Up 6 seconds
webhook-sink          exited    Exited (255) 6 hours ago
worker                running   Up 7 seconds
```

Core workers were restored after memory-constrained verification. Frontend health
is a process probe; API connectivity is independently exercised by real browser
registration/login and the Windows public-proxy smoke. No frontend test flag stays on.

## B6. Backend additions and gaps

**Addition:** `GET /api/v1/tenants/me` in `app/api/v1/endpoints/tenancy.py` returns
only the current active tenant's existing public metadata for ADMIN/VENDOR JWTs.
It has no caller-supplied tenant selector. Anonymous, customer, integration key,
platform-only and suspended cases are denied. The matrix reads two separate tenants
and checks the exact response/no private hash or password fields. No migration or
existing business-handler behavior change was needed. OpenAPI and generated types
include the endpoint and pass freshness checks.

**Later gap:** the current contract exposes warehouse creation but no warehouse-list
GET. Revisit that narrowly in4.3 rather than adding an endpoint preemptively now.
No other backend addition was made.

## B7. Source diff and commits

Snapshot against main before this standalone report-only commit:

```text
.dockerignore                                     |    1 +
 .env.example                                      |    7 +
 .gitattributes                                    |    3 +
 .github/workflows/ci.yml                          |   21 +
 README.md                                         |   52 +
 app/api/v1/endpoints/tenancy.py                   |   15 +-
 app/tests/test_dashboard_tenant.py                |   79 +
 app/tests/test_tenant_isolation.py                |    1 +
 docker-compose.yml                                |   21 +-
 docs/openapi.json                                 |   65 +
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
 frontend/e2e/foundation.spec.ts                   |  210 +
 frontend/eslint.config.mjs                        |   29 +
 frontend/next.config.ts                           |   26 +
 frontend/package-lock.json                        | 6458 +++++++++++++++++++++
 frontend/package.json                             |   64 +
 frontend/playwright.config.ts                     |   15 +
 frontend/postcss.config.mjs                       |    2 +
 frontend/scripts/generate-api.mjs                 |   15 +
 frontend/scripts/ingress.cjs                      |   42 +
 frontend/scripts/start.mjs                        |   26 +
 frontend/src/app/(app)/dashboard/page.tsx         |   18 +
 frontend/src/app/(app)/layout.tsx                 |   15 +
 frontend/src/app/(app)/loading.tsx                |   13 +
 frontend/src/app/(app)/platform/page.tsx          |   13 +
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
 frontend/src/app/globals.css                      |   89 +
 frontend/src/app/health/route.ts                  |    3 +
 frontend/src/app/layout.tsx                       |   26 +
 frontend/src/app/page.tsx                         |    4 +
 frontend/src/components/auth-form.tsx             |  169 +
 frontend/src/components/empty-state.tsx           |   15 +
 frontend/src/components/error-notice.tsx          |   31 +
 frontend/src/components/providers.tsx             |   35 +
 frontend/src/components/rate-limit-notice.tsx     |   31 +
 frontend/src/components/shell.tsx                 |  138 +
 frontend/src/components/toasts.tsx                |   40 +
 frontend/src/components/ui/button.tsx             |   34 +
 frontend/src/lib/api/client.ts                    |   76 +
 frontend/src/lib/api/generated.ts                 | 3591 ++++++++++++
 frontend/src/lib/format.ts                        |   15 +
 frontend/src/lib/roles.ts                         |   26 +
 frontend/src/lib/server/bff.ts                    |  348 ++
 frontend/src/lib/server/session.ts                |   50 +
 frontend/src/lib/strings.ts                       |  152 +
 frontend/src/lib/utils.ts                         |    3 +
 frontend/src/proxy.ts                             |   35 +
 frontend/tests/bff.test.ts                        |  335 ++
 frontend/tests/client.test.ts                     |   98 +
 frontend/tests/expiry.test.ts                     |   34 +
 frontend/tests/forms.test.tsx                     |   90 +
 frontend/tests/ingress.test.ts                    |   48 +
 frontend/tests/server-only.ts                     |    1 +
 frontend/tests/session-cache.test.tsx             |   28 +
 frontend/tests/session.test.ts                    |   69 +
 frontend/tests/setup.ts                           |   15 +
 frontend/tests/toasts.test.tsx                    |   17 +
 frontend/tsconfig.json                            |   27 +
 frontend/vitest.config.ts                         |   21 +
 80 files changed, 13167 insertions(+), 3 deletions(-)

b534437 docs(frontend): retain generated Next.js agent guidance
a52fbe1 build(dashboard): wire compose service and frontend CI
4e3cb76 feat(dashboard): add secure BFF and seller foundation
69883dc feat(api): expose scoped store metadata for dashboard
```

Generated API types and npm lock data account for much of the mechanical diff.
The report itself is committed alone after these four commits.

## B8. Deviations and corrected verification attempts

- Only4.1 is in scope; disabled navigation is availability information, not completed
  order/settings screens. The full Part4 experiment set is not claimed green.
- Existing modified test: `app/tests/test_tenant_isolation.py::test_route_registry_has_no_unclassified_routes`
  adds the new read route to the tenant set. Its exact route equality/policy checks
  remain. No other existing backend test was changed or weakened. All frontend tests
  are new; fixture/mock corrections retained their behavioral assertions.
- Next16 uses `proxy.ts` for the requested middleware role. Its cookie-presence check
  is optimistic; real authorization remains in server layout/BFF/API. The Node socket
  preload is required because route handlers do not expose a reliable socket peer.
- Proxy401 remains401 with cookie clearing/auth redirect metadata; the client navigates
  to login. This preserves API status while implementing the requested expiry flow.
- Owned shadcn-style button/Radix drawer source is used with components.json. Toasts
  are an owned CSS-only implementation because the initial optional library injected
  non-nonced styles. No remote component registry is needed at runtime.
- The host default Node was24; an explicitly selected verified Node22.23.5 binary
  was used for final host checks. Docker/CI pin22.23.5. No machine-wide Node install
  or private environment file was overwritten.
- Initial in-place lint dependency replacement hit an npm stale-tree peer conflict.
  A clean generated dependency install resolved it without force/legacy-peer flags.
- Parallel host/Docker builds exhausted the observed8 GB RAM. Duplicate attempts
  were stopped; final gates run serially with one Next worker and limited builder
  heap. Only this project's background workers were paused and then restored.
- Port13000 became Windows-reserved after Docker restart. That setup attempt did
  not reach browser assertions; final proof uses the default3000 loopback port.
- A copy edit introduced CP1252 apostrophes. Webpack rejected invalid UTF-8; the file
  was corrected/validated and line endings normalized. A stale-image browser run is
  discarded. Only the successful rebuilt-image run above is final evidence.
- Real contrast and desktop overflow failures were fixed in code; no axe threshold,
  viewport assertion, retry/status expectation or role restriction was loosened.
- `next dev` generated frontend/AGENTS.md during startup proof. Its origin was
  checked in Next's generate-agent-files implementation and its guidance retained
  in a separate documentation commit. No delegation was used.
- Synthetic stores are retained with disposable unknown-to-users passwords; no
  money/order history or unrelated volume was deleted. No real provider was called.

## B9. Newly discovered issues

No unresolved selected-task blocker remains. The warehouse-list contract gap is
later4.3 work. Deployment IP identity depends on a correctly configured private
trusted ingress; the local fixture override cannot establish that production proof.
The older preset's peer support lag is avoided by current flat plugins. Windows
port reservations and RAM pressure are environmental facts, not passing assertions.

## B10. NOT VERIFIED

- Tasks4.2–4.7 and F2–F10/full F12, screenshots of their missing screens, complete
  order/SSE consumer flows, settings show-once secrets and buyer tracking rendering.
- Hosted GitHub Actions execution: the job is configured and local equivalents pass;
  no push/workflow run was performed.
- Production deployment/TLS, real reverse proxy/CDN peer identity, serverless/edge
  compatibility, multi-replica frontend operation, multi-tab auth coherence, load/soak
  and a comprehensive mobile/browser/assistive-technology matrix.
- Live Razorpay/provider/email behavior; the optional backend credential test is
  skipped. SQLite also skips PostgreSQL/Redis-only cases, which pass in that layer.
- Frontend test coverage percentage, per-route first-load/wire performance, the full
  4.6 a11y/performance gate and development HMR behavior beyond startup/proxy smoke.

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
try {
  $env:BFF_ALLOW_TEST_IP='true'
  docker compose up -d frontend
  npm --prefix frontend run e2e
} finally {
  $env:BFF_ALLOW_TEST_IP='false'
  docker compose up -d frontend worker integrations-worker scheduler outbox-relay
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

Use a dedicated disposable PostgreSQL database ending in_test and matching host
port overrides. Leave optional provider credential variables unset for the local
suite. Never run the destructive backend fixture suite against the operational DB.
