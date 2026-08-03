# Sub Remuxer

🇬🇧 **English** | [🇷🇺 Русский](README.ru.md)

A filtering proxy for proxy-server subscriptions.

The application's display name is **Sub Remuxer**. Identifiers use the `subremuxer` slug: the
repository and docker image names, the database file, the session cookie, the `kind` field in
configuration files.

The app sits between your client (Happ, v2RayTun, sing-box, Clash/Mihomo, v2rayN…) and the panel
(Remnawave, Marzban, Marzneshin, 3x-ui, Hiddify — anything that serves standard subscription
formats). The client follows a Sub Remuxer link, and Sub Remuxer:

1. requests the original subscription, **substituting the required `x-hwid`** (and, if you want,
   the rest of the device headers);
2. parses the response in whatever format the panel served it;
3. **drops the servers you don't want** by a regular expression over the name and by a protocol list;
4. **reassembles the subscription** in the same format and hands it to the client;
5. **logs** every request: what came in, what was filtered out and why, what was served.

<br>

## Why you would want this

* **One HWID for every device.** The panel counts devices by the `x-hwid` header. Set the same
  HWID everywhere and all your clients look like a single device to the panel — the device limit
  stops getting in the way.
* **Only the servers you need.** A subscription with 80 locations turns into one with 6 —
  "everything containing LTE and not containing RU" — without editing configs by hand.
* **No changes on the client.** The client has no idea anyone is standing between it and the
  panel: the response format, the `subscription-userinfo` / `profile-title` /
  `profile-update-interval` headers and everything else pass straight through.
* **Several panels behind one link.** Subscriptions from different providers are merged into one:
  each is filtered by its own rules and with its own HWID, while the client receives a single
  server list and never learns there were five sources.

<br>

## How it works

### Formats

A subscription is not "the Xray format". It is a universal container that the panel serves in one
of four shapes, chosen from the request headers (primarily the `User-Agent`):

| Family | What it is | How it is detected |
|---|---|---|
| **Base64 / URI list** | `vless://`, `vmess://`, `trojan://`, `ss://`, `hysteria2://`, `tuic://`… lines | the whole body in base64 or in plain text |
| **Xray JSON** | either an array of full configs with `remarks`, or a single config with `outbounds` | JSON whose outbounds have a `protocol` |
| **Sing-box JSON** | a config with `outbounds` (and `endpoints` since 1.11) | JSON whose outbounds have a `type` |
| **Clash / Mihomo / Stash** | YAML with `proxies` and `proxy-groups` | YAML with a `proxies` key |

Sub Remuxer **does not convert between families** — it forwards the client's `User-Agent` and
`Accept` upstream, the panel decides what to answer with, and filtering happens inside the format
that came back. If you need to force the panel into a particular family, set a substitute
`User-Agent` in the profile (ready-made presets are included).

The one exception is switching base64 ↔ plain list: that is the same document in a different
wrapper, so the conversion is safe and is exposed as a profile setting (and as a
`?format=base64|plain` query parameter for debugging).

### Filtering without corrupting the config

The parsers deliberately **do not rebuild** nodes: the original representation is kept as-is and
filtering only removes entries. A subscription that passed through without a filter is therefore
semantically identical to the original — no client will break over a serialisation quirk.

Removing a node is more than removing a line. Groups reference nodes by name, and a group left
empty breaks the client. So removal cascades:

* **sing-box** — names are cleaned out of `selector`/`urltest`, an emptied group is removed,
  references to it are cleaned out further, and `default` and `route.final` are pointed at a live tag;
* **Clash** — the same for `proxy-groups`; a rule that targeted a removed group is rewritten to
  `DIRECT` (rules are never dropped silently); a group that lives off a `use:` (provider) is kept
  even with no proxies of its own;
* **Xray** — `routing.rules` entries pointing at a removed `outboundTag` are dropped and balancer
  selectors are cleaned up.

### Mimicry

The main scenario is connecting a client that cannot send an HWID itself. That is why a profile is
created with **device** mimicry by default — the panel sees a Google Pixel 9 carrying your HWID.

Two different things must not be confused here:

| What is substituted | What it controls | Safe by default |
|---|---|---|
| `x-hwid`, `x-device-os`, `x-ver-os`, `x-device-model` | how the panel counts and attributes devices | **yes** — no effect on the response format |
| `User-Agent` | **which subscription format the panel returns** | no — enable it deliberately |

