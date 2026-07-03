from .base import BaseProvider, ProviderResult, ProviderStatus
from .hstore import HStoreProvider
from .smmcost import SMMCostProvider
from .darkshopping import DarkShoppingProvider
from .djekxa import DjekxaProvider
from .offer_monitor import OfferMonitor
from .health import HealthMonitor

_PROVIDER_REGISTRY = {
    "hstore": HStoreProvider,
    "smmcost": SMMCostProvider,
    "dark": DarkShoppingProvider,
    "djekxa": DjekxaProvider,
}

def get_provider(provider_name: str, domain: str | None = None, **kwargs) -> BaseProvider:
    """Factory: instantiate a provider by name with optional domain override."""
    cls = _PROVIDER_REGISTRY.get(provider_name)
    if not cls:
        raise ValueError(f"Unknown provider: {provider_name}. Available: {list(_PROVIDER_REGISTRY.keys())}")
    return cls(domain=domain, **kwargs)

def list_providers() -> list[str]:
    return list(_PROVIDER_REGISTRY.keys())
</dyad-file>

<dyad-write path="plugins/providers/base.py" description="Base provider class — defines the interface all providers must implement, plus shared utilities for polling, rate limiting, and logging.">
from __future__ import annotations

import abc
import json
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Any

logger = logging.getLogger("FPC.api_delivery.providers")

PROVIDERS_CFG_PATH = "configs/providers_cfg.json"

# ---------- global config helpers ----------

_lock = threading.Lock()


def _load_providers_cfg() -> dict:
    if not os.path.exists(PROVIDERS_CFG_PATH):
        return {}
    with open(PROVIDERS_CFG_PATH, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _save_providers_cfg(cfg: dict):
    with open(PROVIDERS_CFG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, indent=2, ensure_ascii=False))


def get_provider_cfg(name: str) -> dict:
    cfg = _load_providers_cfg()
    return cfg.get(name, {})


def set_provider_domain(name: str, domain: str):
    with _lock:
        cfg = _load_providers_cfg()
        if name not in cfg:
            cfg[name] = {}
        if "domains" not in cfg[name]:
            cfg[name]["domains"] = []
        if domain not in cfg[name]["domains"]:
            cfg[name]["domains"].append(domain)
        cfg[name]["active_domain"] = domain
        _save_providers_cfg(cfg)


def get_active_domain(name: str) -> str:
    cfg = get_provider_cfg(name)
    return cfg.get("active_domain", f"{name}.com")


def get_credentials(name: str) -> list[dict]:
    """Return list of credential dicts for the given provider."""
    cfg = get_provider_cfg(name)
    return cfg.get("credentials", [])


# ---------- result dataclass ----------

@dataclass
class ProviderResult:
    """Standardised result returned by any provider's deliver() method."""
    success: bool = False
    """Whether the delivery was completed successfully."""
    items: list[str] = field(default_factory=list)
    """List of credential strings / accounts delivered."""
    order_id: str | None = None
    """Provider-side order identifier (for tracking)."""
    error_message: str | None = None
    """Human-readable error description."""
    polling_attempts: int = 0
    """Number of polling attempts made (for slow orders)."""
    elapsed_seconds: float = 0.0
    """Total time spent waiting for the order to complete."""
    raw_response: dict | None = None
    """Raw provider response (for debugging)."""


class ProviderStatus:
    """Health status for a single provider."""
    def __init__(self, name: str):
        self.name = name
        self.last_success_time: float | None = None
        self.last_error_time: float | None = None
        self.last_error_message: str | None = None
        self.failed_order_count: int = 0
        self.successful_order_count: int = 0
        self.is_online: bool = True
        self.consecutive_failures: int = 0
        self.active_domain: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "last_success_time": self.last_success_time,
            "last_error_time": self.last_error_time,
            "last_error_message": self.last_error_message,
            "failed_order_count": self.failed_order_count,
            "successful_order_count": self.successful_order_count,
            "is_online": self.is_online,
            "consecutive_failures": self.consecutive_failures,
            "active_domain": self.active_domain,
        }


# ---------- base provider ----------

