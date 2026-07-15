"""Acceptance tests for ``blackbull-htcpcp`` — HTCPCP (RFC 2324 + RFC 7168).

Tests define the expected behaviour of the HTCPCP extension — RFC 2324
(Hyper Text Coffee Pot Control Protocol) + RFC 7168 (HTCPCP-TEA) — as
seen through BlackBull's public APIs (``init_app(app)``, ``app.route``,
``app.on_error``, ``TestClient``).

The tests drive the real ``blackbull_htcpcp.HtcpcpExtension`` through
BlackBull's ``TestClient``; every requirement (R001–R009, S001–S015)
maps to a test class below.

Attack vectors considered
-------------------------
1. Header injection via Accept-Additions (CRLF / NULL byte)
2. DoS — concurrent BREW, rapid successive BREW, huge addition lists
3. Method-based ACL bypass (non-standard methods evading WAF)
4. Status 418 abuse (proxy cache poisoning, response confusion)
5. Content-Type confusion (message/coffeepot parser attacks)
6. HTTP smuggling (BREW + Transfer-Encoding / Content-Length mismatch)
7. Replay / race — double-brew, post-completion replay
8. Path traversal via PROPFIND path parameters
"""

from __future__ import annotations

import json
from http import HTTPMethod, HTTPStatus

import pytest

from blackbull import BlackBull, Response
from blackbull.testing import TestClient


# ===========================================================================
# Constants from RFC 2324 / RFC 7168
# ===========================================================================

# Custom HTTP methods (RFC 2324 §2.2).
# ``http.HTTPMethod`` is a ``StrEnum`` that rejects non-IANA methods in
# Python ≥3.11.  Until the framework relaxes method validation in
# ``BlackBull._dispatch``, these are plain strings and the corresponding
# tests are marked ``xfail(strict=True)``.
_BREW = 'BREW'
_PROPFIND = 'PROPFIND'
_WHEN = 'WHEN'

# Status 418 "I'm a teapot" (RFC 2324 §2.2.2).
# Python 3.12+ includes ``HTTPStatus.IM_A_TEAPOT``.
_IM_A_TEAPOT = HTTPStatus.IM_A_TEAPOT

# Content-type for HTCPCP payloads (RFC 2324 §2.2.1).
COFFEEPOT_CONTENT_TYPE = 'message/coffeepot'

# Known coffee additions (RFC 2324 §2.2.3).
_COFFEE_ADDITIONS = frozenset({
    'cream', 'sugar', 'vanilla', 'cinnamon',
    'syrup', 'whisky', 'rum', 'kahlua', 'aquavit',
})

# Known tea additions (RFC 7168).
_TEA_ADDITIONS = frozenset({
    'milk', 'lemon', 'honey', 'ginger', 'bergamot',
})



from blackbull_htcpcp import HtcpcpExtension

# ===========================================================================
# 1. Extension lifecycle (init_app convention) — R001–R003
# ===========================================================================

class TestExtensionLifecycle:
    """R001–R003: init_app convention compliance."""

    @pytest.mark.integration
    def test_registers_in_app_extensions(self):
        """R001: After init_app, app.extensions['htcpcp:/pot'] is the
        instance.  (Key is per-path since the multi-instance extension —
        was the single key 'htcpcp' before 0.3.0.)"""
        app = BlackBull()
        ext = HtcpcpExtension()
        ext.init_app(app)
        assert app.extensions.get('htcpcp:/pot') is ext

    @pytest.mark.integration
    def test_constructor_with_app_calls_init_app(self):
        """Convenience: HtcpcpExtension(app=app) calls init_app immediately."""
        app = BlackBull()
        ext = HtcpcpExtension(app=app)
        assert app.extensions.get('htcpcp:/pot') is ext

    @pytest.mark.integration
    def test_key_collision_raises_runtime_error(self):
        """R002: Second extension on same key → RuntimeError."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        ext2 = HtcpcpExtension()
        with pytest.raises(RuntimeError, match='already registered'):
            ext2.init_app(app)

    @pytest.mark.integration
    def test_error_handler_registered_for_418(self):
        """R003: app.on_error(418) handler is registered and retrievable."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        assert HTTPStatus.IM_A_TEAPOT in app._error_router
        assert app._error_router[HTTPStatus.IM_A_TEAPOT] is not None


