import typing as t

class APIException(Exception):
    """Represents an HTTP/API-level failure with rich context."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        method: str | None = None,
        detail: t.Any = None,
        headers: t.Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.method = method
        self.detail = detail
        self.headers = dict(headers or {})

    def __str__(self) -> str:  # pragma: no cover
        base = super().__str__()
        if self.status_code:
            base += f" [status={self.status_code}]"
        if self.url:
            base += f" url={self.url}"
        return base