The HWID works **independently of the User-Agent**: the `x-hwid` header is all the panel needs.
Substituting the User-Agent, on the other hand, makes the panel answer every client with the same
format — and a client that cannot read that format imports zero servers.

What that looks like in practice: the profile has substitution to Happ enabled, so the panel serves
an Xray-JSON array to everyone. A client like NekoBox reads base64 only — it imports an empty list,
wipes the servers it had already saved and tears down its own connection. And neither the log nor
the panel's response contains an error: as far as both sides are concerned, everything succeeded.

That is why the client's `User-Agent` is **passed through unchanged** by default, and every client
gets a format it can read. Substitution lives in a separate list behind a warning and is only
needed when you want to impose a format on purpose — there are dedicated "make the panel serve …"
templates for that.

Devices to choose from: Pixel 9 and 9 Pro, Galaxy S24 Ultra, Xiaomi 14, iPhone 16 Pro,
iPhone 14 Pro Max, iPad Pro, a Windows 11 PC, a Mac. Clients for format substitution: Happ,
v2RayTun, Streisand, sing-box, Karing, Hiddify, Clash Verge/Mihomo, FlClash, Stash, Shadowrocket,
v2rayN, v2rayNG, Throne, a browser. Any field can be typed in by hand.

### HWID

The panel (Remnawave ≥ 2.9.0) accepts an `x-hwid` of 10–64 characters made of Latin letters,
digits, `=` and `-`, and simply ignores the header otherwise. The admin UI highlights a malformed
value as you type.

Three modes, per profile:

| Mode | Behaviour |
|---|---|
| **Override** (`override`) | always send our HWID, even if the client sent its own |
| **Fallback** (`fallback`) | send our HWID only when the client sent none |
| **Passthrough** (`passthrough`) | forward the client's HWID as-is |

`x-device-os`, `x-ver-os` and `x-device-model` can be set as well — globally and/or per profile.

### Aggregates: several subscriptions behind one link

An aggregate merges several profiles under a single token. A source stays an ordinary profile —
its own panel link, its own HWID, its own mimicry, its own filter, its own "Test" button, its own
link if you want one separately. The aggregate only adds what a profile cannot have: a parallel
walk over every source and the splicing of whatever survived their filters.

```
                     ┌── profile "Panel A" ── own HWID, own filter ──▶ panel A
client ──▶ aggregate ─┼── profile "Panel B" ── own HWID, own filter ──▶ panel B
                     └── profile "Panel C" ── disabled, skipped
                            │
                            └─▶ one server list, in the first source's format
```

* **The order of sources** in the aggregate is the order of servers in the resulting list.
* **Captions.** A source marker is appended to every server name via ` · ` — the profile name or a
  short caption of your own. Without it, identical names from different panels are
  indistinguishable, and in Clash and sing-box names must be unique on top of that: collisions get
  a "(2)".
* **Deduplication.** A server with the same protocol, address and port enters the list once.
* **Groups and rules** of the first source are carried into the result and re-pointed at the
  combined list: a selector that held only its own servers gets the other panels' servers too, and
  a rule that pointed at a renamed server follows it.
* **One failing source does not sink the aggregate.** As long as somebody answered, the client gets
  a subscription and the reasons for the rest land in the log. If nobody answered — a 502 listing them.

The format is taken from the first source: the app does not translate between families here either.
Normally every panel answers the same way — they are all looking at the User-Agent of one and the
same client — but a source that answered with a different family is skipped, and that is visible in
the log.

One aggregate fetch produces several log entries: one per source, with its HWID and a per-server
breakdown, plus a summary one under the aggregate's name.

### Capturing client data

So you don't have to hunt for the HWID in the app's settings, there is a separate trap link. Add it
to the client as an ordinary subscription — the app will record the `x-hwid`, the model and the
User-Agent, and will return those very values to the client as server names, so they are visible
right in its list. Opened in a browser, the same link shows a human-readable page.

Captured devices are collected in the "Capture" section: repeat visits do not multiply records but
increment a counter. From there the HWID goes into a profile via a dropdown, or is copied with one
button. The link can be reissued.

### Templates

A template stores everything except the profile name and the subscription link: mimicry, HWID mode,
filter, protocols, output format. A new profile is created either from scratch or from a template
in one click. Any existing profile can be saved as a template, and templates can be edited and
deleted.

