"""Memory-only token renewal; never replay an authenticated operation after failure."""

from datetime import timedelta
import threading

from quantpilot.packages.core.kis_paper import KisPaperError

# KIS issues a token at most once per minute and, inside the token's life, re-issuance
# returns the same token (observed 2026-09-12). A held token therefore stays usable when
# issuance is throttled; only a client without any token has to wait.
ISSUANCE_BACKOFF_SECONDS = 60
RENEWAL_SAFETY_MARGIN_SECONDS = 300


class RefreshingClient:
    def __init__(self, config, factory, clock):
        self._lock = threading.Lock()
        self._config = config
        self._factory = factory
        self._clock = clock
        self._client = factory(config)
        self._has_token = bool(getattr(config, "access_token", ""))
        self._renew_at = clock()
        self._retry_after = clock()

    @property
    def account_scope_fingerprint(self):
        return self._client.account_scope_fingerprint

    def invalidate(self):
        """The broker said the token is expired: re-issue at the next call.

        The issuance backoff still applies, so a throttled re-issue keeps the held
        token for at most one minute more before trying again.
        """
        with self._lock:
            self._renew_at = self._clock()

    def current_client(self):
        with self._lock:
            now = self._clock()
            if now >= self._renew_at:
                if now < self._retry_after:
                    if self._has_token:
                        return self._client
                    raise ValueError("token_refresh_backoff")
                self._retry_after = now + timedelta(seconds=ISSUANCE_BACKOFF_SECONDS)
                try:
                    token = self._client.request_access_token()
                except KisPaperError:
                    if self._has_token:
                        # Keep the held token; the next attempt waits out the backoff.
                        return self._client
                    raise
                self._client = self._factory(
                    self._config.with_access_token(token.access_token)
                )
                self._has_token = True
                renew_at = now + timedelta(
                    seconds=max(1, token.expires_in_seconds * 0.9)
                )
                expires_at = getattr(token, "expires_at", None)
                if expires_at is not None:
                    # expires_in is not anchored to now when an existing token is returned.
                    renew_at = min(
                        renew_at,
                        expires_at - timedelta(seconds=RENEWAL_SAFETY_MARGIN_SECONDS),
                    )
                self._renew_at = max(renew_at, now + timedelta(seconds=1))
            return self._client

    def __getattr__(self, name):
        return getattr(self.current_client(), name)
