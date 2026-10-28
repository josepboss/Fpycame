"""Consistent imported-offer titles across preview, publication and monitoring."""
import re

from locales.localizer import Localizer

PREFIXES = {lang: Localizer().translate('oi_instant_delivery', language=lang) for lang in ('en','ru')}
_PREFIX = re.compile(r'^(?:instant delivery|мгновенная доставка)(?=$|[\s:—–-])[\s:—–-]*', re.I)


def imported_title(title: str, language: str = 'en') -> str:
    title = title.strip()
    if not title:
        return ''
    while match := _PREFIX.match(title):
        title = title[match.end():].strip()
    prefix = PREFIXES[language]
    return (prefix + ' ' + title[:100-len(prefix)-1]).rstrip()


def has_delivery_prefix(title: str) -> bool:
    return title == PREFIXES['en'] or title.startswith(PREFIXES['en']+' ')