Built in, and restorable in one click: device mimicry as a Pixel 9 and as an iPhone, Pixel 9 +
foreign locations only, Pixel 9 + mobile channels, three "make the panel serve …" templates (Xray
JSON, sing-box, Clash/Mihomo) and "no mimicry at all" as a clean starting point.

### Configuration editor

The whole configuration — settings, templates and profiles — is available as a single YAML or JSON
document in a built-in editor with highlighting and line numbers. The "Validate" button parses the
document and shows exactly what would change, touching nothing; "Apply" brings the installation to
exactly what the document says.

How applying works:

* the document is **validated as a whole** before the first write — a typo in the last profile will
  not leave the first half applied;
* a profile is matched to an existing one **by token**, so renaming does not break a link clients
  have already imported;
* profiles that disappeared from the document are deleted **softly** and are recoverable for a day;
* built-in templates are matched by their own identifier, so they can be renamed without turning
  into a copy.

The same document can be exported as a file (YAML or JSON) and loaded back. Import, unlike the
editor, only adds: existing profiles are untouched and conflicting names get a suffix. Subscription
tokens are optionally preserved so that already distributed links keep working.

**The configuration file contains subscription tokens and HWIDs — keep it as a secret.**

### Regexp builder

Conditions ("contains", "does not contain", "starts with", "equals", "matches regexp", …) are
combined with AND/OR and **compiled into a single regular expression**, which is shown right there
in the UI. The expression shown is exactly the one applied to server names, so the preview cannot
diverge from the real behaviour:

```
contains "LTE" + does not contain "RU"   →   (?i)^(?=.*LTE)(?!.*RU).*$
```

The **Test** button goes to the real subscription and shows the list of servers marking what
survived, what was dropped and for what reason. There are ready-made templates and a "custom
regexp" mode with separate include/exclude.

<br>

## Quick start

### Docker

```bash
docker run -d --name subremuxer -p 127.0.0.1:8000:8000 -e ADMIN_PASSWORD=... -e PUBLIC_BASE_URL=https://sub.example.org -v ./data:/data ghcr.io/nd4y/subremuxer:latest
```

Or with compose:

```bash
ADMIN_PASSWORD=... PUBLIC_BASE_URL=https://sub.example.org docker compose up -d
```

### Railway

The repository is ready to deploy as-is: `railway.json` tells it to build the `Dockerfile` and
check liveness at `/healthz`, and the image listens on the port from `$PORT`.

What to set in the service's variables:

| Variable | Value |
|---|---|
| `ADMIN_PASSWORD` | the admin password (or `DEMO_MODE=true`, see below) |
| `PUBLIC_BASE_URL` | `https://<your-domain>.up.railway.app` — subscription links are built from it |
| `COOKIE_SECURE` | `true` |
| `DATA_DIR` | the mounted volume's path, e.g. `/data` |
| `PORT` | the same port as the one set on the service's domain (see below) |

**About the port.** Railway injects its own `PORT` and routes to the port configured on the
service's domain. If those two numbers diverge you get a deceptive picture: the container is
healthy, the healthcheck passes, and the domain returns a 502 "Application failed to respond". The
simplest fix is to set `PORT` explicitly, to the same number the domain uses.

**About the volume.** The SQLite database lives in `DATA_DIR`. Without a mounted volume the
container's filesystem is ephemeral and the profiles will vanish on the next deploy. The volume
must be mounted at exactly the path `DATA_DIR` points to.

There is no `VOLUME` instruction in the `Dockerfile` and none should be added: Railway rejects such
a Dockerfile and the build fails seconds before it starts with
`docker VOLUME is not supported, use Railway Volumes`. Volumes are configured through the platform.

Volume permissions sort themselves out: the container starts as root just long enough to make the
data directory accessible to user `10001`, and drops privileges before launching the app. The app
itself never runs as root. If the container is already running as an unprivileged user, that step
is skipped, and on a permissions problem the app fails with an explicit message rather than a
cryptic SQLite error.

**Auto-deploy.** The service is linked to the repository in its settings, and then a push to the
chosen branch deploys itself. It is worth turning on **Wait for CI** right there: Railway will wait
for the workflow from `.github/workflows/`, and if the tests or the image build failed, the deploy
gets the `SKIPPED` status instead of carrying a broken commit onto a live stand. The platform has
one condition: the workflow must be triggered by `push` to that branch — `ci.yml` is set up that way.

### Locally

```bash
pip install -e ".[dev]"
```

```bash
ADMIN_PASSWORD=dev uvicorn app.main:app --reload
```

