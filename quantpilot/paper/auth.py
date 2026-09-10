"""Memory-only token renewal; never replay an authenticated operation after failure."""

from datetime import timedelta
import threading


class RefreshingClient:
    def __init__(self, config, factory, clock):
        self._lock = threading.Lock()
        self._config = config
        self._factory = factory
        self._clock = clock
        self._client = factory(config)
        self._renew_at = clock()
        self._retry_after = clock()

    @property
    def account_scope_fingerprint(self):
        return self._client.account_scope_fingerprint

    def current_client(self):
        with self._lock:
            now = self._clock()
            if now >= self._renew_at:
                if now < self._retry_after:
                    raise ValueError("token_refresh_backoff")
                self._retry_after = now + timedelta(seconds=60)
                token = self._client.request_access_token()
                self._client = self._factory(
                    self._config.with_access_token(token.access_token)
                )
                self._renew_at = now + timedelta(
                    seconds=max(1, token.expires_in_seconds * 0.9)
                )
            return self._client

    def __getattr__(self, name):
        return getattr(self.current_client(), name)
