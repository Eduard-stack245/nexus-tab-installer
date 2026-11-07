"""Nexus Mods URL parsing and collection persistence (standard library only).

A tab or a pasted link may point to a concrete file (``?tab=files&file_id=N``,
the Slow download page). That file ID is the user's explicit choice and is kept
next to the mod identity, so the downloader fetches exactly that archive.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

OLD = re.compile(r'^/([^/]+)/mods/(\d+)(?:/|$)', re.I)
NEW = re.compile(r'^/games/([^/]+)/mods/(\d+)(?:/|$)', re.I)


def _file_id(query):
    try:
        value = parse_qs(query).get('file_id', [''])[0]
    except (TypeError, ValueError):
        return 0
    return int(value) if value.isdigit() and 0 < int(value) < 2**31 else 0


def parse_mod(url):
    try:
        u = urlsplit(url.strip())
        if u.scheme != 'https' or (u.hostname or '').lower() not in ('www.nexusmods.com', 'nexusmods.com'):
            return None
        path = u.path.rstrip('/')
        match = NEW.match(path) or OLD.match(path)
        if not match:
            return None
        game, mod_id = match.groups()
        if not re.fullmatch(r'[A-Za-z0-9_-]{2,80}', game) or game.lower() in ('games', 'users'):
            return None
        item = {'game': game.lower(), 'mod_id': int(mod_id),
                'url': f'https://www.nexusmods.com/{game.lower()}/mods/{int(mod_id)}'}
        fid = _file_id(u.query)
        if fid:
            item['file_ids'] = [fid]
        return item
    except (ValueError, AttributeError):
        return None


def _clean_file_ids(raw):
    result = []
    for value in raw if isinstance(raw, (list, tuple)) else []:
        try:
            fid = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < fid < 2**31 and fid not in result:
            result.append(fid)
    return result


def normalize_entries(entries):
    """Unique mods in first-seen order; explicit file IDs of duplicates are merged."""
    merged = {}
    order = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item = parse_mod(str(entry.get('url', '')))
        if not item:
            continue
        key = (item['game'], item['mod_id'])
        file_ids = _clean_file_ids(item.get('file_ids', []) + _clean_file_ids(entry.get('file_ids')))
        title = str(entry.get('title') or '').strip()[:160]
        if key in merged:
            known = merged[key]
            for fid in file_ids:
                if fid not in known.setdefault('file_ids', []):
                    known['file_ids'].append(fid)
            if title and known['title'].endswith(f"#{item['mod_id']}"):
                known['title'] = title
            continue
        item.pop('file_ids', None)
        if file_ids:
            item['file_ids'] = file_ids
        item['title'] = title or f"{item['game']} / #{item['mod_id']}"
        merged[key] = item
        order.append(key)
    return [merged[key] for key in order]


def save_pack(path, entries):
    Path(path).write_text(json.dumps({'format': 'nexus-tab-pack-v1', 'mods': normalize_entries(entries)}, ensure_ascii=False, indent=2), encoding='utf-8')


def load_pack(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != 'nexus-tab-pack-v1' or not isinstance(data.get('mods'), list):
        raise ValueError('Неподдерживаемый формат сборки')
    return normalize_entries(data['mods'])


def extract_mod_links(text):
    """Find Nexus mod pages in copied browser URLs or text; never guess mod IDs.

    A ``file_id`` query parameter is preserved, so a link to a concrete file
    downloads that file instead of the newest main file.
    """
    if not isinstance(text, str):
        return []
    # Browsers sometimes copy markdown links, percent-encoded URL strings,
    # or URLs broken across lines in a chat window.
    from html import unescape
    from urllib.parse import unquote
    text = unescape(text).replace('​', '').replace('﻿', '')
    text = unquote(text)
    # Ignore whitespace introduced directly after a slash or before the digits
    # of a URL's mod ID; do not join unrelated links together.
    text = re.sub(r'(https?://(?:www\.)?nexusmods\.com/(?:games/)?[\w-]+/mods/)\s+(\d+)', r'\1\2', text, flags=re.I)
    pattern = re.compile(
        r'https?://(?:www\.)?nexusmods\.com/(?:games/)?[A-Za-z0-9_-]+/mods/\d+(?:[/?#][^\s<>"\')\]]*)?', re.I)
    result, index = [], {}
    for match in pattern.finditer(text):
        raw = match.group().replace('http://', 'https://', 1)
        mod = parse_mod(raw)
        if not mod:
            continue
        key = (mod['game'], mod['mod_id'])
        fid = (mod.get('file_ids') or [0])[0]
        if key not in index:
            index[key] = len(result)
            result.append(mod['url'] + (f'?tab=files&file_id={fid}' if fid else ''))
        elif fid:
            # Another link to a different file of the same mod: keep both files.
            result.append(mod['url'] + f'?tab=files&file_id={fid}')
    return result
