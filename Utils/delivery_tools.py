"""Lossless, chat-safe formatting for credentials containing oversized words."""
from __future__ import annotations

import re
from collections.abc import Callable

CHUNK_SIZE = 64
LONG_WORD = re.compile(r'\S{81,}')


def account_fields(product: str) -> list[tuple[str, str]] | None:
    """Recognize only the exact five-section account format seen in delivery."""
    sections = product.split('----')
    if len(sections) != 5:
        return None
    mail = sections[2].split('|')
    if len(mail) != 4 or '@' not in mail[0] or not all(sections) or not all(mail):
        return None
    return [
        ('delivery_field_username', sections[0]),
        ('delivery_field_password', sections[1]),
        ('delivery_field_email', mail[0]),
        ('delivery_field_email_password', mail[1]),
        ('delivery_field_2fa', sections[3]),
        ('delivery_field_auth', sections[4]),
        ('delivery_field_client', mail[3]),
        ('delivery_field_refresh', mail[2]),
    ]



FORMAT_FIELDS = {
    'username':'delivery_field_username', 'password':'delivery_field_password',
    'email':'delivery_field_email', 'email_password':'delivery_field_email_password',
    'refresh_token':'delivery_field_refresh', 'client_id':'delivery_field_client',
    '2fa':'delivery_field_2fa', 'auth_token':'delivery_field_auth', 'ct0':'delivery_field_ct0',
}
FORMAT_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*|2fa')
# Match only a complete delimiter-bounded, long Microsoft-style token.


def format_spec(spec: str) -> tuple[list[str], list[str]]:
    names=list(FORMAT_NAME.finditer(spec))
    if (not spec or len(spec)>500 or not names or names[0].start()!=0 or names[-1].end()!=len(spec)):
        raise ValueError('Invalid delivery format')
    fields=[name.group().lower() for name in names]
    separators=[spec[left.end():right.start()] for left,right in zip(names,names[1:])]
    if (any(name not in FORMAT_FIELDS for name in fields) or len(set(fields))!=len(fields) or
            any(not sep or any(c not in '-|:;,\t ' for c in sep) for sep in separators) or
            all(name=='refresh_token' for name in fields)):
        raise ValueError('Invalid delivery format')
    return fields,separators


def configured_fields(product: str, spec: str) -> list[tuple[str,str]] | None:
    names,separators=format_spec(spec)
    boundary='|'.join(re.escape(sep) for sep in sorted(set(separators),key=len,reverse=True))
    capture='((?:(?!(?:'+boundary+')).)*)' if separators else '(.*)'
    pattern='^' + ''.join(capture+re.escape(sep) for sep in separators) + capture + '$'
    match=re.fullmatch(pattern,product)
    if match is None or any(not value for value in match.groups()):
        return None
    values=list(match.groups())
    if any(sep in value for sep in separators for value in values):
        return None
    return [(FORMAT_FIELDS[name],value) for name,value in zip(names,values)]


def remove_refresh_hints(product: str) -> str:
    # Stop before ---- even though '-' can occur inside the token itself.
    pattern=r'(^|[|:;\s]|----)(M\.C(?:(?!----)[A-Za-z0-9_.!*+$%/=-]){97,})(?=$|[|:;\s]|----)'
    return re.sub(pattern,lambda match: match[1],product)


def format_products(products: list[str], translate: Callable[..., str], delivery_format: str = '') -> tuple[str, bool]:
    """Use an optional per-offer schema; otherwise apply the requested token hint."""
    if delivery_format:
        format_spec(delivery_format)
    structured = [configured_fields(product,delivery_format) if delivery_format else account_fields(product) for product in products]
    cleaned = [product if fields is not None else remove_refresh_hints(product) for product,fields in zip(products,structured)]
    altered = cleaned != products
    if not any(structured) and not any(LONG_WORD.search(product) for product in cleaned):
        return '\n'.join(cleaned), altered
    records = []
    if any(structured):
        records.append(translate('delivery_field_instructions'))
    if any(fields is None for fields in structured):
        records.append(translate('delivery_chunk_instructions'))
    for number, (product, fields) in enumerate(zip(cleaned, structured), 1):
        if '\n' in product or '\r' in product:
            raise ValueError('Product record must be a single line')
        records.append(translate('delivery_chunk_product', number))
        if fields is None:
            pieces = [product[i:i + CHUNK_SIZE] for i in range(0, len(product), CHUNK_SIZE)]
            for index, piece in enumerate(pieces, 1):
                records.append(translate('delivery_chunk_part', index, len(pieces)) + f' [{piece}]')
            continue
        for key, value in fields:
            if key == 'delivery_field_refresh':
                continue
            label = translate(key)
            if len(value) <= CHUNK_SIZE and value == value.strip():
                records.append(f'{label}: {value}')
            else:
                pieces = [value[i:i + CHUNK_SIZE] for i in range(0, len(value), CHUNK_SIZE)]
                for index, piece in enumerate(pieces, 1):
                    records.append(translate('delivery_field_part', number, label, index, len(pieces)) + f' [{piece}]')
    return '\n'.join(records), True
