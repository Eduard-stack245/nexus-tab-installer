"""Persisted Free-account queue, with optional user-enabled browser assistance."""
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def files_page_url(mod_url, file_id):
    p = urlsplit(mod_url)
    if p.scheme not in ('https', 'http') or p.hostname not in ('www.nexusmods.com', 'nexusmods.com'):
        raise ValueError('Invalid Nexus mod page')
    params = dict(parse_qsl(p.query, keep_blank_values=True))
    params.update({'tab': 'files', 'file_id': str(int(file_id))})
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(params), ''))


class FreeQueue:
    def __init__(self, path):
        self.path = Path(path)
        try:
            content = json.loads(self.path.read_text(encoding='utf-8'))
            self.items = [x for x in content if isinstance(x, str) and 'nexusmods.com' in x]
        except (OSError, ValueError, TypeError):
            self.items = []

    def __len__(self):
        return len(self.items)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)

    def add(self, urls):
        old = len(self.items)
        self.items.extend(urls)
        self.items = list(dict.fromkeys(self.items))
        self._save()
        return len(self.items)-old

    def clear(self):
        """Cancel unconfirmed links from the current pending Free batch."""
        if not self.items:
            return 0
        count = len(self.items)
        self.items.clear()
        self._save()
        return count

    def peek(self):
        return self.items[0] if self.items else None

    def contains_file(self, game, mod_id, file_id):
        """Only a file explicitly waiting in the Free batch is eligible for NXM import."""
        target = f'/{game}/mods/{int(mod_id)}'
        for url in self.items:
            p = urlsplit(url)
            q = dict(parse_qsl(p.query))
            if (p.path.rstrip('/') in (target, '/games' + target)
                    and q.get('file_id') == str(int(file_id))):
                return True
        return False

    def remove_file(self, game, mod_id, file_id):
        target = f'/mods/{int(mod_id)}'
        keep = []
        for url in self.items:
            p = urlsplit(url)
            q = dict(parse_qsl(p.query))
            if (p.path.rstrip('/') in (f'/{game}{target}', f'/games/{game}{target}')
                    and q.get('file_id') == str(int(file_id))):
                continue
            keep.append(url)
        changed = len(keep) != len(self.items)
        if changed:
            self.items = keep
            self._save()
        return changed
