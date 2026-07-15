"""HTCPCP extension for BlackBull (RFC 2324 + RFC 7168).

The extension follows BlackBull's ``init_app(app)`` convention.  It
registers four routes on ``/pot`` — BREW (POST fallback) for brewing,
PROPFIND (GET fallback) for inspection, WHEN (GET ``/pot/when``
fallback) for readiness, plus GET for browser-friendly state — and an
``app.on_error(418)`` handler so any other code path emitting 418
gets the same teapot JSON body.

The fallback methods (POST for BREW, GET for PROPFIND / WHEN) remain
for clients that cannot send non-standard verbs.  BlackBull 0.42.1
accepts any RFC 9110 §5.6.2 token as an HTTP method, so BREW,
PROPFIND, and WHEN are now registered as first-class routes alongside
their fallbacks.
"""
from __future__ import annotations

import json
from http import HTTPMethod, HTTPStatus
from typing import Any

from blackbull import BlackBull, Response, read_body
from blackbull.headers import Headers

from blackbull_htcpcp.methods import HtcpcpMethod


# RFC 2324 §2.2.1 — coffeepot content type.
COFFEEPOT_CONTENT_TYPE = 'message/coffeepot'

# RFC 2324 §2.2.3 — known coffee additions.
COFFEE_ADDITIONS = frozenset({
    'cream', 'sugar', 'vanilla', 'cinnamon',
    'syrup', 'whisky', 'rum', 'kahlua', 'aquavit',
})

# RFC 7168 §2.1.1 — known tea additions.
TEA_ADDITIONS = frozenset({
    'milk', 'lemon', 'honey', 'ginger', 'bergamot',
})

# Defensive caps — see tests/test_htcpcp.py S001–S007 for the threat
# model.  Each cap is small enough that a single misbehaving request
# can't pin a worker.
_MAX_BODY_BYTES = 1024 * 1024     # 1 MiB BREW body cap (S007).
_MAX_ADDITION_LEN = 256           # per addition token (S004).
_MAX_ADDITIONS = 64               # total additions per request (S005).


