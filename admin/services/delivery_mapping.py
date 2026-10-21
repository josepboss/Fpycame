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


def check_import_mapping(title: str, product_id: int, provider: str = 'hstore') -> None:
    if not title.strip() or '\n' in title or '\r' in title:
        raise MappingConflict('Invalid mapping title')
    api, auto = read_configs()
    if title in api and (api[title].get('provider') != provider or
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
                          imported: bool = False, delivery_format: str | None = None) -> None:
    if delivery_format is not None and delivery_format.strip():
        from Utils.delivery_tools import format_spec
        format_spec(delivery_format.strip())
    title = title.strip()
    if not title or '\n' in title or '\r' in title:
        raise MappingConflict('Invalid mapping title')
    with _lock:
        if imported:
            check_import_mapping(title, int(item_id), provider)
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
        if delivery_format is not None:
            auto[title]['deliveryFormat'] = delivery_format.strip()
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


def alias_delivery_mapping(old_title: str, new_title: str, product_id: int, provider: str = 'hstore') -> None:
    """Retain the old name for pending orders while adding the supplier's new name."""
    if old_title == new_title:
        return
    with _lock:
        check_import_mapping(new_title, product_id, provider)
        api, auto = read_configs()
        if new_title in auto and new_title not in api:
            raise MappingConflict('New title has an unrelated delivery configuration')
        if old_title not in api or old_title not in auto:
            raise MappingConflict('Original delivery mapping missing')
        if api[old_title].get('provider') != provider or api[old_title].get('product_id') != str(product_id):
            raise MappingConflict('Original mapping points to another product')
        # Never overwrite an independently configured response/inventory file.
        if new_title not in auto:
            auto[new_title] = dict(auto[old_title])
        if new_title not in api:
            api[new_title] = dict(api[old_title])
        atomic_write(auto, AUTO_CFG)
        atomic_write(api, API_CFG)


def remove_delivery_mappings(title: str, product_id: int, protected_titles: set[str], provider: str = 'hstore') -> None:
    """Remove this offer's delivery aliases but preserve inventory and shared users."""
    with _lock:
        api,auto=read_configs()
        filename=auto[title].get('productsFileName') if title in auto else None
        candidates={title}
        if filename:
            candidates.update(name for name in auto.sections()
                if auto[name].get('productsFileName')==filename and name in api
                and api[name].get('provider')==provider and api[name].get('product_id')==str(product_id))
        if candidates & protected_titles:
            return
        for name in candidates:
            if name in api and (api[name].get('provider')!=provider or api[name].get('product_id')!=str(product_id)):
                continue
            api.remove_section(name)
            auto.remove_section(name)
        atomic_write(auto,AUTO_CFG)
        atomic_write(api,API_CFG)
        # Purchased data is deliberately retained for pending orders/recovery.
