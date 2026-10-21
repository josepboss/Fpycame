"""Read-only connectivity checks for the Providers dashboard."""
from __future__ import annotations

import time

from .accounts import AccountSupplier


class HealthMonitor:
    def __init__(self, cardinal=None) -> None:
        self.cardinal = cardinal

    def run_checks(self) -> None:
        from admin.routes.providers import _load_json, _save_json, HEALTH_CACHE
        from plugins.api_delivery import hstore_request, smmcost_request
        cache = _load_json(HEALTH_CACHE)
        for name in ('hstore', 'dark', 'djekxa', 'smmcost'):
            previous = cache.get(name, {})
            online = False
            try:
                if name in ('dark', 'djekxa'):
                    client = AccountSupplier(name)
                    path = '/api/v1/user/balance' if name == 'dark' else '/api/v2/user/balance'
                    client.request('GET', path)
                    online = True
                elif name == 'hstore':
                    response = hstore_request('GET', '/api/v1/orders/lookup', query='external_order_id=health-check')
                    online = response is not None and response.status_code in (200, 404)
                else:
                    response = smmcost_request({'action': 'balance'})
                    online = response is not None and response.status_code == 200 and 'balance' in response.json()
            except Exception:
                pass
            previous['is_online'] = online
            previous['last_success_time' if online else 'last_error_time'] = time.time()
            from locales.localizer import Localizer
            previous['last_error_message'] = None if online else Localizer().translate('oi_provider_key', language='en')
            cache[name] = previous
        _save_json(HEALTH_CACHE, cache)
