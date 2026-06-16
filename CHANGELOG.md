# Changelog

All notable changes to `blackbull-htcpcp` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [ZeroVer](https://0ver.org/) prior to a 1.0
commitment.

## [Unreleased]

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
