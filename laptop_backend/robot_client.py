import httpx

from .config import settings


class RobotClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.pi_base_url).rstrip("/")
        self._client = self._new_client()

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=0.6))

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        except (httpx.ConnectError, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            await self._client.aclose()
            self._client = self._new_client()
            response = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        response.raise_for_status()
        return response.json()

    async def move(self, direction: str, speed: float) -> dict:
        return await self._request(
            "POST",
            "/move",
            json={"direction": direction, "speed": speed},
        )

    async def stop(self) -> dict:
        return await self._request("POST", "/stop")

    async def health(self) -> dict:
        return await self._request("GET", "/health")