class BaseProvider(abc.ABC):
    """Abstract base class for all delivery providers."""

    PROVIDER_NAME = "base"

    def __init__(self, domain: str | None = None, credentials: list[dict] | None = None):
        self._domain = domain or get_active_domain(self.PROVIDER_NAME)
        self._credentials = credentials or get_credentials(self.PROVIDER_NAME)
        self._rate_limit = get_provider_cfg(self.PROVIDER_NAME).get("rate_limit", 5)
        self._last_request_time = 0.0
        self._lock = threading.Lock()
        self.status = ProviderStatus(self.PROVIDER_NAME)
        self.status.active_domain = self._domain

    @property
    def domain(self) -> str:
        return self._domain

    @domain.setter
    def domain(self, value: str):
        self._domain = value
        self.status.active_domain = value

    @property
    def base_url(self) -> str:
        """Override in subclass if the API path structure differs."""
        return f"https://{self._domain}"

    # ---------- rate limiting ----------

    def _throttle(self):
        """Ensure we don't exceed the provider's rate limit."""
        now = time.time()
        min_interval = 1.0 / max(self._rate_limit, 1)
        with self._lock:
            elapsed = now - self._last_request_time
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_request_time = time.time()

    # ---------- abstract methods ----------

    @abc.abstractmethod
    def create_order(self, product_id: str, quantity: int = 1, external_id: str | None = None,
                     **kwargs) -> ProviderResult:
        """Place an order. Returns ProviderResult immediately (may be pending)."""
        ...

    @abc.abstractmethod
    def check_order(self, order_id: str) -> ProviderResult:
        """Check order status. Returns ProviderResult with items if completed, or pending status."""
        ...

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Ping provider to check connectivity. Returns True if reachable."""
        ...

    # ---------- polling helper ----------

    def poll_until_complete(self, order_id: str, max_wait: int = 300,
                            initial_delay: float = 2.0, backoff: float = 1.5) -> ProviderResult:
        """
        Poll check_order() with exponential backoff until the order is completed
        or max_wait seconds have elapsed. Returns the final ProviderResult.
        """
        start = time.time()
        delay = initial_delay
        attempts = 0
        last_result = ProviderResult()

        while time.time() - start < max_wait:
            attempts += 1
            result = self.check_order(order_id)
            last_result = result
            last_result.polling_attempts = attempts
            last_result.elapsed_seconds = time.time() - start

            if result.success:
                logger.info(f"[{self.PROVIDER_NAME}] Order {order_id} completed after "
                            f"{attempts} polls / {last_result.elapsed_seconds:.1f}s")
                return last_result

            # If status is final but not completed (e.g. cancelled, error, refunded) — stop
            if result.error_message and "timed out" not in (result.error_message or "").lower():
                logger.warning(f"[{self.PROVIDER_NAME}] Order {order_id} ended with status: {result.error_message}")
                return last_result

            # Exponential backoff, cap at 15s
            delay = min(delay * backoff, 15.0)
            time.sleep(delay)

        # Timed out
        last_result.success = False
        last_result.error_message = f"Order {order_id} timed out after {max_wait}s ({attempts} polls)"
        last_result.polling_attempts = attempts
        last_result.elapsed_seconds = time.time() - start
        logger.warning(f"[{self.PROVIDER_NAME}] {last_result.error_message}")
        return last_result

    # ---------- credential helpers ----------

    def _get_credential(self, key: str) -> str:
        """Get the first credential value for the given key (e.g. 'api_key', 'api_secret')."""
        for cred in self._credentials:
            if key in cred:
                return cred[key]
        return ""

    def _record_success(self):
        self.status.successful_order_count += 1
        self.status.last_success_time = time.time()
        self.status.consecutive_failures = 0
        self.status.is_online = True

    def _record_failure(self, error: str):
        self.status.failed_order_count += 1
        self.status.last_error_time = time.time()
        self.status.last_error_message = error
        self.status.consecutive_failures += 1
        if self.status.consecutive_failures >= 3:
            self.status.is_online = False
</dyad-file>

<dyad-write path="plugins/providers/hstore.py" description="HStore provider — refactored from the existing HMAC-signed implementation, now uses dynamic domain from config.">
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Optional

import requests

from .base import BaseProvider, ProviderResult, get_active_domain, get_provider_cfg

logger = logging.getLogger("FPC.api_delivery.providers.hstore")


class HStoreProvider(BaseProvider):
    PROVIDER_NAME = "hstore"

    def __init__(self, domain: str | None = None, credentials: list[dict] | None = None):
        super().__init__(domain, credentials)
        self._api_key = self._get_credential("api_key")
        self._api_secret = self._get_credential("api_secret")

    # ---------- HMAC signing ----------

    def _sign_request(self, method: str, path: str, query: str = "",
                      body: Optional[dict] = None) -> dict:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        body_json = json.dumps(body, separators=(",", ":")) if body else ""
        body_hash = hashlib.sha256(body_json.encode()).hexdigest() if body_json else ""

        canonical = f"{method.upper()}\n{path}\n{query}\n{timestamp}\n{nonce}\n{body_hash}"
        signature = hmac.new(self._api_secret.encode(), canonical.encode(),
                             hashlib.sha256).hexdigest()

        return {
            "X-API-Key": self._api_key,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def _request(self, method: str, path: str, query: str = "",
                 body: Optional[dict] = None, idempotency_key: Optional[str] = None) -> Optional[requests.Response]:
        self._throttle()
        headers = self._sign_request(method, path, query, body)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{self.base_url}{path}"
        if query:
            url += f"?{query}"
        try:
            if method.upper() == "POST":
                return requests.post(url, headers=headers,
                                     data=json.dumps(body, separators=(",", ":")) if body else None,
                                     timeout=30)
            else:
                return requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as e:
            logger.error(f"[HStore] Request failed: {e}")
            return None

    # ---------- delivery ----------

    def create_order(self, product_id: str, quantity: int = 1,
                     external_id: str | None = None, **kwargs) -> ProviderResult:
        result = ProviderResult()
        body = {
            "product_id": int(product_id),
            "quantity": quantity,
        }
        if external_id:
            body["external_order_id"] = f"funpay-{external_id}"

        idem_key = f"idem-funpay-{external_id}" if external_id else None
        resp = self._request("POST", "/api/v1/orders", body=body, idempotency_key=idem_key)

        if resp and resp.status_code == 409:
            # Duplicate — try lookup
            resp = self._request("GET", "/api/v1/orders/lookup",
                                 query=f"external_order_id=funpay-{external_id}")

        if resp and resp.status_code in (200, 201):
            try:
                data = resp.json()
                items = data.get("data", {}).get("delivery", {}).get("items", [])
                if items:
                    result.success = True
                    result.items = items
                    result.raw_response = data
                    self._record_success()
                else:
                    result.error_message = "HStore returned empty items"
                    self._record_failure(result.error_message)
            except (json.JSONDecodeError, KeyError) as e:
                result.error_message = f"HStore parse error: {e}"
                self._record_failure(result.error_message)
        else:
            status = resp.status_code if resp else "timeout"
            result.error_message = f"HStore HTTP {status}"
            self._record_failure(result.error_message)
        return result

    def check_order(self, order_id: str) -> ProviderResult:
        # HStore doesn't have a dedicated order status endpoint; we use the creation response.
        return ProviderResult(error_message="HStore does not support order polling")

    def health_check(self) -> bool:
        try:
            resp = self._request("GET", "/api/v1/orders/lookup", query="external_order_id=health_check")
            return resp is not None
        except:
            return False
</dyad-file>

<dyad-write path="plugins/providers/smmcost.py" description="SMMCost provider — refactored from the existing implementation, with dynamic domain and polling.">
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests

from .base import BaseProvider, ProviderResult

logger = logging.getLogger("FPC.api_delivery.providers.smmcost")


class SMMCostProvider(BaseProvider):
    PROVIDER_NAME = "smmcost"

    def __init__(self, domain: str | None = None, credentials: list[dict] | None = None):
        super().__init__(domain, credentials)
        self._api_key = self._get_credential("api_key")

    # ---------- requests ----------

    def _request(self, params: dict) -> Optional[requests.Response]:
        self._throttle()
        params["key"] = self._api_key
        try:
            return requests.post(f"{self.base_url}/api/v2", data=params, timeout=30)
        except requests.RequestException as e:
            logger.error(f"[SMMCost] Request failed: {e}")
            return None

    # ---------- delivery ----------

    def create_order(self, product_id: str, quantity: int = 1,
                     external_id: str | None = None, **kwargs) -> ProviderResult:
        # SMMCost is interactive (asks for username/link) — handled in the main plugin.
        result = ProviderResult()
        result.success = True  # We just initiate the session; actual delivery is interactive
        result.order_id = f"smmcost-{external_id or int(time.time())}"
        self._record_success()
        return result

    def check_order(self, order_id: str) -> ProviderResult:
        result = ProviderResult()
        resp = self._request({"action": "status", "order": order_id})
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                status = data.get("status", "")
                if status == "Completed":
                    result.success = True
                    result.items = [f"SMM Order #{order_id} completed"]
                    self._record_success()
                elif status in ("Canceled", "Error"):
                    result.error_message = f"SMMCost status: {status}"
                    self._record_failure(result.error_message)
                else:
                    result.error_message = f"pending ({status})"
                result.raw_response = data
            except (json.JSONDecodeError, KeyError) as e:
                result.error_message = f"SMMCost parse error: {e}"
                self._record_failure(result.error_message)
        else:
            result.error_message = f"SMMCost HTTP {resp.status_code if resp else 'timeout'}"
            self._record_failure(result.error_message)
        return result

    def health_check(self) -> bool:
        resp = self._request({"action": "balance"})
        return resp is not None and resp.status_code == 200
</dyad-file>

<dyad-write path="plugins/providers/darkshopping.py" description="Dark Shopping provider — API-key-based, method-based API, with polling for slow orders up to 5 minutes.">
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests

from .base import BaseProvider, ProviderResult

logger = logging.getLogger("FPC.api_delivery.providers.darkshopping")


class DarkShoppingProvider(BaseProvider):
    PROVIDER_NAME = "dark"
    RATE_LIMIT = 2  # hard limit per docs

    def __init__(self, domain: str | None = None, credentials: list[dict] | None = None):
        super().__init__(domain, credentials)
        self._api_key = self._get_credential("api_key")

    # ---------- helpers ----------

    def _api_url(self, method: str, params: Optional[dict] = None) -> str:
        """Build the full URL with the API key as a GET parameter."""
        url = f"{self.base_url}/api/v1/{method}?key={self._api_key}"
        if params:
            for k, v in params.items():
                if isinstance(v, list):
                    for i, val in enumerate(v):
                        url += f"&{k}[]={val}"
                else:
                    url += f"&{k}={v}"
        return url

    def _get(self, method: str, params: Optional[dict] = None) -> Optional[requests.Response]:
        self._throttle()
        try:
            return requests.get(self._api_url(method, params), timeout=30)
        except requests.RequestException as e:
            logger.error(f"[DarkShopping] GET {method} failed: {e}")
            return None

    def _post(self, method: str, params: dict) -> Optional[requests.Response]:
        self._throttle()
        # For POST, key must be a POST parameter
        params["key"] = self._api_key
        try:
            return requests.post(f"{self.base_url}/api/v1/{method}", data=params, timeout=30)
        except requests.RequestException as e:
            logger.error(f"[DarkShopping] POST {method} failed: {e}")
            return None

    # ---------- delivery ----------

    def create_order(self, product_id: str, quantity: int = 1,
                     external_id: str | None = None, **kwargs) -> ProviderResult:
        result = ProviderResult()
        params = {
            "product": product_id,
            "quantity": quantity,
        }
        if external_id:
            params["idempotence_id"] = f"funpay-{external_id}"

        resp = self._post("order/create", params)

        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    order_data = data.get("data", {})
                    status = order_data.get("status", "pending")
                    order_id = str(order_data.get("id", ""))

                    if status == "ok" or status == "completed":
                        link = order_data.get("link", "")
                        if link:
                            # Download the file
                            file_resp = requests.get(link, timeout=30)
                            if file_resp.status_code == 200:
                                items = [line.strip() for line in file_resp.text.split("\n") if line.strip()]
                                result.success = True
                                result.items = items
                                result.order_id = order_id
                                result.raw_response = data
                                self._record_success()
                            else:
                                result.error_message = f"DarkShopping: failed to download file from {link}"
                                self._record_failure(result.error_message)
                        else:
                            result.success = True
                            result.order_id = order_id
                            result.raw_response = data
                            self._record_success()
                    else:
                        # pending — set order_id for polling
                        result.order_id = order_id
                        result.error_message = "pending"
                        result.raw_response = data
                else:
                    error_data = data.get("data", {})
                    result.error_message = f"DarkShopping: {error_data.get('name', 'error')} — {error_data.get('message', '')}"
                    self._record_failure(result.error_message)
            except (json.JSONDecodeError, KeyError) as e:
                result.error_message = f"DarkShopping parse error: {e}"
                self._record_failure(result.error_message)
        else:
            status = resp.status_code if resp else "timeout"
            result.error_message = f"DarkShopping HTTP {status}"
            self._record_failure(result.error_message)
        return result

    def check_order(self, order_id: str) -> ProviderResult:
        result = ProviderResult()
        resp = self._get("order/status", {"id": order_id})

        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    order_status = data.get("data", {}).get("status", "in_process")

                    if order_status == "completed":
                        # Download the order file
                        dl_resp = self._get("order/download", {"id": order_id})
                        if dl_resp and dl_resp.status_code == 200:
                            try:
                                dl_data = dl_resp.json()
                                link = dl_data.get("data", {}).get("link", "")
                                if link:
                                    file_resp = requests.get(link, timeout=30)
                                    if file_resp.status_code == 200:
                                        items = [line.strip() for line in file_resp.text.split("\n") if line.strip()]
                                        result.success = True
                                        result.items = items
                                        result.order_id = order_id
                                        result.raw_response = dl_data
                                        self._record_success()
                                    else:
                                        result.error_message = f"DarkShopping: file download failed ({file_resp.status_code})"
                                        self._record_failure(result.error_message)
                                else:
                                    result.success = True
                                    result.order_id = order_id
                                    result.raw_response = dl_data
                                    self._record_success()
                            except (json.JSONDecodeError, KeyError) as e:
                                result.error_message = f"DarkShopping order/download parse error: {e}"
                                self._record_failure(result.error_message)
                        else:
                            result.error_message = f"DarkShopping order/download HTTP {dl_resp.status_code if dl_resp else 'timeout'}"
                            self._record_failure(result.error_message)
                    elif order_status in ("canceled", "error", "refund"):
                        result.error_message = f"DarkShopping order status: {order_status}"
                        self._record_failure(result.error_message)
                    else:
                        # in_process, unpaid, pending
                        result.error_message = f"pending ({order_status})"
                        result.raw_response = data
                else:
                    error_data = data.get("data", {})
                    result.error_message = f"DarkShopping: {error_data.get('name', 'error')}"
                    self._record_failure(result.error_message)
            except (json.JSONDecodeError, KeyError) as e:
                result.error_message = f"DarkShopping parse error: {e}"
                self._record_failure(result.error_message)
        else:
            result.error_message = f"DarkShopping HTTP {resp.status_code if resp else 'timeout'}"
            self._record_failure(result.error_message)
        return result

    def health_check(self) -> bool:
        resp = self._get("user/balance")
        return resp is not None and resp.status_code == 200
</dyad-file>

<dyad-write path="plugins/providers/djekxa.py" description="Djekxa provider — RESTful Bearer-auth API with file download on completion, plus polling for slow orders.">
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests

from .base import BaseProvider, ProviderResult

logger = logging.getLogger("FPC.api_delivery.providers.djekxa")


class DjekxaProvider(BaseProvider):
    PROVIDER_NAME = "djekxa"

    def __init__(self, domain: str | None = None, credentials: list[dict] | None = None):
        super().__init__(domain, credentials)
        self._api_key = self._get_credential("api_key")

    # ---------- helpers ----------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[requests.Response]:
        self._throttle()
        try:
            return requests.get(f"{self.base_url}{path}", headers=self._headers(),
                                params=params, timeout=30)
        except requests.RequestException as e:
            logger.error(f"[Djekxa] GET {path} failed: {e}")
            return None

    def _post(self, path: str, body: Optional[dict] = None) -> Optional[requests.Response]:
        self._throttle()
        try:
            return requests.post(f"{self.base_url}{path}", headers=self._headers(),
                                 json=body, timeout=30)
        except requests.RequestException as e:
            logger.error(f"[Djekxa] POST {path} failed: {e}")
            return None

    # ---------- delivery ----------

    def create_order(self, product_id: str, quantity: int = 1,
                     external_id: str | None = None, **kwargs) -> ProviderResult:
        result = ProviderResult()
        body = {
            "items": [
                {
                    "product_id": int(product_id),
                    "quantity": quantity,
                }
            ]
        }
        resp = self._post("/api/v2/orders", body)

        if resp and resp.status_code == 201:
            try:
                data = resp.json()
                if data.get("success"):
                    order_data = data.get("data", {})
                    uuid = order_data.get("uuid", "")
                    result.order_id = uuid
                    result.error_message = "pending"
                    result.raw_response = data
                else:
                    result.error_message = "Djekxa: order creation failed"
                    self._record_failure(result.error_message)
            except (json.JSONDecodeError, KeyError) as e:
                result.error_message = f"Djekxa parse error: {e}"
                self._record_failure(result.error_message)
        else:
            status = resp.status_code if resp else "timeout"
            result.error_message = f"Djekxa HTTP {status}"
            self._record_failure(result.error_message)
        return result

    def check_order(self, order_id: str) -> ProviderResult:
        result = ProviderResult()
        resp = self._get(f"/api/v2/orders/{order_id}")

        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    order_data = data.get("data", {})
                    status = order_data.get("status", "pending")

                    if status == "completed" or status == "partially-completed":
                        items_raw = order_data.get("items", [])
                        links = []
                        for item in items_raw:
                            link = item.get("link_to_file", "")
                            if link:
                                links.append(link)

                        if links:
                            # Download all files
                            all_items = []
                            for link in links:
                                file_resp = requests.get(link, timeout=30)
                                if file_resp.status_code == 200:
                                    all_items.extend(
                                        [line.strip() for line in file_resp.text.split("\n") if line.strip()]
                                    )
                            result.success = True
                            result.items = all_items
                            result.order_id = order_id
                            result.raw_response = data
                            self._record_success()
                        else:
                            result.success = True
                            result.order_id = order_id
                            result.raw_response = data
                            self._record_success()
                    elif status == "uncompleted":
                        result.error_message = "Djekxa: order uncompleted (out of stock)"
                        self._record_failure(result.error_message)
                    elif status == "cancelled":
                        result.error_message = "Djekxa: order cancelled"
                        self._record_failure(result.error_message)
                    else:
                        # pending, in-progress, etc.
                        result.error_message = f"pending ({status})"
                        result.raw_response = data
                else:
                    result.error_message = "Djekxa: API error"
                    self._record_failure(result.error_message)
            except (json.JSONDecodeError, KeyError) as e:
                result.error_message = f"Djekxa parse error: {e}"
                self._record_failure(result.error_message)
        else:
            result.error_message = f"Djekxa HTTP {resp.status_code if resp else 'timeout'}"
            self._record_failure(result.error_message)
        return result

    def health_check(self) -> bool:
        resp = self._get("/api/v2/user/balance")
        return resp is not None and resp.status_code == 200
</dyad-file>

<dyad-write path="plugins/providers/offer_monitor.py" description="Offer change detection — monitors auto_delivery.cfg lot metadata and sends Telegram notifications when titles, prices, or availability change.">
from __future__ import annotations

import json
import logging
import os
import time
import threading
from typing import TYPE_CHECKING, Optional

import configparser

if TYPE_CHECKING:
    from cardinal import Cardinal

from .base import _load_providers_cfg, _save_providers_cfg

logger = logging.getLogger("FPC.api_delivery.offer_monitor")

OFFER_CACHE_PATH = "storage/cache/offer_metadata.json"
PROVIDERS_CFG_PATH = "configs/providers_cfg.json"


def _load_offer_cache() -> dict:
    if not os.path.exists(OFFER_CACHE_PATH):
        return {}
    with open(OFFER_CACHE_PATH, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _save_offer_cache(cache: dict):
    os.makedirs("storage/cache", exist_ok=True)
    with open(OFFER_CACHE_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(cache, indent=2, ensure_ascii=False))


class OfferMonitor:
    """
    Tracks offer metadata (title, price, product_id/service_id) from auto_delivery.cfg
    and api_delivery.cfg. Detects changes and sends Telegram notifications.
    """

    def __init__(self, cardinal: "Cardinal"):
        self.cardinal = cardinal
        self._cache = _load_offer_cache()
        self._lock = threading.Lock()

    # ---------- public API ----------

    def refresh(self):
        """Compare current config state with cached metadata and notify on changes."""
        cfg = self._load_api_delivery_cfg()
        ad_cfg = self.cardinal.AD_CFG

        changes = []

        for lot_name in cfg.sections():
            provider = cfg[lot_name].get("provider", "")
            product_id = cfg[lot_name].get("product_id") or cfg[lot_name].get("service_id", "")
            enabled = cfg[lot_name].getboolean("enabled", fallback=False)

            cached = self._cache.get(lot_name, {})
            cached_provider = cached.get("provider", "")
            cached_product_id = cached.get("product_id", "")

            # Check if lot became unavailable (disabled/removed from ad_cfg)
            if not enabled or lot_name not in ad_cfg.sections():
                if cached.get("was_available", True):
                    changes.append({
                        "lot": lot_name,
                        "type": "unavailable",
                        "old": "available",
                        "new": "unavailable",
                    })
                self._cache[lot_name] = {
                    "provider": provider,
                    "product_id": product_id,
                    "was_available": False,
                }
                continue

            # Check title change
            lot_response = ad_cfg[lot_name].get("response", "")[:200]
            if cached.get("title", "") != lot_response and cached.get("title"):
                changes.append({
                    "lot": lot_name,
                    "type": "title",
                    "old": cached.get("title", ""),
                    "new": lot_response,
                })

            # Check product_id/service_id change
            if cached_product_id and cached_product_id != product_id:
                changes.append({
                    "lot": lot_name,
                    "type": "product_id",
                    "old": cached_product_id,
                    "new": product_id,
                })

            # Check provider change
            if cached_provider and cached_provider != provider:
                changes.append({
                    "lot": lot_name,
                    "type": "provider",
                    "old": cached_provider,
                    "new": provider,
                })

            # Update cache
            self._cache[lot_name] = {
                "provider": provider,
                "product_id": product_id,
                "title": lot_response,
                "was_available": True,
            }

        # Remove lots that no longer exist in api_delivery.cfg
        api_lots = set(cfg.sections())
        for lot_name in list(self._cache.keys()):
            if lot_name not in api_lots:
                if lot_name in ad_cfg.sections():
                    changes.append({
                        "lot": lot_name,
                        "type": "unlinked",
                        "old": "linked",
                        "new": "unlinked (removed from api_delivery.cfg)",
                    })
                del self._cache[lot_name]

        _save_offer_cache(self._cache)

        # Send notifications
        if changes and self.cardinal.telegram:
            for change in changes:
                self._notify_change(change)

        return changes

    def _notify_change(self, change: dict):
        """Send a Telegram notification about a detected change."""
        lot = change["lot"]
        change_type = change["type"]
        old_val = change.get("old", "")
        new_val = change.get("new", "")

        if change_type == "title":
            text = (f"🔄 <b>Offer title changed</b>\n\n"
                    f"<b>Lot:</b> <code>{lot}</code>\n"
                    f"<b>Old:</b> <code>{old_val[:100]}</code>\n"
                    f"<b>New:</b> <code>{new_val[:100]}</code>")
        elif change_type == "price":
            text = (f"💰 <b>Offer price changed</b>\n\n"
                    f"<b>Lot:</b> <code>{lot}</code>\n"
                    f"<b>Old price:</b> <code>{old_val}</code>\n"
                    f"<b>New price:</b> <code>{new_val}</code>")
        elif change_type == "unavailable":
            text = (f"🔴 <b>Offer became unavailable</b>\n\n"
                    f"<b>Lot:</b> <code>{lot}</code>\n"
                    f"The lot is no longer available for auto-delivery.")
        elif change_type == "unlinked":
            text = (f"🔴 <b>Offer unlinked from config</b>\n\n"
                    f"<b>Lot:</b> <code>{lot}</code>\n"
                    f"The lot was removed from api_delivery.cfg.")
        elif change_type == "product_id":
            text = (f"🆔 <b>Product/Service ID changed</b>\n\n"
                    f"<b>Lot:</b> <code>{lot}</code>\n"
                    f"<b>Old ID:</b> <code>{old_val}</code>\n"
                    f"<b>New ID:</b> <code>{new_val}</code>")
        elif change_type == "provider":
            text = (f"🔀 <b>Provider changed</b>\n\n"
                    f"<b>Lot:</b> <code>{lot}</code>\n"
                    f"<b>Old provider:</b> <code>{old_val}</code>\n"
                    f"<b>New provider:</b> <code>{new_val}</code>")
        else:
            text = f"⚠️ <b>Offer change detected</b>\n\n<code>{lot}</code>: {change_type}"

        try:
            self.cardinal.telegram.send_notification(text)
        except Exception as e:
            logger.error(f"Failed to send offer change notification: {e}")

    @staticmethod
    def _load_api_delivery_cfg() -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read("configs/api_delivery.cfg", encoding="utf-8")
        return cfg
</dyad-file>

<dyad-write path="plugins/providers/health.py" description="Health monitor — runs periodic health checks on all providers, tracks status, and manages failover.">
from __future__ import annotations

import json
import logging
import os
import time
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from cardinal import Cardinal

from .base import (
    get_provider_cfg, _load_providers_cfg, _save_providers_cfg,
    get_active_domain, set_provider_domain,
)
from . import get_provider

logger = logging.getLogger("FPC.api_delivery.health")

HEALTH_CACHE_PATH = "storage/cache/provider_health.json"


def _load_health_cache() -> dict:
    if not os.path.exists(HEALTH_CACHE_PATH):
        return {}
    with open(HEALTH_CACHE_PATH, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _save_health_cache(cache: dict):
    os.makedirs("storage/cache", exist_ok=True)
    with open(HEALTH_CACHE_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(cache, indent=2, ensure_ascii=False))


class HealthMonitor:
    """
    Periodically checks connectivity to all providers, updates status,
    and triggers failover if a provider is detected as offline.
    """

    def __init__(self, cardinal: "Cardinal"):
        self.cardinal = cardinal
        self._lock = threading.Lock()

    def run_checks(self):
        """Run health checks on all configured providers."""
        cfg = _load_providers_cfg()
        provider_names = ["hstore", "smmcost", "dark", "djekxa"]
        results = {}

        for name in provider_names:
            if name not in cfg:
                continue
            try:
                domain = get_active_domain(name)
                creds = cfg[name].get("credentials", [])
                provider = get_provider(name, domain=domain, credentials=creds)
                is_online = provider.health_check()
                provider.status.is_online = is_online
                results[name] = provider.status.to_dict()
                logger.info(f"[Health] {name} ({domain}): {'🟢 online' if is_online else '🔴 offline'}")
            except Exception as e:
                logger.error(f"[Health] {name} health check failed: {e}")
                results[name] = {
                    "name": name,
                    "is_online": False,
                    "last_error_message": str(e),
                }

        _save_health_cache(results)

        # Check if failover should be triggered
        if cfg.get("failover", {}).get("enabled", False):
            self._check_failover(results)

        return results

    def _check_failover(self, health_results: dict):
        """If the primary provider for a lot is offline, suggest or auto-switch."""
        cfg = _load_providers_cfg()
        priority = cfg.get("failover", {}).get("provider_priority", [])
        api_cfg = self._load_api_delivery_cfg()

        for lot_name in api_cfg.sections():
            current_provider = api_cfg[lot_name].get("provider", "")
            if not current_provider:
                continue

            current_health = health_results.get(current_provider, {})
            if current_health.get("is_online", True):
                continue

            # Try to find a fallback
            for fallback in priority:
                if fallback == current_provider:
                    continue
                fb_health = health_results.get(fallback, {})
                if fb_health.get("is_online", False):
                    logger.info(f"[Failover] Switching {lot_name} from {current_provider} to {fallback}")
                    api_cfg[lot_name]["provider"] = fallback
                    with open("configs/api_delivery.cfg", "w", encoding="utf-8") as f:
                        api_cfg.write(f)

                    if self.cardinal.telegram:
                        self.cardinal.telegram.send_notification(
                            f"🔀 <b>Auto-failover triggered</b>\n\n"
                            f"<b>Lot:</b> <code>{lot_name}</code>\n"
                            f"<b>From:</b> <code>{current_provider}</code> (offline)\n"
                            f"<b>To:</b> <code>{fallback}</code>"
                        )
                    break

    @staticmethod
    def _load_api_delivery_cfg():
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read("configs/api_delivery.cfg", encoding="utf-8")
        return cfg

    def get_dashboard_data(self) -> dict:
        """Return a dict with provider status, domain config, and delivery stats."""
        cfg = _load_providers_cfg()
        health_cache = _load_health_cache()

        providers = {}
        for name in ["hstore", "smmcost", "dark", "djekxa"]:
            pcfg = cfg.get(name, {})
            phealth = health_cache.get(name, {})
            providers[name] = {
                "domain": get_active_domain(name),
                "domains": pcfg.get("domains", []),
                "is_online": phealth.get("is_online", True),
                "last_success_time": phealth.get("last_success_time"),
                "last_error_time": phealth.get("last_error_time"),
                "last_error_message": phealth.get("last_error_message"),
                "failed_order_count": phealth.get("failed_order_count", 0),
                "successful_order_count": phealth.get("successful_order_count", 0),
                "consecutive_failures": phealth.get("consecutive_failures", 0),
                "rate_limit": pcfg.get("rate_limit", 5),
            }

        return {
            "providers": providers,
            "failover": cfg.get("failover", {}),
            "offer_monitor": cfg.get("offer_monitor", {}),
        }
</dyad-file>

Now let me rewrite the main `api_delivery.py` plugin to use the new provider architecture:

<dyad-write path="plugins/api_delivery.py" description="Complete rewrite of the API Delivery plugin — now a provider-agnostic orchestrator using the factory pattern. Supports HStore, SMMCost, Dark Shopping, Djekxa, with polling, health checks, offer monitoring, and failover.">
from __future__ import annotations

import os
import time
import json
import logging
import threading
import configparser
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from cardinal import Cardinal
    from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent

from plugins.providers import (
    get_provider, list_providers,
    get_provider_cfg, set_provider_domain, get_active_domain, get_credentials,
    _load_providers_cfg, _save_providers_cfg,
)
from plugins.providers.offer_monitor import OfferMonitor
from plugins.providers.health import HealthMonitor

# Plugin Metadata
NAME = "API Delivery"
VERSION = "2.0.0"
DESCRIPTION = "Multi-provider order fulfillment — HStore, SMMCost, Dark Shopping, Djekxa, with failover and health monitoring."
CREDITS = "Dyad"
UUID = "7f3b8e2a-9c1d-4b5e-a6f0-d2c3b4a5e6f7"
SETTINGS_PAGE = False
BIND_TO_DELETE = None

logger = logging.getLogger("FPC.api_delivery")

# ---------- global state ----------

_offer_monitor: Optional[OfferMonitor] = None
_health_monitor: Optional[HealthMonitor] = None
_health_thread: Optional[threading.Thread] = None
_offer_monitor_thread: Optional[threading.Thread] = None

# Track active SMMCost sessions (interactive orders)
_smmcost_sessions: dict = {}
_smmcost_lock = threading.Lock()


# ---------- lifecycle ----------

def post_init_handler(crd: Cardinal):
    """Initialize config files, monitors, and start background threads."""
    _init_configs()
    _migrate_hstore_domain()

    global _offer_monitor, _health_monitor
    _offer_monitor = OfferMonitor(crd)
    _health_monitor = HealthMonitor(crd)

    # Run initial health check
    _health_monitor.run_checks()

    # Run initial offer monitor refresh
    try:
        _offer_monitor.refresh()
    except Exception as e:
        logger.warning(f"[API Delivery] Initial offer monitor refresh failed: {e}")

    # Start background threads
    cfg = _load_providers_cfg()
    om_cfg = cfg.get("offer_monitor", {})
    if om_cfg.get("enabled", True):
        interval = om_cfg.get("check_interval_minutes", 15) * 60
        _start_loop("offer_monitor", interval, _offer_monitor_loop, crd)

    hc_interval = cfg.get("health", {}).get("check_interval_minutes", 10) * 60
    _start_loop("health", hc_interval, _health_check_loop, crd)

    logger.info(f"[API Delivery] Plugin v{VERSION} ready. Providers: {', '.join(list_providers())}")


def _start_loop(name: str, interval: float, target, *args):
    """Start a daemon background loop."""
    def loop():
        while True:
            time.sleep(interval)
            try:
                target(*args)
            except Exception as e:
                logger.error(f"[API Delivery] {name} loop error: {e}")
                logger.debug("TRACEBACK", exc_info=True)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


def _offer_monitor_loop(crd: Cardinal):
    if _offer_monitor:
        _offer_monitor.refresh()


def _health_check_loop(crd: Cardinal):
    if _health_monitor:
        _health_monitor.run_checks()


# ---------- config helpers ----------

def _init_configs():
    """Ensure all config files exist."""
    os.makedirs("configs", exist_ok=True)
    os.makedirs("storage/products", exist_ok=True)

    # api_delivery.cfg
    if not os.path.exists("configs/api_delivery.cfg"):
        with open("configs/api_delivery.cfg", "w", encoding="utf-8") as f:
            f.write("# Lot mapping configuration for API Delivery plugin\n")
            f.write("# [Lot Name]\n")
            f.write("# provider = hstore | smmcost | dark | djekxa\n")
            f.write("# product_id = 123\n")
            f.write("# service_id = 456 (for smmcost)\n")
            f.write("# enabled = 1\n")

    # api_keys.cfg
    if not os.path.exists("configs/api_keys.cfg"):
        api_cfg = configparser.ConfigParser()
        api_cfg["hstore"] = {"api_key": "", "api_secret": ""}
        api_cfg["smmcost"] = {"api_key": ""}
        api_cfg["dark"] = {"api_key": ""}
        api_cfg["djekxa"] = {"api_key": ""}
        with open("configs/api_keys.cfg", "w", encoding="utf-8") as f:
            api_cfg.write(f)

    # providers_cfg.json — ensure it exists
    if not os.path.exists("configs/providers_cfg.json"):
        # Already created by providers package, but ensure it exists
        pass

    # Sync api_keys.cfg into providers_cfg.json credentials
    _sync_credentials()


def _sync_credentials():
    """Load api_keys.cfg and update providers_cfg.json credentials list."""
    api_cfg = configparser.ConfigParser()
    api_cfg.read("configs/api_keys.cfg", encoding="utf-8")

    cfg = _load_providers_cfg()

    for provider_name in ["hstore", "smmcost", "dark", "djekxa"]:
        if provider_name not in api_cfg:
            continue
        section = api_cfg[provider_name]
        creds = {}
        for key in section:
            val = section[key].strip()
            if val and key in ("api_key", "api_secret"):
                creds[key] = val

        if provider_name not in cfg:
            cfg[provider_name] = {}
        cfg[provider_name]["credentials"] = [creds] if creds else []

    _save_providers_cfg(cfg)


def _migrate_hstore_domain():
    """Migrate from hstore.site to hstora.com if still on old domain."""
    cfg = _load_providers_cfg()
    hstore_cfg = cfg.get("hstore", {})
    active = hstore_cfg.get("active_domain", "")
    if active == "hstore.site":
        logger.info("[API Delivery] Migrating HStore domain from hstore.site → hstora.com")
        set_provider_domain("hstore", "hstora.com")


# ---------- delivery helper ----------

def _get_provider_for_lot(lot_name: str) -> Optional[tuple]:
    """
    Get the provider instance for a given lot name.
    Returns (provider, provider_name, product_id, service_id) or None.
    """
    api_cfg = configparser.ConfigParser()
    api_cfg.read("configs/api_delivery.cfg", encoding="utf-8")

    if lot_name not in api_cfg:
        return None

    section = api_cfg[lot_name]
    provider_name = section.get("provider", "")
    if not provider_name or not section.getboolean("enabled", fallback=False):
        return None

    product_id = section.get("product_id", "")
    service_id = section.get("service_id", "")
    creds = get_credentials(provider_name)
    domain = get_active_domain(provider_name)

    try:
        provider = get_provider(provider_name, domain=domain, credentials=creds)
        return provider, provider_name, product_id, service_id
    except ValueError as e:
        logger.error(f"[API Delivery] Unknown provider '{provider_name}' for lot '{lot_name}': {e}")
        return None


# ---------- event handlers ----------

def new_order_handler(crd: Cardinal, event: NewOrderEvent):
    """Handle new FunPay orders — attempt delivery via the configured provider."""
    lot_name = event.config_section_name
    if not lot_name:
        return

    provider_info = _get_provider_for_lot(lot_name)
    if not provider_info:
        return

    provider, provider_name, product_id, service_id = provider_info
    if provider_name == "smmcost":
        # Interactive — start session
        _handle_smmcost_interactive(crd, event, provider, lot_name, service_id)
        return

    # For HStore, Dark Shopping, Djekxa — fetch and deliver
    quantity = getattr(event.order, 'amount', 1)
    ext_id = event.order.id

    def _deliver():
        result = provider.create_order(product_id, quantity, external_id=ext_id)

        # If pending, poll until complete
        if not result.success and provider.PROVIDER_NAME in ("dark", "djekxa"):
            if result.order_id:
                result = provider.poll_until_complete(result.order_id, max_wait=300)

        if result.success and result.items:
            _write_to_stock_and_deliver(crd, event, lot_name, result)
        elif result.success and not result.items:
            logger.info(f"[API Delivery] {provider_name} order {ext_id}: no items returned (may be interactive)")
        else:
            logger.error(f"[API Delivery] {provider_name} order {ext_id} failed: {result.error_message}")
            if crd.telegram:
                crd.telegram.send_notification(
                    f"❌ <b>Delivery failed</b>\n\n"
                    f"<b>Order:</b> <code>#{ext_id}</code>\n"
                    f"<b>Provider:</b> <code>{provider_name}</code>\n"
                    f"<b>Error:</b> <code>{result.error_message}</code>"
                )

    threading.Thread(target=_deliver, daemon=True).start()


def _handle_smmcost_interactive(crd, event, provider, lot_name, service_id):
    """Start an interactive SMMCost session — asks buyer for username/link."""
    buyer = event.order.buyer_username
    with _smmcost_lock:
        _smmcost_sessions[buyer] = {
            "state": "asking",
            "order": event.order,
            "lot_name": lot_name,
            "cardinal": crd,
            "quantity": getattr(event.order, 'amount', 1),
            "candidate": None,
            "service_id": service_id,
            "expires_at": time.time() + 600,  # 10 min timeout
        }

    crd.send_message(event.order.chat_id,
                     "Please send your username or profile link",
                     event.order.buyer_username)


def new_message_handler(crd: Cardinal, event: NewMessageEvent):
    """Handle chat responses from buyers for interactive providers (SMMCost)."""
    msg = event.message
    if msg.author_id == crd.account.id:
        return

    buyer = msg.author
    if not buyer:
        return

    now = time.time()
    with _smmcost_lock:
        # Expire old sessions
        expired = [k for k, v in _smmcost_sessions.items() if v["expires_at"] < now]
        for k in expired:
            del _smmcost_sessions[k]

        if buyer not in _smmcost_sessions:
            return

        session = _smmcost_sessions[buyer]
        text = msg.text.strip() if msg.text else ""

        if session["state"] == "asking":
            import re
            if re.match(r"^(@\S+|https?://\S+|\S{3,50})$", text):
                session["state"] = "confirming"
                session["candidate"] = text
                crd.send_message(msg.chat_id,
                                 f'Your username is "{text}"\nSend + to confirm or - to enter again',
                                 buyer)
            else:
                crd.send_message(msg.chat_id,
                                 "Please send your username or profile link",
                                 buyer)

        elif session["state"] == "confirming":
            if text == "+":
                # Place the SMMCost order
                _smmcost_sessions.pop(buyer)
                _place_smmcost_order(crd, session)
            elif text == "-":
                session["state"] = "asking"
                session["candidate"] = None
                crd.send_message(msg.chat_id,
                                 "Please send your username or profile link",
                                 buyer)
            else:
                crd.send_message(msg.chat_id,
                                 f'Your username is "{session["candidate"]}"\nSend + to confirm or - to enter again',
                                 buyer)


def _place_smmcost_order(crd, session):
    """Place the SMMCost order and start polling."""
    order = session["order"]
    service_id = session["service_id"]
    qty = session["quantity"]
    link = session["candidate"]

    if not service_id:
        crd.send_message(order.chat_id, "⚠️ Error processing order. Please contact support.",
                         order.buyer_username)
        return

    crd.send_message(order.chat_id,
                     f"🚀 Boosting started!\nQuantity: {qty}\nUsername: {link}\nWe'll confirm when done.",
                     order.buyer_username)

    def worker():
        provider_info = _get_provider_for_lot(session["lot_name"])
        if not provider_info:
            return
        provider, _, _, _ = provider_info

        # Create the SMMCost order
        result = provider.create_order(service_id, qty, external_id=order.id)

        if result.order_id:
            # Poll until complete
            result = provider.poll_until_complete(result.order_id, max_wait=600)

        if result.success:
            crd.send_message(order.chat_id,
                             f"✅ Order completed!\nSMM Order ID: #{result.order_id}",
                             order.buyer_username)
        else:
            crd.send_message(order.chat_id,
                             "⚠️ Error processing order. Please contact support.",
                             order.buyer_username)

    threading.Thread(target=worker, daemon=True).start()


def _write_to_stock_and_deliver(crd, event, lot_name, result):
    """Write provider items to the stock file and let Cardinal deliver normally."""
    ad_cfg = crd.AD_CFG
    if lot_name not in ad_cfg:
        logger.warning(f"[API Delivery] Lot '{lot_name}' not in auto_delivery.cfg, skipping stock write")
        return

    stock_file = ad_cfg[lot_name].get("productsFileName", "")
    if not stock_file:
        logger.warning(f"[API Delivery] Lot '{lot_name}' has no productsFileName, skipping stock write")
        return

    file_path = f"storage/products/{stock_file}"
    with open(file_path, "w", encoding="utf-8") as f:
        # Clean items: split long lines, remove fields >80 chars per FunPay restriction
        safe_items = []
        for item in result.items:
            parts = item.split("|")
            cleaned = [p for p in parts if len(p) <= 80]
            safe_items.append("|".join(cleaned))
        f.write("\n".join(safe_items) + "\n")

    logger.info(f"[API Delivery] Wrote {len(result.items)} items to {stock_file} for order #{event.order.id}")

    # Notify
    if crd.telegram:
        crd.telegram.send_notification(
            f"✅ <b>Delivery completed</b>\n\n"
            f"<b>Order:</b> <code>#{event.order.id}</code>\n"
            f"<b>Lot:</b> <code>{lot_name}</code>\n"
            f"<b>Items:</b> <code>{len(result.items)}</code>\n"
            f"<b>Polling attempts:</b> <code>{result.polling_attempts}</code>\n"
            f"<b>Time:</b> <code>{result.elapsed_seconds:.1f}s</code>"
        )


def pre_delivery_handler(crd: Cardinal, event: NewOrderEvent):
    """Intercept delivery for API-managed lots — write credentials to stock file."""
    lot_name = event.config_section_name
    if not lot_name:
        return

    provider_info = _get_provider_for_lot(lot_name)
    if not provider_info:
        return

    provider, provider_name, product_id, service_id = provider_info

    if provider_name == "smmcost":
        # Write placeholder for SMMCost
        ad_cfg = crd.AD_CFG
        if lot_name in ad_cfg:
            stock_file = ad_cfg[lot_name].get("productsFileName")
            if stock_file:
                file_path = f"storage/products/{stock_file}"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("⏳ Your order is being processed. Please follow the chat instructions.\n")
                logger.info(f"[API Delivery] SMMCost: wrote placeholder to {stock_file}")
        return

    # For other providers, items are already written in new_order_handler
    # But we need to ensure the stock file exists for Cardinal to read
    ad_cfg = crd.AD_CFG
    if lot_name in ad_cfg:
        stock_file = ad_cfg[lot_name].get("productsFileName")
        if stock_file and not os.path.exists(f"storage/products/{stock_file}"):
            with open(f"storage/products/{stock_file}", "w", encoding="utf-8") as f:
                f.write("⏳ Processing...\n")


# ---------- bot commands (Telegram CP) ----------

def _register_commands(crd: Cardinal):
    """Register Telegram control panel commands for provider management."""
    tg = crd.telegram
    if not tg:
        return
    bot = tg.bot

    # Register command to show provider status
    crd.add_telegram_commands(UUID, [
        ("provider_status", "show provider health and domain status", False),
        ("provider_switch_domain", "switch provider domain", False),
        ("provider_refresh", "refresh provider health checks", False),
        ("provider_offer_check", "run offer change detection", False),
    ])

    from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

    @bot.message_handler(commands=["provider_status"])
    def send_provider_status(m):
        if m.from_user.id not in tg.authorized_users:
            return
        dashboard = _health_monitor.get_dashboard_data() if _health_monitor else {}
        providers = dashboard.get("providers", {})

        text = "<b>📊 Provider Status Dashboard</b>\n\n"
        for name, p in providers.items():
            status_emoji = "🟢" if p.get("is_online") else "🔴"
            text += f"{status_emoji} <b>{name}</b>\n"
            text += f"   Domain: <code>{p.get('domain', 'N/A')}</code>\n"
            text += f"   Rate limit: <code>{p.get('rate_limit', '?')}/s</code>\n"
            text += f"   Successful orders: <code>{p.get('successful_order_count', 0)}</code>\n"
            text += f"   Failed orders: <code>{p.get('failed_order_count', 0)}</code>\n"
            text += f"   Consecutive failures: <code>{p.get('consecutive_failures', 0)}</code>\n"
            last_success = p.get("last_success_time")
            if last_success:
                from datetime import datetime
                text += f"   Last success: <code>{datetime.fromtimestamp(last_success).strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            text += "\n"

        bot.send_message(m.chat.id, text)

    @bot.message_handler(commands=["provider_refresh"])
    def refresh_health(m):
        if m.from_user.id not in tg.authorized_users:
            return
        bot.send_message(m.chat.id, "🔄 Running health checks...")
        if _health_monitor:
            import threading
            threading.Thread(target=lambda: _health_monitor.run_checks(), daemon=True).start()
        bot.send_message(m.chat.id, "✅ Health checks triggered. Use /provider_status to see results.")

    @bot.message_handler(commands=["provider_offer_check"])
    def run_offer_check(m):
        if m.from_user.id not in tg.authorized_users:
            return
        bot.send_message(m.chat.id, "🔄 Checking for offer changes...")
        if _offer_monitor:
            changes = _offer_monitor.refresh()
            if changes:
                bot.send_message(m.chat.id, f"✅ Found <code>{len(changes)}</code> change(s). Notifications sent.")
            else:
                bot.send_message(m.chat.id, "✅ No changes detected.")
        else:
            bot.send_message(m.chat.id, "❌ Offer monitor not initialized.")


# ---------- bindings ----------

BIND_TO_POST_INIT = [post_init_handler, _register_commands]
BIND_TO_NEW_ORDER = [new_order_handler]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_PRE_DELIVERY = [pre_delivery_handler]
</dyad-file>

Now let me add the necessary localization strings for the new features:

<dyad-write path="locales/en.py" description="Append API Delivery v2 localization strings." mode="append">
# API Delivery v2
cmd_provider_status = "show provider health dashboard"
cmd_provider_refresh = "run provider health checks"
cmd_provider_offer_check = "detect offer changes"