"""Delivery configuration shared by manual lot mapping and HStore imports."""
from __future__ import annotations

import configparser
import hashlib
import os
import tempfile
from pathlib import Path
from threading import RLock

from locales.localizer import Localizer

API_CFG = Path('configs/api_delivery.cfg')
AUTO_CFG = Path('configs/auto_delivery.cfg')
PRODUCTS = Path('storage/products')
_lock = RLock()


class MappingConflict(ValueError):
    pass


def read_configs() -> tuple[configparser.ConfigParser, configparser.ConfigParser]:
    api = configparser.ConfigParser(interpolation=None)
    api.read(API_CFG, encoding='utf-8')
    auto = configparser.ConfigParser(delimiters=(':',), interpolation=None)
    auto.optionxform = str
    auto.read(AUTO_CFG, encoding='utf-8')
    return api, auto


def check_import_mapping(title: str, product_id: int) -> None:
    if not title.strip() or '\n' in title or '\r' in title:
        raise MappingConflict('Invalid mapping title')
    api, auto = read_configs()
    if title in api and (api[title].get('provider') != 'hstore' or
                         api[title].get('product_id') != str(product_id)):
        raise MappingConflict('Title already maps to another provider product')
    if title in auto:
        filename = auto[title].get('productsFileName', '')
        if filename and Path(filename).name != filename:
            raise MappingConflict('Unsafe existing product file')
        if auto[title].get('response') and '$product' not in auto[title]['response']:
            raise MappingConflict('Existing response lacks product placeholder')


def atomic_write(config: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            config.write(stream)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def save_delivery_mapping(title: str, provider: str, item_id: str, enabled: bool,
                          imported: bool = False) -> None:
    title = title.strip()
    if not title or '\n' in title or '\r' in title:
        raise MappingConflict('Invalid mapping title')
    with _lock:
        if imported:
            check_import_mapping(title, int(item_id))
        api, auto = read_configs()
        if title not in api:
            api.add_section(title)
        api[title]['provider'] = provider
        id_key, old_key = ('service_id', 'product_id') if provider == 'smmcost' else ('product_id', 'service_id')
        api[title][id_key] = item_id
        api[title].pop(old_key, None)
        api[title]['enabled'] = '1' if enabled else '0'
        if title not in auto:
            auto.add_section(title)
        if not auto[title].get('response'):
            auto[title]['response'] = Localizer().translate('oi_delivery_response', language='en')
        filename = auto[title].get('productsFileName')
        if not filename:
            safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in title)[:60].strip('_') or 'product'
            filename = f'{safe}_{hashlib.sha256(title.encode()).hexdigest()[:12]}.txt'
            auto[title]['productsFileName'] = filename
        if Path(filename).name != filename or '\\' in filename:
            raise MappingConflict('Unsafe product file name')
        PRODUCTS.mkdir(parents=True, exist_ok=True)
        # Preserve existing inventory: only create the stock file when absent.
        (PRODUCTS / filename).touch(exist_ok=True)
        if imported:
            auto[title]['disable'] = '0'
            # API goods are purchased on demand; an empty local file is normal.
            # Supplier availability belongs to the dedicated stock monitor.
            auto[title]['disableAutoDisable'] = '1'
            auto[title]['disableAutoRestore'] = '1'
        atomic_write(auto, AUTO_CFG)
        atomic_write(api, API_CFG)