The admin UI is at the root (`/`), the API documentation at `/api/docs`.

<br>

## Sign-in and roles

There are two ways in: a master password and OpenID Connect. The password always works, the
provider is optional; when both are configured, the sign-in screen shows both buttons.

### Roles

| Role | What it sees and can do |
|---|---|
| **Administrator** | everything: whole profiles, the log, capture, templates, the configuration editor, settings |
| **Viewer** | only the lists of subscriptions and aggregates: name, link, QR code and the import-into-client buttons |

A viewer is not shown the original subscription's address, the HWID, the filter, the mimicry
settings or the aggregate's composition — these fields are not hidden in the UI but **stripped on
the server**, so they never reach the browser at all. Everything except reading profiles and
aggregates answers a viewer with `403`. The role has its own help: how to add the link to a client,
how to scan the QR code and what to do if no servers showed up.

The role is determined by membership in groups that arrive in the token. A user who lands in both
groups gets administrator rights.

### Setting up Keycloak

1. Create a client: **Client authentication** on (confidential), **Standard flow**, valid redirect
   URI `https://<your-domain>/auth/oidc/callback`.
2. Create two groups, e.g. `subremuxer_admins` and `subremuxer_viewers`.
3. Add a **Group Membership** mapper with Token Claim Name `groups` under
   **Client scopes → \<client\>-dedicated**. Clear **Full group path**, or leave it — the app
   understands both spellings and compares by the last path segment.

   A mapper on the client's dedicated scope works for all tokens by itself, and there is no need to
   ask for groups as a separate scope. In fact it is harmful: a client scope named `groups` is not
   built into Keycloak, and requesting a non-existent scope aborts the whole sign-in with
   `invalid_scope` — before the password form even appears. That is why `OIDC_SCOPES` defaults to
   `openid profile email`.
4. Set the variables:

```bash
OIDC_ISSUER=https://auth.example.org/realms/main
OIDC_CLIENT_ID=subremuxer
OIDC_CLIENT_SECRET=...
OIDC_ADMIN_GROUP=subremuxer_admins
OIDC_VIEWER_GROUP=subremuxer_viewers
```

The app validates the `id_token` against the provider's JWKS: signature, `iss`, `aud`, expiry and
`nonce`. Sign-in itself is authorization code with PKCE; a started attempt is stored in the
database, so a restart between the redirect to the provider and the return breaks nothing.

### Provider only

Like Grafana, via two independent flags:

```bash
OIDC_AUTO_LOGIN=true            # skip the sign-in screen, go straight to the provider
AUTH_DISABLE_LOGIN_FORM=true    # remove password sign-in entirely
```

`OIDC_AUTO_LOGIN` removes an extra screen: an unauthenticated visitor lands directly at the
provider. `AUTH_DISABLE_LOGIN_FORM` is not cosmetic: the `/api/auth/login` endpoint starts
answering `404` and the password stops being accepted. Both settings are ignored if OIDC is not
configured — otherwise the installation would be left with no way in at all; a warning is written
to the log when that happens.

### If the provider is unreachable

Check the log first: at startup the app reads the provider's `.well-known` itself and complains
loudly if it did not answer. After that, it depends:

| What happened | What to do |
|---|---|
| The provider is down and `AUTH_DISABLE_LOGIN_FORM` is not set | open `https://<domain>/?disableAutoLogin=true` and sign in with the master password — no restart needed |
| Sign-in works but there is no role | the app shows a page listing the groups that actually arrived in the token and the claim it looked for them in. That is usually enough to fix the mapper |
| `AUTH_DISABLE_LOGIN_FORM=true` and the provider is unreachable | unset the variable and restart the container |

The `?disableAutoLogin=true` parameter is named after Grafana's and does the same thing: it
disables the automatic redirect to the provider for the current tab. Signing out sets the same flag —
otherwise, with auto-login on, the "Sign out" button would return the same session.

An emergency master-password sign-in while a provider is configured is written to the log as a
separate warning: it is an event that should be noticeable.

<br>

