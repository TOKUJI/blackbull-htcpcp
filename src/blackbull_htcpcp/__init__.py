"""HTCPCP (RFC 2324 + RFC 7168) extension for BlackBull.

Public surface::

    from blackbull_htcpcp import HtcpcpExtension, HtcpcpMethod, IM_A_TEAPOT

See the package README for usage and configuration.
"""
from http import HTTPStatus
from importlib.metadata import version

from blackbull_htcpcp.extension import HtcpcpExtension
from blackbull_htcpcp.methods import HtcpcpMethod

#: RFC 2324 §2.2.2 — re-exported from ``http.HTTPStatus`` (stdlib since
#: Python 3.9) so it is discoverable alongside :class:`HtcpcpMethod`.
IM_A_TEAPOT = HTTPStatus.IM_A_TEAPOT  # 418

#: Single source of truth is ``pyproject.toml`` (same convention as
#: blackbull itself); re-run ``pip install -e .`` after a local bump.
__version__ = version("blackbull-htcpcp")

__all__ = ["HtcpcpExtension", "HtcpcpMethod", "IM_A_TEAPOT"]
