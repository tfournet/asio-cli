from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

from .config import AsioConfig, load_config


@dataclass
class Token:
    access_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class RateLimitError(Exception):
    """Raised when the API responds with a rate limit (HTTP 429)."""

    def __init__(self, retry_after: float, response: Optional[requests.Response] = None) -> None:
        super().__init__("Rate limit exceeded")
        self.retry_after = retry_after
        self.response = response


class AsioApiClient:
    """Thin wrapper around the ConnectWise Asio REST API."""

    def __init__(
        self,
        config: Optional[AsioConfig] = None,
        session: Optional[requests.Session] = None,
        *,
        login_debug: bool = False,
        login_logger: Optional[Callable[[str, Optional[Any]], None]] = None,
        http_debug: bool = False,
        http_logger: Optional[Callable[[str, Optional[Any]], None]] = None,
    ) -> None:
        self.config = config or load_config()
        self._session = session or requests.Session()
        self._token: Optional[Token] = None
        self._login_debug = login_debug
        self._login_logger = login_logger
        self._http_debug = http_debug
        self._http_logger = http_logger

    # ------------------------------------------------------------------
    # Authentication helpers
    def _authenticate(self) -> Token:
        data = self._request_token(self.config.scope.split(), store_token=False)
        expires_in = data.get("expires_in", 3600)
        token = Token(
            access_token=data["access_token"],
            expires_at=time.time() + max(0, int(expires_in) - 30),
        )
        self._token = token
        return token

    def _get_token(self) -> Token:
        if self._token is None or self._token.expired:
            return self._authenticate()
        return self._token

    # ------------------------------------------------------------------
    # Generic request helpers
    def _build_url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        path = path if path.startswith("/") else f"/{path}"
        return f"{base}{path}"

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None, json: Any = None) -> Dict[str, Any] | List[Any]:
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Accept": "application/json",
        }
        url = self._build_url(path)
        self._emit_http_request_debug(method, url, headers, params, json)
        response = self._session.request(method, url, params=params, json=json, headers=headers, timeout=30)
        self._emit_http_response_debug(response)
        if response.status_code == 429:
            raise RateLimitError(self._parse_retry_after(response), response=response)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    # ------------------------------------------------------------------
    # Debug helpers
    def _request_token(self, scopes: Iterable[str], *, store_token: bool = False) -> Dict[str, Any]:
        scope_str = " ".join(str(scope).strip() for scope in scopes if scope)
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        if scope_str:
            payload["scope"] = scope_str
        masked_payload = self._masked_payload(payload)
        self._emit_login_debug(f"POST {self.config.token_endpoint}", masked_payload)
        self._emit_http_request_debug(
            "POST",
            self.config.token_endpoint,
            {"Content-Type": "application/json"},
            None,
            masked_payload,
        )
        response = self._session.post(self.config.token_endpoint, json=payload, timeout=30)
        self._emit_login_debug("Token endpoint response status", response.status_code)
        self._emit_http_response_debug(response)  # ensure HTTP logger sees token response
        if response.status_code == 429:
            raise RateLimitError(self._parse_retry_after(response), response=response)
        response.raise_for_status()
        data = response.json()
        self._emit_login_debug("Token endpoint response body", self._masked_token_response(data))
        if store_token:
            expires_in = data.get("expires_in", 3600)
            token = Token(
                access_token=data["access_token"],
                expires_at=time.time() + max(0, int(expires_in) - 30),
            )
            self._token = token
        return data

    def test_scopes(self, scopes: Iterable[str]) -> tuple[bool, Any]:
        try:
            data = self._request_token(scopes, store_token=False)
            return True, self._masked_token_response(data)
        except requests.HTTPError as exc:
            detail: Any
            if exc.response is not None:
                try:
                    detail = exc.response.json()
                except ValueError:
                    detail = exc.response.text
            else:
                detail = str(exc)
            return False, detail
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def get_endpoint_detail(self, endpoint_id: str) -> Dict[str, Any]:
        path = f"/api/platform/v1/device/endpoints/{endpoint_id}"
        return self._get(path)

    def _emit_login_debug(self, message: str, payload: Optional[Any] = None) -> None:
        if not self._login_debug or self._login_logger is None:
            return
        self._login_logger(message, payload)

    def _masked_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        masked = dict(payload)
        secret = masked.get("client_secret")
        if secret:
            masked["client_secret"] = self._mask_secret(secret)
        return masked

    def _masked_token_response(self, data: Any) -> Any:
        if isinstance(data, dict):
            masked: Dict[str, Any] = {}
            for key, value in data.items():
                if key in {"access_token", "refresh_token", "token"} and isinstance(value, str):
                    masked[key] = self._mask_token(value)
                else:
                    masked[key] = self._masked_token_response(value)
            return masked
        if isinstance(data, list):
            return [self._masked_token_response(item) for item in data]
        return data

    @staticmethod
    def _mask_secret(secret: str) -> str:
        if not secret:
            return secret
        if len(secret) <= 4:
            return "*" * len(secret)
        prefix = secret[:2]
        suffix = secret[-2:]
        return f"{prefix}{'*' * (len(secret) - 4)}{suffix}"

    @staticmethod
    def _mask_token(token: str) -> str:
        if not token:
            return token
        if len(token) <= 8:
            return "*" * len(token)
        prefix = token[:4]
        suffix = token[-4:]
        return f"{prefix}{'*' * (len(token) - 8)}{suffix}"

    # HTTP debug helpers
    def set_http_debug(
        self,
        enabled: bool,
        logger: Optional[Callable[[str, Optional[Any]], None]],
    ) -> None:
        self._http_debug = enabled
        self._http_logger = logger

    def _emit_http_request_debug(
        self,
        method: str,
        url: str,
        headers: Dict[str, Any],
        params: Optional[Dict[str, Any]],
        json_body: Any,
    ) -> None:
        if not self._http_debug or self._http_logger is None:
            return
        payload = {
            "method": method,
            "url": url,
            "headers": self._masked_headers(headers),
            "params": params,
            "json": json_body,
        }
        self._http_logger("REQUEST", payload)

    def _emit_http_response_debug(self, response: requests.Response) -> None:
        if not self._http_debug or self._http_logger is None:
            return
        content_type = response.headers.get("Content-Type", "")
        body: Any
        if "json" in content_type.lower():
            try:
                body = self._masked_token_response(response.json())
            except ValueError:
                body = response.text
        else:
            body = response.text
        payload = {
            "status": response.status_code,
            "url": response.url,
            "headers": self._masked_headers(dict(response.headers)),
            "body": body,
        }
        self._http_logger("RESPONSE", payload)

    def _masked_headers(self, headers: Dict[str, Any]) -> Dict[str, Any]:
        masked = dict(headers)
        for key in list(masked.keys()):
            if key.lower() == "authorization":
                value = str(masked[key])
                masked[key] = self._mask_authorization(value)
        return masked

    def _mask_authorization(self, value: str) -> str:
        if not value:
            return value
        if value.lower().startswith("bearer "):
            token = value[7:]
            return f"Bearer {self._mask_token(token)}"
        return value

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return 1.0
        try:
            return float(retry_after)
        except ValueError:
            return 1.0

    def _get(self, path: str, **kwargs: Any) -> Dict[str, Any] | List[Any]:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> Dict[str, Any] | List[Any]:
        return self._request("POST", path, **kwargs)

    # ------------------------------------------------------------------
    # Public API surface
    def list_companies(self) -> List[Dict[str, Any]]:
        data = self._get("/api/platform/v1/company/companies")
        if isinstance(data, dict) and "companies" in data:
            return data["companies"]
        return data if isinstance(data, list) else []

    def list_company_sites(self, company_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/api/platform/v1/company/companies/{company_id}/sites")
        if isinstance(data, dict) and "sites" in data:
            return data["sites"]
        return data if isinstance(data, list) else []

    def list_company_endpoints(self, client_id: str) -> List[Dict[str, Any]]:
        data = self._get(f"/api/platform/v1/device/clients/{client_id}/endpoints")
        if isinstance(data, dict) and "endpoints" in data:
            return data["endpoints"]
        return data if isinstance(data, list) else []

    def list_scripts(self) -> List[Dict[str, Any]]:
        data = self._get("/api/platform/v1/automation/scripts")
        if isinstance(data, dict) and "scripts" in data:
            return data["scripts"]
        return data if isinstance(data, list) else []

    def schedule_script(
        self,
        *,
        template_id: str,
        template_type: str,
        endpoint_ids: Iterable[str],
        name: Optional[str] = None,
        resources_type: str = "Both",
        schedule: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Schedule a script execution on one or more endpoints."""
        payload = {
            "name": name or "Automation Task",
            "templateType": template_type,
            "templateID": template_id,
            "targets": list(endpoint_ids),
            "targetType": "MANAGED_ENDPOINT",
            "resourcesType": resources_type,
            "schedule": schedule
            or {
                "regularity": "Immediate",
                "category": "STZ",
                "scheduleType": "TIME",
            },
        }
        return self._post("/api/platform/v1/automation/endpoints/schedule-tasks", json=payload)

    def get_task_instances_summary(self, task_id: str) -> Dict[str, Any]:
        path = f"/api/platform/v1/automation/tasks/{task_id}/instances/summary"
        return self._get(path)

    def get_task_instance_results(self, task_id: str, instance_id: str) -> Dict[str, Any]:
        path = f"/api/platform/v1/automation/tasks/{task_id}/instances/{instance_id}/results"
        return self._get(path)


__all__ = ["AsioApiClient", "RateLimitError"]
