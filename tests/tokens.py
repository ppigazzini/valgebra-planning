"""A token verifier for the command line's HTTP proxy test: one token, issued for the proxy.

The resource a token is issued for is read from ``PROXY_RESOURCE`` when a token is verified,
since the test picks the proxy's port.
"""

from __future__ import annotations

import os

from mcp.server.auth.provider import AccessToken

#: The tokens issued: the agent's.
ISSUED = frozenset({"agent"})


class Tokens:
    """Read ``agent`` as a token issued to the agent for the proxy, and no other token."""

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return what ``token`` was issued for, or None for a token never issued."""
        if token not in ISSUED:
            return None
        resource = os.environ["PROXY_RESOURCE"]
        return AccessToken(token=token, client_id="agent", scopes=[], resource=resource)


VERIFIER = Tokens()
