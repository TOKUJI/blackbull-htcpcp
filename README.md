# blackbull-htcpcp

HTCPCP (RFC 2324 + RFC 7168) extension for the [BlackBull](https://github.com/TOKUJI/BlackBull) ASGI framework.

[![PyPI](https://img.shields.io/pypi/v/blackbull-htcpcp.svg)](https://pypi.org/project/blackbull-htcpcp/)
[![Python](https://img.shields.io/pypi/pyversions/blackbull-htcpcp.svg)](https://pypi.org/project/blackbull-htcpcp/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

## What this is

A full implementation of the Hyper Text Coffee Pot Control Protocol
([RFC 2324](https://datatracker.ietf.org/doc/html/rfc2324), 1998 April Fool's)
and its tea-pot extension ([RFC 7168](https://datatracker.ietf.org/doc/html/rfc7168),
2014 April Fool's), packaged as a BlackBull extension.

Beyond the joke, the package is a worked example of layering a small
application protocol on top of HTTP — custom methods, custom status
codes, custom content type, custom error semantics — through BlackBull's
documented extension surface
([`init_app(app)`](https://github.com/TOKUJI/BlackBull/blob/master/docs/guide/extensions.md)),
in a few hundred lines.

What it implements:

- **BREW** request to start brewing (POST fallback in environments where
  the stdlib `http.HTTPMethod` enum rejects non-IANA verbs).
- **PROPFIND** request to inspect the pot.
- **WHEN** request to ask whether the beverage is ready.
- **418 I'm a teapot** — RFC 2324 §2.2.2.  Coffee pots brewing tea (or vice
  versa under RFC 7168) return 418.
- **`message/coffeepot`** content type on every `/pot` response.
- **Accept-Additions** header — `cream; sugar; vanilla` etc.  Validated
  against header-injection / oversize / control-char attacks.

## Install

```bash
pip install blackbull-htcpcp
```

## Use

```python
from blackbull import BlackBull
from blackbull_htcpcp import HtcpcpExtension

app = BlackBull()

# Eager — wire on construction.
HtcpcpExtension(app=app, pot_type='coffee')

# Or deferred.
ext = HtcpcpExtension(pot_type='teapot')
ext.init_app(app)
```

The extension registers four routes on `/pot` (BREW / PROPFIND / WHEN /
GET) plus `/pot/when`, and an `app.on_error(418)` handler.  After
`init_app(app)` the live extension is reachable at
`app.extensions['htcpcp']`.

## Configuration

| Parameter | Default | Notes |
|---|---|---|
| `pot_type` | `'coffee'` | `'coffee'` or `'teapot'`.  Determines which Accept-Additions are valid and when 418 fires. |
| `capacity_ml` | `1500` | Reported via PROPFIND.  Doesn't enforce anything — the pot is metaphorical. |

## Security

The Accept-Additions parser rejects:

- CRLF and bare LF (`response splitting`),
- NULL bytes,
- non-printable control characters (other than horizontal tab, valid OWS),
- addition tokens > 256 characters,
- more than 64 addition tokens per request.

BREW request bodies are capped at 1 MiB.  See `tests/test_htcpcp.py` for
the full security matrix (S001–S015).

## How it fits

`blackbull-htcpcp` is the second external extension on top of BlackBull's
`init_app(app)` convention (after `blackbull-session`).  It exists partly
to validate that the extension surface can carry a non-trivial application
protocol — custom methods, custom content types, custom error semantics —
without modifications to the framework.

## License

[Apache License 2.0](LICENSE) — © TOKUJI.