## Environment settings

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_PASSWORD` | *generated* | the admin password. If unset, a random one is generated and written to the log once |
| `AUTH_DISABLE_LOGIN_FORM` | `false` | removes password sign-in — both the button and the endpoint. Ignored if OIDC is not configured |
| `DEMO_MODE` | `false` | **disables admin sign-in entirely.** For public demo stands only |
| `OIDC_ISSUER` | — | the realm address, e.g. `https://auth.example.org/realms/main`. Set together with `OIDC_CLIENT_ID`, it enables provider sign-in |
| `OIDC_CLIENT_ID` | — | the client identifier |
| `OIDC_CLIENT_SECRET` | — | the client secret. Empty means a public client and PKCE-only sign-in |
| `OIDC_ADMIN_GROUP` | — | the group granting administrator rights |
| `OIDC_VIEWER_GROUP` | — | the group granting viewer rights |
| `OIDC_GROUPS_CLAIM` | `groups` | the claim carrying group membership |
| `OIDC_SCOPES` | `openid profile email` | the requested scopes. Groups need not be added here — see above |
| `OIDC_AUTO_LOGIN` | `false` | skip the sign-in screen and go straight to the provider |
| `OIDC_DISPLAY_NAME` | `OIDC` | the label on the sign-in button |
| `OIDC_REDIRECT_URL` | from `PUBLIC_BASE_URL` | the callback address, if the app cannot see its own external address |
| `OIDC_VERIFY_TLS` | `true` | verify the provider's certificate |
| `DATA_DIR` | `./data` (`/data` in the image) | the directory holding the SQLite database |
| `PUBLIC_BASE_URL` | from the request | the origin for subscription links and QR codes |
| `COOKIE_SECURE` | `false` | set `true` when the admin UI is behind HTTPS |
| `SESSION_TTL_HOURS` | `336` | admin session lifetime |
| `TRUST_FORWARDED_FOR` | `true` | take the client IP from `X-Forwarded-For` |
| `UPSTREAM_TIMEOUT` | `20` | timeout for the request to the panel, seconds |
| `UPSTREAM_MAX_BYTES` | `8388608` | maximum size of the panel's response |
| `UPSTREAM_PROXY` | — | HTTP/SOCKS proxy for requests to the panel |
| `UPSTREAM_VERIFY_TLS` | `true` | verify the panel's certificate |
| `LOG_LEVEL` | `INFO` | verbosity of the app's own log. At `INFO` the startup checks are visible — whether the OIDC provider answered |
| `LOG_RETENTION_DAYS` | `30` | log retention, days (`0` — do not purge by time) |
| `LOG_MAX_ROWS` | `20000` | maximum number of log records |

