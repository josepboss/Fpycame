"""Lossless, chat-safe formatting for credentials containing oversized words."""
from __future__ import annotations

import re
from collections.abc import Callable

CHUNK_SIZE = 64
LONG_WORD = re.compile(r'\S{81,}')


def format_products(products: list[str], translate: Callable[..., str]) -> tuple[str, bool]:
    """Frame chunks so Cardinal's whitespace trimming cannot alter their content."""
    if not any(LONG_WORD.search(product) for product in products):
        return '\n'.join(products), False
    records = [translate('delivery_chunk_instructions')]
    for number, product in enumerate(products, 1):
        # Newlines cannot be framed on a single chat line. JSON encoding is not
        # used here: product records come from the existing line-based stock store.
        if '\n' in product or '\r' in product:
            raise ValueError('Product record must be a single line')
        pieces = [product[i:i + CHUNK_SIZE] for i in range(0, len(product), CHUNK_SIZE)]
        records.append(translate('delivery_chunk_product', number))
        for index, piece in enumerate(pieces, 1):
            records.append(translate('delivery_chunk_part', index, len(pieces)) + f' [{piece}]')
    return '\n'.join(records), True
