# blackbull-htcpcp

A [BlackBull](https://github.com/TOKUJI/BlackBull) extension implementing HTCPCP — the Hyper Text Coffee Pot Control Protocol from [RFC 2324](https://datatracker.ietf.org/doc/html/rfc2324) (1998 April Fool's) and its tea-pot extension [RFC 7168](https://datatracker.ietf.org/doc/html/rfc7168) (2014 April Fool's).

[![PyPI](https://img.shields.io/pypi/v/blackbull-htcpcp.svg)](https://pypi.org/project/blackbull-htcpcp/)
[![Python](https://img.shields.io/pypi/pyversions/blackbull-htcpcp.svg)](https://pypi.org/project/blackbull-htcpcp/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

The joke is well-known.  The reason to actually ship it is that HTCPCP is a small, frozen RFC that uses every awkward corner of HTTP at once — a custom method, a custom status code, a custom content type, an application-defined request header with its own grammar, and error semantics that aren't really about HTTP itself.  Implementing it end-to-end in roughly 350 lines on top of an unmodified web framework makes a fine worked example for how far HTTP can be pushed as an application-protocol carrier.

Here is what the package actually puts on the wire:

```http
BREW /pot HTTP/1.1                ← the real RFC 2324 verb (POST still works as an alias)
Host: localhost:8000
Accept-Additions: cream; sugar

HTTP/1.1 200 OK
Content-Type: message/coffeepot

{"pot-type": "coffee", "state": "ready", "additions": ["cream", "sugar"]}
```

Ask a teapot to brew coffee, and it answers honestly:

```http
POST /pot HTTP/1.1
Host: localhost:8000

HTTP/1.1 418 I'm a teapot
Content-Type: message/coffeepot

{"error": "I'm a teapot", "pot-type": "teapot"}
```

All of it sits on BlackBull's documented [`init_app(app)`](https://github.com/TOKUJI/BlackBull/blob/master/docs/guide/extensions.md) extension surface — no framework fork, no monkey-patches.  See [`src/blackbull_htcpcp/extension.py`](src/blackbull_htcpcp/extension.py).

## Install

```bash
pip install blackbull-htcpcp
```

## Use

```python
from blackbull import BlackBull
from blackbull_htcpcp import HtcpcpExtension

app = BlackBull()
HtcpcpExtension(app=app, pot_type='coffee')   # or pot_type='teapot'

# Deferred form, if you configure the app elsewhere:
ext = HtcpcpExtension(pot_type='teapot')
ext.init_app(app)
```

After `init_app(app)` the live extension sits at `app.extensions['htcpcp']`.  Four routes (BREW / PROPFIND / WHEN / GET) are registered on `/pot`, a readiness route at `/pot/when`, plus an `app.on_error(418)` handler so any 418 the rest of the app emits gets the same `message/coffeepot` body.

## What it implements

- **BREW** — start brewing.  Registered as a first-class `BREW` route on `/pot` (BlackBull ≥ 0.42.1 dispatches arbitrary method tokens); `POST` is kept as a backwards-compatible alias.
- **PROPFIND** — inspect the pot's metadata.  Exposed via GET in the same window; native PROPFIND verb gated on the same framework change.
- **WHEN** — readiness check (GET `/pot/when`).
- **418 I'm a teapot** — RFC 2324 §2.2.2.  A teapot brewing anything that isn't tea returns 418.  Coffee pots return 200 for any brew request.
- **`message/coffeepot`** — content type on every `/pot` response, success or error.
- **Accept-Additions** — `cream; sugar; vanilla` etc.  Parsed, lower-cased, validated.

## Configuration

| Parameter | Default | Notes |
|---|---|---|
| `pot_type` | `'coffee'` | `'coffee'` or `'teapot'`.  Decides which Accept-Additions are valid and when 418 fires. |
| `capacity_ml` | `1500` | Reported via PROPFIND.  Doesn't enforce anything — the pot is metaphorical. |

## Security

HTCPCP is a joke; the parser isn't.  `Accept-Additions` is rejected when an addition token contains:

- CRLF or bare LF (response splitting),
- a NULL byte,
- non-printable control characters (other than horizontal tab — valid OWS per RFC 9110 §5.6.3),
- more than 256 characters,

or when the request carries more than 64 addition tokens.  BREW request bodies are capped at 1 MiB.  See [`tests/test_htcpcp.py`](tests/test_htcpcp.py) for the full S001–S015 matrix.

## How it fits

`blackbull-htcpcp` is the second external extension built on BlackBull's `init_app(app)` convention, after [`blackbull-session`](https://github.com/TOKUJI/blackbull-session).  It exists partly to prove that the extension surface can carry a non-trivial application protocol — custom methods, custom content types, custom error semantics — without modifications to the framework itself.  The cultural reference is IPoAC ([RFC 1149](https://datatracker.ietf.org/doc/html/rfc1149), implemented by Bergen Linux User Group in 2001): the joke RFCs are funny because they could actually be implemented, and implementing them teaches something about the layer underneath.

## License

[Apache License 2.0](LICENSE) — © TOKUJI.