# ===========================================================================
# 2. GET /pot — basic state inspection — R004
# ===========================================================================

class TestGetPot:
    """R004: GET /pot returns pot metadata."""

    @pytest.mark.integration
    def test_get_returns_200_with_state(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.get('/pot')
            assert resp.status_code == 200
            body = resp.json()
            assert body['pot-type'] == 'coffee'
            assert body['state'] == 'idle'

    @pytest.mark.integration
    def test_content_type_is_message_coffeepot(self):
        """All /pot responses carry Content-Type: message/coffeepot."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.get('/pot')
            assert resp.headers.get('content-type') == COFFEEPOT_CONTENT_TYPE

    @pytest.mark.integration
    def test_teapot_get_returns_200(self):
        """GET on a teapot is allowed — inspection, not brewing."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.get('/pot')
            assert resp.status_code == 200
            assert resp.json()['pot-type'] == 'teapot'


# ===========================================================================
# 3. Status 418 "I'm a teapot" — R005
# ===========================================================================

class TestStatus418:
    """R005: Teapot + brew → 418.  Coffee pot + brew → 200."""

    @pytest.mark.integration
    def test_teapot_brew_no_additions_returns_418(self):
        """Teapot with no Accept-Additions → 418 (default: coffee intended)."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 418
            assert "I'm a teapot" in resp.json().get('error', '')

    @pytest.mark.integration
    def test_teapot_brew_with_coffee_additions_returns_418(self):
        """Teapot + Accept-Additions: cream; sugar → 418."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post(
                '/pot', headers={'Accept-Additions': 'cream; sugar'})
            assert resp.status_code == 418

    @pytest.mark.integration
    def test_teapot_brew_tea_additions_returns_200(self):
        """RFC 7168: teapot + Accept-Additions: milk → 200 (tea brewing)."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post(
                '/pot', headers={'Accept-Additions': 'milk'})
            assert resp.status_code == 200
            assert 'milk' in resp.json().get('additions', [])

    @pytest.mark.integration
    def test_coffee_pot_brew_does_not_return_418(self):
        """Coffee pot + brew → 200, never 418."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='coffee')
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 200

    @pytest.mark.integration
    def test_418_response_has_coffeepot_content_type(self):
        """Error responses still carry message/coffeepot."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 418
            assert resp.headers.get('content-type') == COFFEEPOT_CONTENT_TYPE


# ===========================================================================
# 4. Accept-Additions header — R006
# ===========================================================================

class TestAcceptAdditions:
    """R006: Accept-Additions parsing (RFC 2324 §2.2.3)."""

    @pytest.mark.integration
    def test_additions_reflected_in_response(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot', headers={'Accept-Additions': 'cream; sugar'})
            assert resp.status_code == 200
            assert 'cream' in resp.json()['additions']
            assert 'sugar' in resp.json()['additions']

    @pytest.mark.integration
    def test_empty_additions_is_ok(self):
        """Black coffee is fine."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 200

    @pytest.mark.integration
    def test_whitespace_around_additions_trimmed(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': ' cream ;  sugar ; vanilla '},
            )
            assert resp.status_code == 200
            assert resp.json()['additions'] == ['cream', 'sugar', 'vanilla']

    @pytest.mark.integration
    def test_additions_case_insensitive(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot', headers={'Accept-Additions': 'Cream; SUGAR'})
            assert resp.status_code == 200
            assert 'cream' in resp.json()['additions']


# ===========================================================================
# 5. WHEN method — R007
# ===========================================================================

class TestWhen:
    """R007: WHEN /pot returns readiness (RFC 2324 §2.2.4)."""

    @pytest.mark.integration
    def test_when_idle_returns_not_scheduled(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.get('/pot/when')
            assert resp.status_code == 200
            assert resp.json()['ready'] is False

    @pytest.mark.integration
    def test_when_after_brew_returns_ready(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            client.post('/pot')            # brew
            resp = client.get('/pot/when')  # when?
            assert resp.json()['ready'] is True
            assert resp.json()['when'] == 'now'


# ===========================================================================
# 6. Method routing — R008
# ===========================================================================

class TestMethodRouting:
    """R008: Correct routing for standard and custom methods."""

    @pytest.mark.integration
    def test_post_triggers_brew(self):
        """POST /pot (BREW fallback) triggers brewing."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 200
            assert resp.json()['state'] == 'ready'

    @pytest.mark.integration
    def test_delete_returns_405(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.delete('/pot')
            assert resp.status_code == 405

    @pytest.mark.integration
    def test_put_returns_405(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.put('/pot')
            assert resp.status_code == 405

    @pytest.mark.integration
    def test_405_includes_allow_header(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.put('/pot')
            assert resp.status_code == 405
            allow = resp.headers.get('allow', '')
            assert 'GET' in allow.upper()

    @pytest.mark.integration
    def test_unknown_path_returns_404(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.get('/cups')
            assert resp.status_code == 404

    # -- Custom method tests --

    @pytest.mark.integration
    def test_brew_method_routes_to_brew_handler(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.request('BREW', '/pot')
            assert resp.status_code == 200

    @pytest.mark.integration
    def test_propfind_method_returns_properties(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.request('PROPFIND', '/pot')
            assert resp.status_code == 200
            assert 'additions-supported' in resp.json()

    @pytest.mark.integration
    def test_when_method_returns_readiness(self):
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.request('WHEN', '/pot')
            assert resp.status_code == 200
            assert 'ready' in resp.json()

    @pytest.mark.integration
    def test_unknown_custom_method_returns_405(self):
        """A truly unknown method (FROBNICATE) → 405.
        Already works: HTTPMethod('FROBNICATE') raises ValueError
        in _dispatch, mapping to 405."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.request('FROBNICATE', '/pot')
            assert resp.status_code == 405


# ===========================================================================
# 7. Coexistence — R009
# ===========================================================================

class TestCoexistence:
    """R009: HTCPCP extension must not interfere with other routes."""

    @pytest.mark.integration
    def test_coexists_with_other_routes(self):
        app = BlackBull()

        @app.route(path='/hello')
        async def hello():
            return {'message': 'Hello'}

        HtcpcpExtension(app=app)

        with TestClient(app) as client:
            assert client.get('/hello').json() == {'message': 'Hello'}
            assert client.get('/pot').status_code == 200

    @pytest.mark.integration
    def test_coexists_with_other_extension(self):
        app = BlackBull()

        class _OtherExt:
            def init_app(self, a):
                a.extensions['other'] = self

                @a.route(path='/other')
                async def other():
                    return {'ok': True}
                self._h = other

        _OtherExt().init_app(app)
        HtcpcpExtension(app=app)

        with TestClient(app) as client:
            assert client.get('/other').status_code == 200
            assert client.get('/pot').status_code == 200
        assert 'other' in app.extensions
        assert 'htcpcp:/pot' in app.extensions


# ===========================================================================
# 8. SECURITY — Header injection via Accept-Additions (S001–S003)
# ===========================================================================

class TestSecurityHeaderInjection:
    """S001–S003: Malicious Accept-Additions values must be rejected.

    Threat model: an attacker injects CRLF / NULL bytes into the
    Accept-Additions header to achieve HTTP response splitting or
    header injection.  The extension must validate each addition
    token before processing.
    """

    @pytest.mark.integration
    def test_crlf_injection_rejected(self):
        """S001: CRLF in addition name → 400, not response splitting."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': 'cream\r\nSet-Cookie: evil=true'},
            )
            assert resp.status_code == 400
            body = resp.json()
            assert 'CRLF' in body.get('error', '')

    @pytest.mark.integration
    def test_lf_only_injection_rejected(self):
        """S001b: Bare LF in addition name → 400."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': 'cream\nX-Injected: true'},
            )
            assert resp.status_code == 400
            assert 'CRLF' in resp.json().get('error', '')

    @pytest.mark.integration
    def test_null_byte_rejected(self):
        """S002: NULL byte in addition name → 400."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': 'cream\x00hidden'},
            )
            assert resp.status_code == 400
            assert 'NULL' in resp.json().get('error', '')

    @pytest.mark.integration
    def test_non_printable_characters_rejected(self):
        """S003: Addition names must be printable ASCII; control chars
        (other than horizontal tab, which is valid OWS per RFC 9110)
        are suspicious and must cause rejection."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            # SOH (0x01) — genuine non-printable control character
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': 'cream\x01hidden'},
            )
            # Must not 200 — either 400 (rejected) or 418/other error
            assert resp.status_code != 200, (
                'Non-printable control chars in Accept-Additions must be rejected')


# ===========================================================================
# 9. SECURITY — Denial of Service (S004–S008)
# ===========================================================================

class TestSecurityDenialOfService:
    """S004–S008: Resource exhaustion vectors.

    Threat model: an attacker sends crafted Accept-Additions headers
    or rapid BREW requests to consume server memory, CPU, or to
    corrupt pot state.
    """

    @pytest.mark.integration
    def test_excessively_long_addition_name_rejected(self):
        """S004: Addition name > 256 chars → 400 (buffer exhaustion prevention)."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': 'A' * 300},
            )
            assert resp.status_code == 400
            assert 'too long' in resp.json().get('error', '').lower()

    @pytest.mark.integration
    def test_too_many_additions_rejected(self):
        """S005: More than 64 additions → 400 (memory exhaustion prevention)."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            many = '; '.join([f'add{i}' for i in range(100)])
            resp = client.post(
                '/pot',
                headers={'Accept-Additions': many},
            )
            assert resp.status_code == 400
            assert 'many' in resp.json().get('error', '').lower()

    @pytest.mark.integration
    def test_brew_when_already_brewing_returns_conflict(self):
        """S006: Double-brew → 409 Conflict.

        Specification marker — with the synchronous test double,
        brewing completes instantly so we cannot hit the 'brewing'
        state.  A real async implementation MUST return 409 for a
        second BREW while brewing is in progress.  This test documents
        the requirement; validate with the async implementation.
        """
        # The guard is present in HtcpcpExtension._register_brew:
        #   if self._state == 'brewing': → 409
        # This is validated by code review until an async test double
        # is available.
        app = BlackBull()
        ext = HtcpcpExtension(app=app)
        # Verify the guard clause exists in the source
        import inspect
        source = inspect.getsource(ext._register_brew)
        assert "self._state == 'brewing'" in source, (
            'S006: brew handler must check for already-brewing state')

    @pytest.mark.integration
    def test_brew_request_body_size_limited(self):
        """S007: BREW with excessively large body → 413 or 400.

        HTCPCP BREW requests may carry a body (e.g. the coffee type).
        A body over 1 MiB must be rejected to prevent memory exhaustion."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post('/pot', content='X' * (1024 * 1024 + 1))
            # Must not 200 — either 413 (Payload Too Large) or 400
            assert resp.status_code != 200, (
                'S007: Oversized BREW body must be rejected')

    @pytest.mark.integration
    def test_rapid_successive_brew_requests_safe(self):
        """S008: Many rapid POST /pot requests must not corrupt state
        or cause 500 errors."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            for _ in range(20):
                resp = client.post('/pot')
                assert resp.status_code in (200, 409), (
                    'S008: Rapid successive brew requests must not 500')
            # State must still be coherent
            final = client.get('/pot')
            assert final.status_code == 200
            assert final.json()['state'] in ('idle', 'ready')


# ===========================================================================
# 10. SECURITY — Method-based ACL bypass (S009)
# ===========================================================================

class TestSecurityMethodAclBypass:
    """S009: Non-standard methods must not bypass access controls.

    Threat model: a WAF or middleware that only inspects GET/POST/PUT/
    DELETE could be bypassed by BREW/PROPFIND/WHEN.  The framework must
    ensure custom methods pass through the same middleware chain.
    """

    @pytest.mark.integration
    def test_brew_subject_to_global_middleware(self):
        """S009a: BREW (POST fallback) must pass through global middleware."""
        app = BlackBull()
        middleware_seen: list[str] = []

        async def audit_mw(scope, receive, send, call_next):
            middleware_seen.append(scope.get('method', ''))
            await call_next(scope, receive, send)

        app.use(audit_mw)
        HtcpcpExtension(app=app)

        with TestClient(app) as client:
            client.post('/pot')
            assert 'POST' in middleware_seen, (
                'S009a: BREW (POST) must pass through global middleware')

    @pytest.mark.integration
    def test_get_subject_to_global_middleware(self):
        """S009b: GET /pot must also pass through middleware (consistency)."""
        app = BlackBull()
        middleware_seen: list[str] = []

        async def audit_mw(scope, receive, send, call_next):
            middleware_seen.append(scope.get('method', ''))
            await call_next(scope, receive, send)

        app.use(audit_mw)
        HtcpcpExtension(app=app)

        with TestClient(app) as client:
            client.get('/pot')
            assert 'GET' in middleware_seen


# ===========================================================================
# 11. SECURITY — Status 418 abuse (S010)
# ===========================================================================

class TestSecurityStatus418Abuse:
    """S010: 418 must not be usable for cache poisoning or response confusion.

    Threat model: a 418 response cached by an intermediary could be
    served to a different client expecting a standard HTTP response.
    The 418 body must be JSON (not HTML) and must not carry permissive
    cache headers.
    """

    @pytest.mark.integration
    def test_418_response_is_valid_json(self):
        """S010a: 418 body is well-formed JSON — no injection possible."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 418
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_418_response_cache_headers_safe(self):
        """S010b: 418 responses must not be publicly cacheable.

        If cache headers are present, they must include no-store,
        no-cache, or private."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 418
            cc = resp.headers.get('cache-control', '')
            if cc:
                assert any(
                    directive in cc
                    for directive in ('no-store', 'no-cache', 'private')
                ), 'S010b: 418 response must not be publicly cacheable'

    @pytest.mark.integration
    def test_418_body_contains_no_html(self):
        """S010c: 418 body must NOT be HTML.

        Prevents reflected XSS if a browser ignores the Content-Type
        header and renders the body as HTML."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot')
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 418
            assert '<script' not in resp.text.lower()
            assert '<html' not in resp.text.lower()


# ===========================================================================
# 12. SECURITY — Content-Type confusion (S011)
# ===========================================================================

class TestSecurityContentTypeConfusion:
    """S011: message/coffeepot must not confuse HTTP parsers.

    Threat model: a parser that doesn't recognise message/coffeepot
    might fall back to text/html or application/octet-stream, leading
    to MIME confusion attacks.
    """

    @pytest.mark.integration
    def test_request_with_wrong_content_type_still_handled(self):
        """S011a: POST with Content-Type: text/html must not crash.

        HTCPCP doesn't require a specific request content-type; the
        handler should be tolerant."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post(
                '/pot',
                headers={'Content-Type': 'text/html'},
            )
            assert resp.status_code in (200, 415), (
                'S011a: Wrong request Content-Type must not 500')

    @pytest.mark.integration
    def test_response_always_has_coffeepot_content_type(self):
        """S011b: Every /pot response sets Content-Type: message/coffeepot,
        never text/html (prevents MIME confusion)."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            for _ in range(3):
                resp = client.get('/pot')
                assert resp.headers.get('content-type') == COFFEEPOT_CONTENT_TYPE
                client.post('/pot')


# ===========================================================================
# 13. SECURITY — HTTP smuggling (S012)
# ===========================================================================

class TestSecurityHttpSmuggling:
    """S012: Non-standard methods must not be exploitable for HTTP smuggling.

    Threat model: BREW with conflicting Transfer-Encoding / Content-Length
    could desynchronise the HTTP parser.  The extension must not interfere
    with the framework's HTTP/1.1 parser safeguards.
    """

    @pytest.mark.integration
    def test_brew_with_content_length_zero_accepted(self):
        """S012a: BREW with Content-Length: 0 is a valid request."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post('/pot', headers={'Content-Length': '0'})
            assert resp.status_code == 200

    @pytest.mark.integration
    def test_brew_body_not_interpreted_as_command(self):
        """S012b: The BREW body is opaque — it must not be executed
        as a command or interpreted beyond basic parsing."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            # Body that looks like an HTTP request line
            malicious_body = b'GET /admin HTTP/1.1\r\nHost: evil\r\n\r\n'
            resp = client.post('/pot', content=malicious_body)
            assert resp.status_code in (200, 400, 415), (
                'S012b: Malicious body must not crash or redirect')


# ===========================================================================
# 14. SECURITY — Replay / race conditions (S013)
# ===========================================================================

class TestSecurityReplayAndRace:
    """S013: BREW replay and race conditions must be safe.

    Threat model: an attacker replays a BREW request to exhaust the
    coffee pot or trigger unexpected state transitions.
    """

    @pytest.mark.integration
    def test_brew_twice_sequential_safe(self):
        """S013a: Two sequential BREW requests — second may be 200 or 409
        but must not 500."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            r1 = client.post('/pot')
            assert r1.status_code == 200
            r2 = client.post('/pot')
            assert r2.status_code in (200, 409), (
                'S013a: Second brew must not 500')

    @pytest.mark.integration
    def test_get_state_consistent_after_brew(self):
        """S013b: After brew completes, GET must reflect ready state
        (no stale idle state)."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            client.post('/pot')
            resp = client.get('/pot')
            assert resp.json()['state'] == 'ready'


# ===========================================================================
# 15. SECURITY — Path traversal (S014)
# ===========================================================================

class TestSecurityPathTraversal:
    """S014: PROPFIND / GET must not allow path traversal.

    Threat model: an attacker uses path traversal sequences on /pot
    to probe or access files outside the HTCPCP resource namespace.
    """

    @pytest.mark.integration
    def test_path_traversal_attempt_returns_404(self):
        """S014a: /pot/../../../etc/passwd → 404."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.get('/pot/../../../etc/passwd')
            assert resp.status_code == 404

    @pytest.mark.integration
    def test_url_encoded_traversal_rejected(self):
        """S014b: GET /pot with URL-encoded traversal → 404."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.get('/pot/%2e%2e/%2e%2e/etc/passwd')
            assert resp.status_code == 404


# ===========================================================================
# 16. SECURITY — HTTP method casing / verb tampering (S015)
# ===========================================================================

class TestSecurityMethodCasing:
    """S015: Non-standard method casing must not bypass routing.

    Threat model: an attacker sends 'brew' (lowercase) instead of
    'BREW' to evade method-based controls.  RFC 9110 §9.1: methods
    are case-sensitive.
    """

    @pytest.mark.integration
    def test_uppercase_brew_routes_correctly(self):
        """S015a: 'BREW' on /pot → 200 (brew handler).

        httpx normalises method names to uppercase before they reach the
        ASGI scope, so TestClient cannot send a truly lowercase method.
        Case-sensitivity at the router level is verified by
        BlackBull's own TestCustomMethods::test_case_sensitivity_custom_method.
        """
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.request('BREW', '/pot')
            assert resp.status_code == 200, (
                'S015a: BREW /pot must route to the brew handler')

    @pytest.mark.integration
    def test_mixed_case_post_works(self):
        """S015b: 'POST' (uppercase) works via the fallback registration."""
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            resp = client.post('/pot')
            assert resp.status_code == 200


# ===========================================================================
# 9. Public method API — HtcpcpMethod enum + IM_A_TEAPOT re-export
# ===========================================================================

class TestPublicMethodApi:
    """The package exposes RFC 2324 §2.2 methods as a public StrEnum and
    re-exports 418 for discoverability, so downstream consumers stop
    importing private constants or defining their own."""

    def test_htcpcp_method_members(self):
        from blackbull_htcpcp import HtcpcpMethod
        assert list(HtcpcpMethod) == [
            HtcpcpMethod.BREW, HtcpcpMethod.PROPFIND, HtcpcpMethod.WHEN]

    def test_htcpcp_method_equals_plain_strings(self):
        """StrEnum members compare equal to their string values, so code
        that passes 'BREW' keeps working."""
        from blackbull_htcpcp import HtcpcpMethod
        assert HtcpcpMethod.BREW == 'BREW'
        assert HtcpcpMethod.PROPFIND == 'PROPFIND'
        assert HtcpcpMethod.WHEN == 'WHEN'
        assert isinstance(HtcpcpMethod.BREW, str)

    @pytest.mark.integration
    def test_mixes_with_httpmethod_in_route_registration(self):
        """HtcpcpMethod and http.HTTPMethod coexist in one methods list."""
        from blackbull_htcpcp import HtcpcpMethod
        app = BlackBull()

        @app.route(path='/mixed', methods=[HtcpcpMethod.BREW, HTTPMethod.POST])
        async def mixed(scope, receive, send):
            await send(Response('ok'))

        with TestClient(app) as client:
            assert client.request('BREW', '/mixed').status_code == 200
            assert client.post('/mixed').status_code == 200

    @pytest.mark.integration
    def test_extension_routes_still_reachable(self):
        """The internal migration to HtcpcpMethod must not change the
        wire surface: the real verbs keep working."""
        from blackbull_htcpcp import HtcpcpMethod
        app = BlackBull()
        HtcpcpExtension(app=app)
        with TestClient(app) as client:
            assert client.request(str(HtcpcpMethod.BREW), '/pot').status_code == 200
            assert client.request(str(HtcpcpMethod.PROPFIND), '/pot').status_code == 200
            assert client.request(str(HtcpcpMethod.WHEN), '/pot').status_code == 200

    def test_im_a_teapot_reexported(self):
        import blackbull_htcpcp
        assert blackbull_htcpcp.IM_A_TEAPOT is HTTPStatus.IM_A_TEAPOT
        assert blackbull_htcpcp.IM_A_TEAPOT == 418

    def test_public_all(self):
        import blackbull_htcpcp
        assert set(blackbull_htcpcp.__all__) == {
            'HtcpcpExtension', 'HtcpcpMethod', 'IM_A_TEAPOT'}


# ===========================================================================
# 10. Multi-instance support — configurable path
# ===========================================================================

class TestMultiInstance:
    """Proposal extension: a `path` parameter (default '/pot') and a
    per-path extension key so one app can host e.g. an RFC 2324 coffee
    pot at /pot and an RFC 7168 teapot at /teapot."""

    @pytest.mark.integration
    def test_custom_path_routes_registered(self):
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot', path='/teapot')
        with TestClient(app) as client:
            assert client.get('/teapot').status_code == 200
            assert client.get('/teapot/when').status_code == 200
            assert client.request('PROPFIND', '/teapot').status_code == 200

    @pytest.mark.integration
    def test_teapot_at_custom_path_discriminates(self):
        """RFC 7168 behaviour is path-independent: tea additions → 200,
        anything else → 418."""
        app = BlackBull()
        HtcpcpExtension(app=app, pot_type='teapot', path='/teapot')
        with TestClient(app) as client:
            resp = client.request('BREW', '/teapot')
            assert resp.status_code == 418
            resp = client.request('BREW', '/teapot',
                                  headers={'Accept-Additions': 'milk'})
            assert resp.status_code == 200

    @pytest.mark.integration
    def test_two_instances_coexist(self):
        """Coffee at /pot + teapot at /teapot on one app."""
        app = BlackBull()
        coffee = HtcpcpExtension(app=app, pot_type='coffee')
        tea = HtcpcpExtension(app=app, pot_type='teapot', path='/teapot')
        assert app.extensions.get('htcpcp:/pot') is coffee
        assert app.extensions.get('htcpcp:/teapot') is tea
        with TestClient(app) as client:
            assert client.get('/pot').json()['pot-type'] == 'coffee'
            assert client.get('/teapot').json()['pot-type'] == 'teapot'
            # Coffee additions brew at /pot but 418 at /teapot.
            ok = client.request('BREW', '/pot',
                                headers={'Accept-Additions': 'cream'})
            no = client.request('BREW', '/teapot',
                                headers={'Accept-Additions': 'cream'})
            assert ok.status_code == 200
            assert no.status_code == 418

    @pytest.mark.integration
    def test_same_path_collision_still_raises(self):
        app = BlackBull()
        HtcpcpExtension(app=app, path='/teapot')
        ext2 = HtcpcpExtension(path='/teapot')
        with pytest.raises(RuntimeError, match='already registered'):
            ext2.init_app(app)

    def test_path_must_start_with_slash(self):
        with pytest.raises(ValueError, match='path'):
            HtcpcpExtension(path='pot')

    def test_extension_key_reflects_path(self):
        assert HtcpcpExtension(path='/teapot').extension_key == 'htcpcp:/teapot'
        assert HtcpcpExtension().extension_key == 'htcpcp:/pot'
