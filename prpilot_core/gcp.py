"""Calling a private Cloud Run service as this service's own service account.

Cloud Run checks an identity token for the receiving service (audience = its URL). The OpenAI
client already uses `Authorization` for its API key, so the token goes in
`X-Serverless-Authorization`, which Cloud Run checks first and does not pass on. Tokens come
from the metadata server and are reused until five minutes before they expire (they last an
hour).
"""

import time

import httpx

ID_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
TOKEN_LIFETIME_S = 3600
REFRESH_EARLY_S = 300
HEADER = "X-Serverless-Authorization"


class CloudRunIdToken(httpx.Auth):
    def __init__(self, audience: str, http: httpx.AsyncClient | None = None, clock=time.monotonic):
        self.audience, self.clock = audience, clock
        self.http = http or httpx.AsyncClient(timeout=10)
        self._token, self._expires = "", 0.0

    async def _id_token(self) -> str:
        if self._token and self.clock() < self._expires - REFRESH_EARLY_S:
            return self._token
        r = await self.http.get(
            ID_TOKEN_URL, params={"audience": self.audience}, headers={"Metadata-Flavor": "Google"}
        )
        r.raise_for_status()
        self._token, self._expires = r.text, self.clock() + TOKEN_LIFETIME_S
        return self._token

    async def async_auth_flow(self, request: httpx.Request):
        request.headers[HEADER] = f"Bearer {await self._id_token()}"
        yield request
