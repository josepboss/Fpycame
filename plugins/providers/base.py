"""Provider configuration shared with the admin dashboard."""
from __future__ import annotations

import configparser
import json
from pathlib import Path

PROVIDERS_CFG_PATH = Path('configs/providers_cfg.json')


def get_provider_cfg(name: str) -> dict:
    if not PROVIDERS_CFG_PATH.exists():
        return {}
    return json.loads(PROVIDERS_CFG_PATH.read_text(encoding='utf-8')).get(name, {})


def get_credentials(name: str) -> list[dict]:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read('configs/api_keys.cfg', encoding='utf-8')
    if name in cfg and cfg[name].get('api_key', '').strip():
        return [dict(cfg[name])]
    return get_provider_cfg(name).get('credentials', [])


def set_provider_domain(name: str, domain: str) -> None:
    cfg = json.loads(PROVIDERS_CFG_PATH.read_text(encoding='utf-8'))
    if domain not in cfg.get(name, {}).get('domains', []):
        raise ValueError('Domain is not configured for this provider')
    cfg[name]['active_domain'] = domain
    from admin.routes.providers import _save_json
    _save_json(str(PROVIDERS_CFG_PATH), cfg)
