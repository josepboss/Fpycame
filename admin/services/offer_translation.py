"""Cached translation of public offer text, never purchased credentials."""
from __future__ import annotations

from contextlib import closing
import hashlib
import html
import re
import sqlite3
from pathlib import Path

import requests

from admin.services.offer_import import ImportFailure

DB_PATH = Path('storage/cache/offer_translations.sqlite3')
CYRILLIC = re.compile(r'[\u0400-\u04ff]')


def translate_text(text: str, source: str, target: str) -> str:
    """Use MyMemory's documented public lookup API (500-byte request limit)."""
    digest = hashlib.sha256(f'{source}|{target}|{text}'.encode()).hexdigest()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS translations (id TEXT PRIMARY KEY, text TEXT NOT NULL)')
        row = db.execute('SELECT text FROM translations WHERE id=?', (digest,)).fetchone()
    if row:
        return row[0]
    output = []
    for segment in re.split(r'(\n+)', text):
        if not segment or segment.isspace():
            output.append(segment)
            continue
        translated = []
        while segment:
            count, size = 0, 0
            for char in segment:
                if size + len(char.encode()) > 450:
                    break
                count += 1
                size += len(char.encode())
            cut = count
            if cut < len(segment):
                boundary = segment.rfind(' ', 0, cut+1)
                if boundary > cut//2:
                    cut = boundary
            part, segment = segment[:cut].strip(), segment[cut:].lstrip()
            try:
                response = requests.get('https://api.mymemory.translated.net/get',
                    params={'q': part, 'langpair': f'{source}|{target}', 'mt': 1},
                    timeout=(10, 30), allow_redirects=False)
                data = response.json() if response.status_code == 200 else {}
                value = data.get('responseData', {}).get('translatedText')
                if (str(data.get('responseStatus')) != '200' or data.get('quotaFinished') or
                        not isinstance(value, str) or not value.strip()):
                    raise ImportFailure('oi_translation_failed')
                value = html.unescape(value).strip()
                if target == 'en' and CYRILLIC.search(value):
                    raise ImportFailure('oi_translation_failed')
                translated.append(value)
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                raise ImportFailure('oi_translation_failed') from None
        output.append(' '.join(translated))
    result = ''.join(output).strip()
    if not result:
        raise ImportFailure('oi_translation_failed')
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as db:
        db.execute('INSERT OR REPLACE INTO translations VALUES (?,?)', (digest, result))
        db.commit()
    return result


def bilingual_text(text: str, limit: int) -> dict[str, str]:
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()[:limit].rstrip()
    source = 'ru' if CYRILLIC.search(text) else 'en'
    target = 'en' if source == 'ru' else 'ru'
    return {source: text, target: translate_text(text, source, target)[:limit].rstrip()}


def bilingual_listing(title: str, description: str) -> dict[str, str]:
    names, descriptions = bilingual_text(title, 100), bilingual_text(description, 817)
    return {'title_en': names['en'], 'title_ru': names['ru'],
            'description_en': descriptions['en'], 'description_ru': descriptions['ru']}
