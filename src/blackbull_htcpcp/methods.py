"""Public HTCPCP method enum (RFC 2324 §2.2)."""
from enum import StrEnum


class HtcpcpMethod(StrEnum):
    """HTCPCP custom methods (RFC 2324 §2.2).

    Being a ``StrEnum``, members compare equal to their string values
    and can be mixed with ``http.HTTPMethod`` in route registrations::

        methods=[HtcpcpMethod.BREW, HTTPMethod.POST]

    A standalone enum (rather than a subclass of ``http.HTTPMethod``)
    because Python forbids extending an enum that already has members —
    and it clearly signals "non-standard HTTP method" to the reader.
    """
    BREW = 'BREW'
    PROPFIND = 'PROPFIND'
    WHEN = 'WHEN'
