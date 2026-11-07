"""Presentation helpers: never confuse browser tab titles with mod names."""
from __future__ import annotations
import re

_SITE_SUFFIX = re.compile(r'\s+(?:at|в)\s+(?:[\w ]+\s+)?Nexus\s*[-–:]\s*Mods.*$', re.I)
_TRAILING = re.compile(r'\s+(?:at\s+)?(?:Skyrim Special Edition|Fallout 4|Cyberpunk 2077|Skyrim)\s+Nexus.*$', re.I)
_GENERIC = re.compile(r'^\s*[\w-]+\s*/\s*#\d+\s*$')
_SITE_ONLY = re.compile(r'(?:[\w ]+\s)?Nexus(?:\s+Mods)?', re.I)


def clean_mod_title(title: object, game: str, mod_id: int) -> str:
    """Display the mod's title, not Edge's SEO suffix or browser page metadata."""
    text = ' '.join(str(title or '').split())
    if not text or _GENERIC.fullmatch(text):
        return f'Мод #{mod_id}'
    text = _SITE_SUFFIX.sub('', text)
    text = _TRAILING.sub('', text)
    text = re.split(r'\s+[-–—]\s+Mods\s+(?:and|&)\s+Community', text, maxsplit=1, flags=re.I)[0]
    # A tab that had not loaded yet carries the name of the site, not of the mod.
    if _SITE_ONLY.fullmatch(text):
        return f'Мод #{mod_id}'
    # Browser auto-translation sometimes appends page metadata. Avoid losing
    # legitimate hyphenated mod titles by trimming only recognisable suffixes.
    return text.strip(' –—-')[:112] or f'Мод #{mod_id}'
