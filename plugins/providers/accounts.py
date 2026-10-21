"""Verified Djekxa v2 and Dark Shopping v1 APIs; never retry purchases."""
from __future__ import annotations

import re
import time
from threading import Lock
from urllib.parse import urlsplit, quote

import requests

from .base import get_provider_cfg, get_credentials

_request_lock = Lock()
_last_requests: dict[str, float] = {}

DOMAINS = {'dark': ('dark.shopping', 'darkstore.contact'), 'djekxa': ('djekxa.ru',)}


class SupplierError(ValueError):
    """Sanitized API failure; remote responses and keys must not reach logs."""


class SupplierReview(SupplierError):
    """A final/unsafe delivery outcome requiring an operator."""


class AccountSupplier:
    def __init__(self, name: str, domain: str | None = None) -> None:
        if name not in DOMAINS:
            raise SupplierError('Unsupported account supplier')
        self.name = name
        cfg = get_provider_cfg(name)
        self.domain = domain or cfg.get('active_domain') or DOMAINS[name][0]
        if self.domain not in DOMAINS[name]:
            raise SupplierError('Unsupported supplier domain')
        self.key = next((v['api_key'] for v in get_credentials(name) if v.get('api_key')), '')

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.key:
            raise SupplierError('oi_provider_key')
        with _request_lock:
            elapsed = time.monotonic() - _last_requests.get(self.name, 0)
            time.sleep(max(0, (0.55 if self.name == 'dark' else 0.25) - elapsed))
            _last_requests[self.name] = time.monotonic()
        data = dict(payload or {})
        headers = {'Accept': 'application/json'}
        if self.name == 'djekxa':
            headers['Authorization'] = f'Bearer {self.key}'
        else:
            data['key'] = self.key
        kwargs = {'params': data} if method == 'GET' else ({'json': data} if self.name == 'djekxa' else {'data': data})
        try:
            response = requests.request(method, f'https://{self.domain}{path}', headers=headers,
                                        timeout=(10, 45), allow_redirects=False, **kwargs)
            if response.status_code not in (200, 201):
                raise SupplierError(f'Supplier HTTP {response.status_code}')
            result = response.json()
            if not isinstance(result, dict) or result.get('success') is not True or not isinstance(result.get('data'), dict):
                raise SupplierError('Supplier response invalid or rejected')
            return result['data']
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, SupplierError):
                raise
            raise SupplierError('Supplier request failed') from None

    def product(self, product_id: int) -> dict:
        if self.name == 'djekxa':
            return self.request('GET', f'/api/v2/products/{product_id}')
        return self.request('GET', '/api/v1/product/view', {'id': product_id})

    def create_order(self, product_id: int, quantity: int, external_id: str) -> str:
        if self.name == 'djekxa':
            data = self.request('POST', '/api/v2/orders', {'items': [{'product_id': product_id, 'quantity': quantity}]})
            order_id = data.get('uuid')
        else:
            data = self.request('POST', '/api/v1/order/create', {'product': product_id,
                'quantity': quantity, 'idempotence_id': f'funpay-{external_id}'})
            order_id = data.get('id')
        if not order_id or not re.fullmatch(r'[A-Za-z0-9-]+', str(order_id)):
            raise SupplierError('Missing supplier order identity')
        return str(order_id)

    def order(self, order_id: str) -> dict:
        if self.name == 'djekxa':
            return self.request('GET', '/api/v2/orders/'+quote(order_id, safe=''))
        return self.request('GET', '/api/v1/order/status', {'id': order_id})

    def download(self, url: str) -> list[str]:
        parts = urlsplit(url)
        hosts = (*DOMAINS[self.name], 'cdn.djekxa.biz') if self.name == 'djekxa' else DOMAINS[self.name]
        if parts.scheme != 'https' or parts.netloc not in hosts:
            raise SupplierReview('Unexpected supplier download host')
        try:
            response = requests.get(url, timeout=(10, 45), allow_redirects=False)
            if response.status_code != 200 or 'html' in response.headers.get('Content-Type', '').lower():
                raise SupplierError('Supplier download unavailable')
            # Preserve every character in a record; normalize only line endings.
            items = [line for line in response.content.decode('utf-8-sig').splitlines() if line.strip()]
            if not items or any(len(line) > 100000 for line in items):
                raise SupplierReview('Supplier delivery file is empty or unsupported')
            return items
        except requests.RequestException:
            raise SupplierError('Supplier download failed') from None
        except UnicodeError:
            raise SupplierReview('Unsupported supplier file encoding') from None

    def items(self, order_id: str, data: dict, product_id: int, quantity: int) -> list[str]:
        if data.get('status') != 'completed':
            raise SupplierError('Supplier order incomplete')
        if self.name == 'djekxa':
            records = data.get('items', [])
            if len(records) != 1 or records[0].get('product_id') != product_id:
                raise SupplierReview('Supplier order product mismatch')
            links = [records[0].get('link_to_file')]
        else:
            links = [self.request('GET', '/api/v1/order/download', {'id': order_id}).get('link')]
        if not all(isinstance(link, str) and link for link in links):
            raise SupplierError('Supplier download not ready')
        items = [line for link in links for line in self.download(link)]
        if len(items) != quantity:
            raise SupplierReview('Supplier quantity mismatch; manual review required')
        return items


def get_provider(name: str, domain: str | None = None, **kwargs) -> AccountSupplier:
    return AccountSupplier(name, domain)