class HtcpcpExtension:
    """HTCPCP extension following BlackBull's ``init_app(app)`` convention.

    >>> # Eager — wire on construction.
    >>> HtcpcpExtension(app=app, pot_type='coffee')

    >>> # Deferred — useful when the app is configured elsewhere.
    >>> ext = HtcpcpExtension(pot_type='teapot')
    >>> ext.init_app(app)

    After ``init_app``:

    * ``app.extensions['htcpcp']`` is *self*.
    * Four routes on ``/pot`` (BREW / PROPFIND / WHEN / GET) and a
      readiness route on ``/pot/when`` are registered.
    * ``app.on_error(418)`` is wired to a JSON ``message/coffeepot``
      teapot response.

    Parameters
    ----------
    app:
        Optional BlackBull app for eager wiring.  When provided,
        ``init_app(app)`` is called from the constructor.
    pot_type:
        ``'coffee'`` (default) or ``'teapot'``.  RFC 2324: a coffee
        pot serving coffee returns 200; a teapot asked to brew
        coffee returns 418.  RFC 7168 lets a teapot brew tea (200
        when the request carries explicit tea additions).
    capacity_ml:
        Reported via PROPFIND.  Metaphorical; nothing is enforced.
    """
    #: Key under which the extension registers itself in
    #: ``app.extensions``.  Follows the ``blackbull-<name>`` →
    #: ``<name>`` convention; not configurable to avoid a
    #: collision-bypass loophole.
    extension_key: str = 'htcpcp'

    def __init__(
        self,
        app: BlackBull | None = None,
        *,
        pot_type: str = 'coffee',
        capacity_ml: int = 1500,
    ):
        if pot_type not in ('coffee', 'teapot'):
            raise ValueError(
                f"pot_type must be 'coffee' or 'teapot'; got {pot_type!r}")
        self._pot_type = pot_type
        self._capacity_ml = capacity_ml
        self._state = 'idle'           # idle | brewing | ready
        self._additions: list[str] = []

        # Hold handler references so the closures stay reachable.
        self._brew_handler: Any = None
        self._propfind_handler: Any = None
        self._when_handler: Any = None
        self._when_direct_handler: Any = None
        self._get_handler: Any = None

        if app is not None:
            self.init_app(app)

    # ------------------------------------------------------------------
    # init_app(app) — extension entry point
    # ------------------------------------------------------------------

    def init_app(self, app: BlackBull) -> None:
        """Wire the HTCPCP routes and error handler onto *app*."""
        existing = app.extensions.get(self.extension_key)
        if existing is not None and existing is not self:
            existing_origin = type(existing).__module__
            raise RuntimeError(
                f"app.extensions[{self.extension_key!r}] is already "
                f"registered by {existing_origin}. Cannot initialise "
                f"{type(self).__module__}.{type(self).__name__}."
            )

        app.on_error(HTTPStatus.IM_A_TEAPOT)(self._on_teapot_error)

        self._register_brew(app)
        self._register_propfind(app)
        self._register_when(app)

        @app.route(path='/pot', methods=[HTTPMethod.GET])
        async def get_pot(scope, receive, send):
            await send(self._build_response(HTTPStatus.OK))
        self._get_handler = get_pot

        app.extensions[self.extension_key] = self

    # ------------------------------------------------------------------
    # Route registration helpers
    # ------------------------------------------------------------------

    def _register_brew(self, app: BlackBull) -> None:
        """Register BREW on ``/pot``, keeping POST as a backwards-compatible alias."""
        @app.route(path='/pot', methods=[HtcpcpMethod.BREW, HTTPMethod.POST])
        async def brew_handler(scope, receive, send):
            # S007: cap the request body before we look at it.
            body = await read_body(receive)
            if len(body) > _MAX_BODY_BYTES:
                await send(self._error_response(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    'Request body too large',
                ))
                return

            additions, err = self._parse_accept_additions(scope)
            if err is not None:
                await send(err)
                return

            # Teapot discrimination — R005, S010.  RFC 7168: teapots
            # brew tea; explicit tea additions → 200, anything else
            # (no additions or coffee additions) → 418.
            if self._pot_type == 'teapot':
                has_coffee = any(a in COFFEE_ADDITIONS for a in additions)
                has_tea = any(a in TEA_ADDITIONS for a in additions)
                if not has_tea or has_coffee:
                    await send(self._teapot_response())
                    return

            # S006: brewing while brewing → 409.  The sync impl flips
            # idle→brewing→ready inside one call, so the guard only
            # fires if an async wrapper holds the state across awaits.
            if self._state == 'brewing':
                await send(self._build_response(
                    HTTPStatus.CONFLICT,
                    message='Already brewing',
                ))
                return

            self._state = 'brewing'
            self._additions = additions
            self._state = 'ready'

            await send(self._build_response(
                HTTPStatus.OK,
                message='Brewing complete',
                additions=self._additions,
            ))
        self._brew_handler = brew_handler

    def _register_propfind(self, app: BlackBull) -> None:
        """Register PROPFIND on ``/pot``."""
        @app.route(path='/pot', methods=[HtcpcpMethod.PROPFIND])
        async def propfind_handler(scope, receive, send):
            await send(Response(
                json.dumps({
                    'pot-type': self._pot_type,
                    'state': self._state,
                    'capacity-ml': self._capacity_ml,
                    'additions-supported': (
                        sorted(COFFEE_ADDITIONS)
                        if self._pot_type == 'coffee' else
                        sorted(TEA_ADDITIONS)
                    ),
                }),
                status=HTTPStatus.OK,
                content_type=COFFEEPOT_CONTENT_TYPE,
            ))
        self._propfind_handler = propfind_handler

    def _register_when(self, app: BlackBull) -> None:
        """Register WHEN: GET /pot/when always, plus WHEN /pot when supported."""
        @app.route(path='/pot/when', methods=[HTTPMethod.GET])
        async def when_handler(scope, receive, send):
            if self._state == 'ready':
                body = json.dumps({'ready': True, 'when': 'now'})
            elif self._state == 'brewing':
                body = json.dumps({'ready': False, 'seconds': 30})
            else:
                body = json.dumps({
                    'ready': False,
                    'when': 'never',
                    'scheduled': False,
                })
            await send(Response(
                body,
                status=HTTPStatus.OK,
                content_type=COFFEEPOT_CONTENT_TYPE,
            ))
        self._when_handler = when_handler

        @app.route(path='/pot', methods=[HtcpcpMethod.WHEN])
        async def when_direct(scope, receive, send):
            await when_handler(scope, receive, send)
        self._when_direct_handler = when_direct

    # ------------------------------------------------------------------
    # Accept-Additions parsing and validation
    # ------------------------------------------------------------------

    def _parse_accept_additions(
        self, scope: dict,
    ) -> tuple[list[str], Response | None]:
        """Parse and validate ``Accept-Additions``.

        Returns ``(additions, None)`` on success or
        ``([], error_response)`` on failure — the caller forwards the
        error response straight to send.
        """
        raw_headers = scope.get('headers', Headers([]))
        getter = getattr(raw_headers, 'get', None)
        if getter is not None:
            raw = getter(b'accept-additions', b'')
        else:
            raw = b''
            for name, value in raw_headers:
                if name.lower() == b'accept-additions':
                    raw = value
                    break
        additions_raw = raw.decode('ascii', errors='replace')
        additions = [a.strip().lower() for a in additions_raw.split(';') if a.strip()]

        for a in additions:
            if '\r' in a or '\n' in a:
                return [], self._error_response(
                    HTTPStatus.BAD_REQUEST,
                    'Invalid addition: CRLF rejected',
                )
            if '\x00' in a:
                return [], self._error_response(
                    HTTPStatus.BAD_REQUEST,
                    'Invalid addition: NULL byte rejected',
                )
            # RFC 9110 §5.6.3 — HTAB is valid OWS; everything else
            # below 0x20 is suspicious.
            if any(ord(c) < 0x20 and c != '\t' for c in a):
                return [], self._error_response(
                    HTTPStatus.BAD_REQUEST,
                    'Invalid addition: control chars rejected',
                )
            if len(a) > _MAX_ADDITION_LEN:
                return [], self._error_response(
                    HTTPStatus.BAD_REQUEST,
                    'Addition name too long',
                )

        if len(additions) > _MAX_ADDITIONS:
            return [], self._error_response(
                HTTPStatus.BAD_REQUEST,
                'Too many additions',
            )

        return additions, None

    # ------------------------------------------------------------------
    # Response builders
    # ------------------------------------------------------------------

    def _build_response(self, status: HTTPStatus, **extra) -> Response:
        """Build a ``message/coffeepot`` response with pot metadata."""
        body = {'pot-type': self._pot_type, 'state': self._state}
        body.update(extra)
        return Response(
            json.dumps(body),
            status=status,
            content_type=COFFEEPOT_CONTENT_TYPE,
        )

    def _error_response(self, status: HTTPStatus, message: str) -> Response:
        return Response(
            json.dumps({'error': message}),
            status=status,
            content_type=COFFEEPOT_CONTENT_TYPE,
        )

    def _teapot_response(self) -> Response:
        """RFC 2324 §2.2.2 — 418 I'm a teapot."""
        return Response(
            json.dumps({'error': "I'm a teapot", 'pot-type': 'teapot'}),
            status=HTTPStatus.IM_A_TEAPOT,
            content_type=COFFEEPOT_CONTENT_TYPE,
        )

    async def _on_teapot_error(self, _scope, _receive, send):
        """Registered via ``app.on_error(418)``."""
        await send(self._teapot_response())
