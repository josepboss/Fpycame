"""HStore catalog import and FunPay offer creation (no provider purchases)."""
from __future__ import annotations

import configparser
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_UP
from urllib.parse import urlsplit, urljoin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from FunPayAPI import Account

import requests
from bs4 import BeautifulSoup


class ImportFailure(ValueError):
    """A localized error code with optional sanitized remote validation details."""

    def __init__(self, code: str, details: str = "") -> None:
        super().__init__(code)
        self.details = details


@dataclass
class Product:
    product_id: int
    url: str
    title: str
    description: str
    price: str
    stock: int
    currency: str = "USD"
    provider: str = "hstore"
    minimum_order: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def product_url(value: str) -> tuple[int, str]:
    parts = urlsplit(value.strip())
    match = re.fullmatch(r"/en/product/([1-9][0-9]*)(?:-[\w-]+)?/?", parts.path)
    if (parts.scheme != "https" or parts.netloc != "hstora.com" or
            parts.query or parts.fragment or not match):
        raise ImportFailure("oi_bad_url")
    return int(match[1]), f"https://hstora.com{parts.path}"


def parse_product(html: str, url: str) -> Product:
    product_id, url = product_url(url)
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one("h1.product-main-title")
    description = soup.select_one("#description .product-description")
    price = soup.select_one("#productPurchasePanel #priceAmount")
    stock = soup.select_one('#productPurchasePanel [data-live-product-stock]')
    if not all((title, description, price, stock)):
        raise ImportFailure("oi_bad_product")
    price_text = price.get_text(strip=True)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?\s*\$", price_text):
        raise ImportFailure("oi_currency")
    try:
        stock_text = stock.get("data-stock-value", stock.get_text(" ", strip=True))
        stock_text = re.sub(r"\s+", " ", stock_text).strip()
        if stock_text.casefold() == "out of stock":
            amount = 0
        else:
            if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", stock_text):
                raise ValueError()
            amount = int(stock_text.replace(",", ""))
        if int(stock["data-product-id"]) != product_id or amount < 0:
            raise ValueError()
        cost = Decimal(price_text.rstrip("$").strip())
        if cost <= 0:
            raise ValueError()
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise ImportFailure("oi_bad_product") from exc
    # Preserve plain-text line breaks; never import executable HTML.
    for br in description.select("br"):
        br.replace_with("\n")
    for block in description.select("p, div, li, h2, h3, h4"):
        block.insert_before("\n")
        block.insert_after("\n")
    text = description.get_text().strip()
    name = title.get_text(" ", strip=True)
    if not name or not text:
        raise ImportFailure("oi_bad_product")
    return Product(product_id, url, name, text, str(cost), amount)