<br>

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/s/{token}` | **the public subscription link** — this is what goes into the client; the token belongs either to a profile or to an aggregate |
| `GET` | `/s/{token}/{any-suffix}` | the same; the suffix is ignored (some clients append a file name) |
| `GET` | `/probe/{token}` | **the trap link** for capturing client data |
| `GET` | `/auth/oidc/login` | start of provider sign-in |
| `GET` | `/auth/oidc/callback` | return from the provider |
| `GET` | `/healthz` | health check |
| `GET` | `/api/docs` | Swagger |

Internal, behind an administrator session: `/api/config` (read, `/validate`, apply), `/api/export`,
`/api/import`, `/api/templates`, `/api/aggregates`, `/api/probe`, `/api/filter/test`. A viewer only
gets `GET /api/profiles`, `GET /api/aggregates`, individual records and their QR codes — in a
trimmed form, without the panel address, the aggregate's composition or the settings.

The public link answers `404` both for a non-existent token and for a disabled profile or aggregate —
the response cannot be used to enumerate which tokens exist. A disabled profile never contacts the
panel at all.

<br>

## Interface

Material 3 Expressive, with no build step and no external fonts: one HTML, one CSS, two JS files
(the app and the help text). Light and dark themes, system by default.

Inside is detailed help with examples: how the proxying works, how device mimicry differs from
format substitution, the filter builder with a live sandbox against the real test endpoint, the
subscription formats, reading the log and troubleshooting common problems. It opens from the "?"
icon in the header on any tab. The last help section links here, to GitHub — for a viewer that is
the only way to find the project, since settings are out of reach; an administrator sees the same
link in settings, in the "About" card.

**Installable as an app.** The manifest, icons and service worker are in place: on Android and on
desktop Chrome/Edge will offer to install it (there is a button in settings too), on iOS it is "Add
to Home Screen". The shell is cached and opens offline. Installation requires HTTPS — over http://
the app works in full, but the browser will not create a shortcut.

The addresses of `app.js`, `help.js` and `styles.css` carry a content hash, and the page itself is
served with `no-cache`, so after a container update the browser cannot get stuck on an old version.

Deleting a profile or an aggregate does not ask for confirmation: the action is immediately
reversible, so instead of a dialog a snackbar appears with an "Undo" button and a circular
countdown — exactly as the Material 3 guidelines describe it. Beyond that window the record stays
recoverable for another day.

In the subscription link dialog the operating system is detected automatically from the browser but
can be switched by hand — each one shows import buttons for its specific clients.

There are five sections: "Profiles", "Aggregates", "Capture", "Logs", "Settings". A viewer sees the
first two — the rest are hidden in the UI and closed on the server.

* **Desktop** — a navigation rail on the left, centred dialogs, a two-column profile grid on wide screens.
* **Mobile** — bottom navigation, a bottom sheet that closes by dragging down, `safe-area-inset`
  accounted for around notches and the gesture bar, touch targets no smaller than 48 px, 16 px input
  fields (so iOS does not zoom the viewport), `overscroll-behavior: contain` in scrollable areas,
  `content-visibility` on long lists. The back gesture closes an open sheet rather than leaving the
  app, and does so through `CloseWatcher` — which is why recent Android versions show a gesture
  preview. Where `CloseWatcher` is missing, a history-entry fallback works without the preview.
* `prefers-reduced-motion` and `prefers-contrast` are honoured.

<br>

## Development

```bash
pytest -q
```

```bash
ruff check .
```

The tests cover parsing and reassembly of all four format families (including cascading group
removal), the condition builder's compiler, the protocol filter, all three HWID modes, header
forwarding, the end-to-end client request path against a stub upstream, authorisation, logging,
mimicry presets, client data capture, templates, cloning, soft deletion with restore, export/import
and the "validate — apply" cycle in the configuration editor. A separate suite covers aggregates:
merging within each format family with groups and rules re-pointed, deduplication, source captions,
behaviour with an unreachable source and with a source that answered in a foreign format. A separate
test compares the browser's regexp generator against the server's — so that the preview in the admin
UI cannot diverge from the real behaviour.

The frontend (`app/static/app.js`) is a plain script with no build step, but with its own Vitest
tests:

```bash
npm install
npm test
```

`app.js` exports nothing explicitly — the test loader (`tests-js/support/loadApp.js`) picks up every
top-level name automatically, so a new function becomes testable immediately, with no manual export
list. The tests cover the regexp generator, OS and host detection, syntax highlighting, session
resolution on a 401, the sign-in screen, admin/viewer role switching (including what a viewer really
sees on a card) and the aggregate editor: source order, picking from unused profiles, a deleted
source.

<br>

## Security

* There is a single administrator password, sessions are server-side, and the cookie is
  `HttpOnly`/`SameSite=Lax`; turn on `COOKIE_SECURE=true` behind HTTPS.
* Provider sign-in is authorization code with PKCE. The `id_token` is validated against the JWKS:
  signature, issuer, audience, expiry and the `nonce` that binds the token to one specific sign-in
  attempt. A started attempt is single-use: the `state` is deleted on first use, so a leaked
  callback address cannot be replayed. The `next` parameter is only accepted as a relative one — an
  open redirect on a sign-in page is exactly what makes a phishing link convincing.
* Viewer rights are trimmed on the server, not in the UI: the panel address, HWIDs and filters never
  enter the API response, so they cannot be pulled out of the browser around the interface.
* `DEMO_MODE=true` **removes sign-in entirely**: anyone who opens the app can change profiles, read
  the log and export the configuration along with the subscription tokens. The mode announces itself
  with a warning in the log at startup and an unremovable banner in the UI. So that a demo stand
  cannot be used as an open proxy into its own host's internal network, this mode forbids upstreams
  whose names resolve to private, loopback and special-use addresses. Do not enable it where real
  subscriptions live.
* Password guessing is rate-limited: 10 failed attempts from an address within 5 minutes and
  sign-in is blocked.
* Only an allow-list of request headers goes upstream and only an allow-list of response headers
  comes back down: the panel's `Set-Cookie`, its `Server` and the rest never reach the client.
* A subscription link can be reissued in one click — the old one stops working instantly.
* The original subscription's address is set by the administrator and is not restricted in any way:
  the panel often lives on an internal network, and banning internal addresses would break the
  ordinary self-hosting scenario. Access to the admin UI is therefore equivalent to the right to
  make the server fetch an arbitrary address — do not hand it to people you do not trust. The
  exception is `DEMO_MODE`, where that ban is in force.

<br>

## License

MIT.
