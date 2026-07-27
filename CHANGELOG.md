# Changelog

All notable changes to `blackbull-htcpcp` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [ZeroVer](https://0ver.org/) prior to a 1.0
commitment.

## [Unreleased]

## [0.4.0] — 2026-07-27

### Changed

- **Migrate to BlackBull v0.60.0 native `Connection` API.**  All route
  handlers and the `_parse_accept_additions` helper now use the typed
  `Connection` object (`conn.headers`, `conn.method`, …) instead of the
  raw ASGI `scope` dict (`scope.get('headers', …)`, `scope['method']`).
  The minimum BlackBull dependency is raised from `>= 0.42.1` to
  `>= 0.60.0`.

## [0.3.0] — 2026-07-16

### Added

- **Multi-instance support via a `path` parameter** (default `'/pot'`).
  All routes mount on the given path (readiness at `f'{path}/when'`),
  and the `app.extensions` key is derived per path, so one app can host
  e.g. an RFC 2324 coffee pot at `/pot` and an RFC 7168 teapot at
  `/teapot` side by side. Any absolute path is accepted — the extension
  is a building block, not a gatekeeper.

### Changed

- **`extension_key` is now per-instance**: `f'htcpcp:{path}'`
  (`'htcpcp:/pot'` for the default) instead of the single class-level
  `'htcpcp'`. Two instances on the *same* path still collide with the
  usual `RuntimeError`. Code reading `app.extensions['htcpcp']`
  directly must switch to the new key (undocumented surface, but noted
  for completeness).

## [0.2.0] — 2026-07-16

### Added

- **`HtcpcpMethod` — public `StrEnum` of the RFC 2324 §2.2 verbs**
  (`BREW`, `PROPFIND`, `WHEN`). Members compare equal to their plain
  string values and mix freely with `http.HTTPMethod` in BlackBull
  route registrations, so downstream consumers no longer import private
  constants or define their own.
- **`IM_A_TEAPOT` re-export** (= `http.HTTPStatus.IM_A_TEAPOT`, 418)
  for discoverability alongside `HtcpcpMethod`.

### Changed

- `extension.py` registers its routes via `HtcpcpMethod` members
  instead of private module-level strings (`_BREW` etc.). No wire-level
  behaviour change — `StrEnum` members are `str` subclasses.

### Fixed

- `blackbull_htcpcp.__version__` no longer drifts from `pyproject.toml`
  (it reported `0.1.0` on the `0.1.1` release): it now reads the
  installed distribution's version via `importlib.metadata`, the same
  single-source-of-truth convention BlackBull itself uses.

## [0.1.1] — 2026-06-26

### Changed

- `BREW`, `PROPFIND`, and `WHEN` are now registered as **first-class HTTP
  methods** on `/pot` (and `WHEN` on `/pot/when`), so `curl -X BREW …`
  reaches the brewing handler directly instead of being unreachable. The
  `POST` (BREW) and `GET` (PROPFIND / WHEN) fallbacks are retained as
  backwards-compatible aliases for clients that cannot send non-standard
  verbs.  This removes the `try/except HTTPMethod` workarounds that
  previously downgraded the RFC 2324 verbs to their IANA fallbacks.

### Fixed

- Custom RFC 2324 verbs no longer return *no response* / silently fall
  back to `POST`/`GET`; the registered route table now exposes the real
  methods.

### Requirements

- Minimum `blackbull` raised to **>= 0.42.1**, the first release whose
  router dispatches arbitrary RFC 9110 §5.6.2 method tokens.

## [0.1.0] — 2026-06-17

### Added

- Initial release.  `HtcpcpExtension` implementing RFC 2324 (HTCPCP) and
  RFC 7168 (HTCPCP-TEA) on top of BlackBull's
  [`init_app(app)`](https://github.com/TOKUJI/BlackBull/blob/master/docs/guide/extensions.md)
  extension convention.
- Registers BREW (POST fallback), PROPFIND (GET fallback), WHEN (GET
  `/pot/when` fallback), and GET routes on `/pot`.
- Registers an `app.on_error(418)` teapot handler.
- Accept-Additions parsing with header-injection / oversize / control-char
  validation (S001–S005 in the test matrix).
- 1 MiB BREW body cap (S007).
- 418 response with `message/coffeepot` content type, JSON body, no
  permissive cache headers (S010).
- Eager (`HtcpcpExtension(app=app, ...)`) and deferred
  (`ext = HtcpcpExtension(...); ext.init_app(app)`) construction styles
  both supported.