def fetch_product(url: str, product_id: int | None = None) -> Product:
    if urlsplit(url.strip()).hostname != "hstora.com":
        return fetch_account_product(url, product_id)
    expected_id, url = product_url(url)
    try:
        for _ in range(4):
            response = requests.get(url, timeout=(10, 30), allow_redirects=False,
                                    headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get('Location')
                if not isinstance(location,str) or not location:
                    raise ImportFailure('oi_fetch_failed')
                next_id,next_url=product_url(urljoin(url,location))
                if next_id != expected_id:
                    raise ImportFailure('oi_bad_product')
                url=next_url
                continue
            if response.status_code != 200:
                raise ImportFailure("oi_fetch_failed")
            return parse_product(response.text, url)
        raise ImportFailure('oi_fetch_failed')
    except requests.RequestException as exc:
        raise ImportFailure("oi_fetch_failed") from exc


def fetch_live_stock(product_id: int, provider: str = 'hstore') -> int:
    """Check inventory independently of product descriptions or prices."""
    if provider != 'hstore':
        from plugins.providers.accounts import AccountSupplier, SupplierError
        try:
            data = AccountSupplier(provider).product(product_id)
            value = data['quantity']
            if (int(data['id']) != product_id or isinstance(value, bool) or
                    not isinstance(value, (int, str)) or not str(value).isdigit()):
                raise ValueError()
            return int(value)
        except (SupplierError, KeyError, TypeError, ValueError):
            raise ImportFailure('oi_fetch_failed') from None
    try:
        response = requests.get('https://hstora.com/api/product-stock',
            params={'ids': str(product_id)}, timeout=(10, 30), allow_redirects=False,
            headers={'User-Agent':'Mozilla/5.0', 'Accept':'application/json', 'Cache-Control':'no-cache'})
        if response.status_code != 200:
            raise ImportFailure('oi_fetch_failed')
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('success') is not True:
            raise ImportFailure('oi_bad_product')
        value = payload['stocks'][str(product_id)]['stock_quantity']
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
            raise ImportFailure('oi_bad_product')
        return int(value)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise ImportFailure('oi_fetch_failed') from exc


def selling_price(cost: str, markup: str) -> str:
    try:
        if not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,2})?", markup):
            raise ValueError()
        base, percent = Decimal(cost), Decimal(markup)
        if (not base.is_finite() or not percent.is_finite() or
                base <= 0 or percent < 0 or percent > 100000):
            raise ValueError()
        return str((base * (1 + percent / 100)).quantize(Decimal("0.01"), rounding=ROUND_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ImportFailure("oi_bad_markup") from exc


def make_account() -> Account:
    # Web admin runs in a separate process; do not construct Cardinal's singleton.
    from FunPayAPI import Account
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read("configs/_main.cfg", encoding="utf-8")
    key = cfg.get("FunPay", "golden_key", fallback="").strip()
    if not key:
        raise ImportFailure("oi_no_account")
    proxy = {}
    if cfg.getboolean("Proxy", "enable", fallback=False):
        from Utils.cardinal_tools import validate_proxy, build_proxy
        value = cfg.get("Proxy", "proxy", fallback="").strip()
        if not value:
            raise ImportFailure("oi_proxy")
        value = build_proxy(*validate_proxy(value))
        proxy = {"http": value, "https": value}
    return Account(key, user_agent=cfg.get("FunPay", "user_agent", fallback=None),
                   requests_timeout=30, proxy=proxy, locale="en").get()


def new_offer_fields(html: str, node: int) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    form = soup.select_one("form.form-offer-editor")
    if form is None:
        if "there are too many offers" in soup.get_text(" ", strip=True).lower():
            raise ImportFailure("oi_offer_limit")
        raise ImportFailure("oi_bad_form")
    fields = {}
    for element in form.select("input[name], textarea[name], select[name]"):
        if element.has_attr("disabled"):
            continue
        name = element["name"]
        kind = element.get("type", "")
        if kind in ("submit", "button", "file"):
            continue
        if kind in ("checkbox", "radio") and not element.has_attr("checked"):
            continue
        if element.name == "textarea":
            fields[name] = element.get_text()
        elif element.name == "select":
            option = element.select_one("option[selected]") or element.select_one("option")
            fields[name] = option.get("value", option.get_text()) if option else ""
            # Category-specific choices must be made explicitly in FunPay first.
            if name.startswith("fields["):
                raise ImportFailure("oi_category_fields")
        else:
            fields[name] = element.get("value", "on" if kind == "checkbox" else "")
    if (fields.get("node_id") != str(node) or fields.get("offer_id") not in ("", "0") or
            not fields.get("csrf_token") or "fields[summary][en]" not in fields):
        raise ImportFailure("oi_bad_form")
    price_input = form.select_one('input[name="price"]')
    group = price_input.find_parent(class_="form-group") if price_input else None
    currency = group.select_one(".form-control-feedback") if group else None
    if currency is None or currency.get_text(strip=True) != "$":
        raise ImportFailure("oi_currency")
    return fields


def prepare_offer(account: Account, node: int, title: str, description: str,
                  price: str, quantity: int, title_ru: str | None = None,
                  description_ru: str | None = None) -> dict[str, str]:
    response = account.method("get", f"lots/offerEdit?node={node}", {}, {},
                              raise_not_200=True, locale="en")
    fields = new_offer_fields(response.text, node)
    fields.update({"offer_id": "0", "deleted": "", "active": "on",
                   "auto_delivery": "", "secrets": "", "amount": str(quantity),
                   "price": price, "fields[summary][en]": title,
                   "fields[summary][ru]": title_ru if title_ru is not None else title, "fields[desc][en]": description,
                   "fields[desc][ru]": description_ru if description_ru is not None else description, "fields[images]": ""})
    return fields


def rejection_details(result: dict, sensitive_values: list[str]) -> str:
    """Extract validation messages only; never expose the raw response or cookies."""
    messages = []
    if isinstance(result.get("error"), str):
        messages.append(result["error"])
    errors = result.get("errors", [])
    if isinstance(errors, dict):
        errors = list(errors.items())
    if isinstance(errors, list):
        for error in errors[:10]:
            if isinstance(error, (list, tuple)) and len(error) == 2:
                field, message = error
                if isinstance(field, str) and isinstance(message, str):
                    messages.append(f"{field}: {message}")
            elif isinstance(error, str):
                messages.append(error)
    # Strip remote markup and redact known credentials before displaying anything.
    text = "\n".join(messages)
    for value in sorted((v for v in sensitive_values if isinstance(v, str) and v), key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(golden_key|phpsessid|csrf_token|api_key|password)(\s*[=:]\s*)[^\s<>&]+",
                  r"\1\2[redacted]", text)
    soup = BeautifulSoup(text, "lxml")
    for element in soup.select("script, style"):
        element.decompose()
    return soup.get_text(" ", strip=True)[:1500]


def submit_offer(account: Account, fields: dict[str, str]) -> dict:
    # The legacy client retries POSTs on 5xx/read errors. Creation must be sent
    # exactly once: a lost response must never create a second public listing.
    account.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
    response = account.session.post(
        "https://funpay.com/en/lots/offerSave",
        headers={"accept": "application/json",
                 "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                 "x-requested-with": "XMLHttpRequest",
                 "user-agent": account.user_agent or "Mozilla/5.0"},
        cookies={"golden_key": account.golden_key, "PHPSESSID": account.phpsessid},
        data=fields, timeout=account.requests_timeout, proxies=account.proxy or {},
        allow_redirects=False,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ImportFailure("oi_uncertain")
    if result.get("error") or result.get("errors"):
        details = rejection_details(result, [account.golden_key, account.phpsessid,
                                             fields.get("csrf_token", "")])
        raise ImportFailure("oi_rejected", details)
    if result.get("done") is not True and not result.get("url") and not result.get("offer_id"):
        raise ImportFailure("oi_uncertain")
    return result


def supplier_url(value: str) -> tuple[str, str, int | None]:
    from plugins.providers.accounts import DOMAINS
    parts = urlsplit(value.strip())
    provider = next((name for name, hosts in DOMAINS.items() if parts.netloc in hosts), None)
    if not provider or parts.scheme != 'https' or parts.query or parts.fragment:
        raise ImportFailure('oi_bad_url')
    pattern = r'/product/([1-9][0-9]*)/?' if provider == 'djekxa' else r'/(?:product|products/view)/([\w-]+)/?'
    match = re.fullmatch(pattern, parts.path)
    if not match:
        raise ImportFailure('oi_bad_url')
    identifier = int(match[1]) if match[1].isdigit() else None
    return provider, value.strip(), identifier


def fetch_account_product(url: str, expected_id: int | None = None) -> Product:
    from plugins.providers.accounts import AccountSupplier, SupplierError
    provider, url, product_id = supplier_url(url)
    if expected_id is not None:
        if product_id is not None and product_id != expected_id:
            raise ImportFailure('oi_bad_product')
        product_id = expected_id
    try:
        if product_id is None:
            response = requests.get(url, timeout=(10, 30), allow_redirects=False)
            if response.status_code != 200:
                raise ImportFailure('oi_fetch_failed')
            card = BeautifulSoup(response.text, 'lxml').select_one('.card-product[data-product-id]')
            if card is None:
                raise ImportFailure('oi_bad_product')
            product_id = int(card['data-product-id'])
        data = AccountSupplier(provider).product(product_id)
        if isinstance(data.get('id'), bool) or int(data['id']) != product_id:
            raise ImportFailure('oi_bad_product')
        if data.get('is_manual_order_delivery') not in (None, 0, False):
            raise ImportFailure('oi_manual_product')
        canonical = data.get('url')
        if canonical:
            canonical_provider, canonical, canonical_id = supplier_url(canonical)
            if canonical_provider != provider or (canonical_id is not None and canonical_id != product_id):
                raise ImportFailure('oi_bad_product')
            url = canonical
        title = str(data['title'] if provider == 'djekxa' else data['name']).strip()
        description = BeautifulSoup(str(data.get('description', ''))+'\n'+str(data.get('manual', '')), 'lxml')
        for tag in description.select('script,style'):
            tag.decompose()
        for br in description.select('br'):
            br.replace_with('\n')
        text = description.get_text('\n', strip=True)
        price = Decimal(str(data['price']))
        stock, minimum = data['quantity'], data.get('minimum_order', 1)
        if (not title or not text or not price.is_finite() or price <= 0 or
            isinstance(stock, bool) or not str(stock).isdigit() or not str(minimum).isdigit() or int(minimum) < 1):
            raise ImportFailure('oi_bad_product')
        return Product(product_id, url, title, text, format(price, 'f'), int(stock), 'RUB', provider, int(minimum))
    except SupplierError as exc:
        raise ImportFailure('oi_provider_key' if str(exc) == 'oi_provider_key' else 'oi_fetch_failed') from None
    except (requests.RequestException, KeyError, TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, ImportFailure):
            raise
        raise ImportFailure('oi_bad_product') from None


def usd_cost(price: str, currency: str = 'USD', rub_per_usd: str | None = None) -> str:
    if currency == 'USD':
        return price
    try:
        rate = Decimal(rub_per_usd or '')
        if currency != 'RUB' or not rate.is_finite() or rate <= 0:
            raise ValueError()
        return str(Decimal(price) / rate)
    except (ValueError, InvalidOperation):
        raise ImportFailure('oi_exchange_rate') from None
