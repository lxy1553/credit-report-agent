from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CreditServiceError(RuntimeError):
    def __init__(self, path: str, retryable: bool):
        super().__init__(f"认证数据服务请求失败：{path}")
        self.retryable = retryable


class CreditClient:
    def __init__(self, base_url: str, bearer_token: str, timeout: float = 10.0):
        if not base_url.startswith(("http://", "https://")) or not bearer_token:
            raise ValueError("需要有效的服务地址和访问令牌")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Authorization": f"Bearer {self.bearer_token}",
                     "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            retryable = exc.code in {408, 429} or 500 <= exc.code < 600
            raise CreditServiceError(path, retryable) from exc
        except (URLError, TimeoutError) as exc:
            raise CreditServiceError(path, retryable=True) from exc

    def catalog(self):
        return self._request("/v1/catalog")

    def query_metric(self, metric_id: str, day: str):
        return self._request("/v1/metrics/query", {
            "metric_id": metric_id, "start_date": day, "end_date": day,
            "data_status": "FINAL", "group_by": ["biz_date"], "max_rows": 1,
        })
