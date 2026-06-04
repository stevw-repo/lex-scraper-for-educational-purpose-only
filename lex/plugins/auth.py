"""Auth status checks. There is no login automation — login is interactive in
the persistent browser window; this plugin only validates and, if needed, waits.
"""

from __future__ import annotations

from .base import Plugin


class AuthPlugin(Plugin):
    def validate(self) -> bool:
        """Authoritative check: navigate to the app and confirm we land on the
        authenticated host. We deliberately do NOT trust JWT cookies for Lexis —
        it sets unrelated analytics JWTs whose expiry would cause false negatives
        (and needless re-logins)."""
        return self.session.is_logged_in()

    def ensure_logged_in(self) -> None:
        if not self.validate():
            self.session.wait_for_login()
