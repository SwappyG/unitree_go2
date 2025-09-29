from __future__ import annotations

from dataclasses import dataclass
import httpx
from pydantic import BaseModel, ValidationError
import typing as t

from arcana_go2.json_utils import JSONKey, JSONValue, JSONObject
from arcana_go2.http_utils import QueryParams, ParamSequence, ScalarParam
from arcana_go2.api_exception import APIException
from arcana_go2.logger import make_logger

lg = make_logger(__name__)

T = t.TypeVar("T", bound=BaseModel)


@dataclass
class FastAPIClientConfig:
    attempts: int = 3  # total attempts including the first
    backoff_base_seconds: float = 0.25  # exponential backoff base
    retry_on_status: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504)


class HTTPClient:
    def __init__(
        self,
        *,
        base_url: str,
        get_token: t.Callable[[], str | None] | None = None,
        timeout: float = 15.0,
        retry: FastAPIClientConfig | None = None,
        default_headers: t.Mapping[str, str] | None = None,
        verify_tls: bool | str = True,
    ) -> None:
        lg.debug(f"connecting to {base_url=}")
        self.base_url = base_url.rstrip("/")
        self._get_token = get_token if get_token is not None else (lambda: None)
        self._timeout = timeout
        self._retry = retry or FastAPIClientConfig()
        self._default_headers = dict(default_headers or {})
        self._verify_tls = verify_tls
        # Connect immediately (no explicit open())
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self._timeout,
            verify=self._verify_tls,
            headers=self._build_default_headers(),
        )

    async def __aenter__(self) -> "HTTPClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        lg.debug(f"closing connection to {self.base_url=}")
        await self._client.aclose()

    async def get(
        self, url: str, *, model: t.Type[T], params: QueryParams | None = None
    ) -> T | None:
        lg.debug(f"call GET at {url=} with\n{model=}\n{params=}")
        return await self._request("GET", url, model=model, params=params)

    async def post(
        self,
        url: str,
        *,
        model: t.Type[T],
        params: QueryParams | None = None,
        payload: JSONObject | None = None,
    ) -> T | None:
        lg.debug(f"call POST at {url=} with\n{model=}\n{params=}")
        return await self._request("POST", url, model=model, params=params, payload=payload)

    async def put(
        self,
        url: str,
        *,
        model: t.Type[T],
        params: QueryParams | None = None,
        payload: JSONObject | None = None,
    ) -> T | None:
        lg.debug(f"call PUT at {url=} with\n{model=}\n{params=}")
        return await self._request("PUT", url, model=model, params=params, payload=payload)

    async def patch(
        self,
        url: str,
        *,
        model: t.Type[T],
        params: QueryParams | None = None,
        payload: JSONObject | None = None,
    ) -> T | None:
        lg.debug(f"call PATCH at {url=} with\n{model=}\n{params=}")
        return await self._request("PATCH", url, model=model, params=params, payload=payload)

    async def delete(
        self,
        url: str,
        *,
        model: t.Type[T],
        params: QueryParams | None = None,
        payload: JSONObject | None = None,
    ) -> T | None:
        lg.debug(f"call DELETE at {url=} with\n{model=}\n{params=}")
        return await self._request("DELETE", url, model=model, params=params, payload=payload)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        model: t.Type[T],
        params: QueryParams | None = None,
        payload: JSONObject | None = None,
        headers: t.Mapping[str, str] | None = None,
        expected_status: t.Iterable[int] | int = (200, 201, 202, 204),
    ) -> T | None:
        hdrs = self._merge_headers(headers)
        expected = {expected_status} if isinstance(expected_status, int) else set(expected_status)

        last_err: Exception | None = None
        for attempt in range(1, self._retry.attempts + 1):
            try:
                resp = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    headers=hdrs,
                )
                if resp.status_code not in expected:
                    try:
                        detail = resp.json()
                    except Exception:
                        detail = resp.text
                    if (
                        resp.status_code in self._retry.retry_on_status
                        and attempt < self._retry.attempts
                    ):
                        self._sleep_backoff(attempt)
                        continue
                    raise APIException(
                        f"Unexpected status {resp.status_code}",
                        status_code=resp.status_code,
                        url=str(resp.request.url),
                        method=method,
                        detail=detail,
                        headers=resp.headers,
                    )
                if resp.status_code == 204 or not resp.content:
                    return None
                payload = resp.json()
                try:
                    return model.model_validate(payload)
                except ValidationError as ve:
                    raise APIException(
                        "Response validation failed",
                        status_code=resp.status_code,
                        url=str(resp.request.url),
                        method=method,
                        detail=ve.errors(),
                        headers=resp.headers,
                    ) from ve
            except (httpx.TransportError, httpx.ReadTimeout, httpx.PoolTimeout) as te:
                last_err = te
                if attempt < self._retry.attempts:
                    self._sleep_backoff(attempt)
                    continue
                raise APIException(
                    "Transport error",
                    url=f"{self.base_url}{url}",
                    method=method,
                    detail=str(te),
                ) from te
        raise APIException(
            "Request failed after retries",
            url=f"{self.base_url}{url}",
            method=method,
            detail=str(last_err),
        )

    def _build_default_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            **self._default_headers,
        }
        token = None
        try:
            token = self._get_token()
        except Exception as e:
            lg.warning(f"token provider raised: {e}", exc_info=e)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _merge_headers(self, extra: t.Mapping[str, str] | None) -> dict[str, str]:
        merged = self._build_default_headers()
        if extra:
            merged.update(extra)
        return merged

    def _sleep_backoff(self, attempt: int) -> None:
        import time

        delay = (2 ** (attempt - 1)) * self._retry.backoff_base_seconds
        lg.debug(
            f"retrying in {delay:.3f} (attempt {attempt+1}/{self._retry.attempts})",
        )
        time.sleep(delay)
