#!/usr/bin/env python3
"""Mirrors OpenDota's /api/heroStats into data/heroStats.json.

Run by .github/workflows/hero-stats.yml once a day. The file is OpenDota's
response (minified) plus one field, `complexity`: Valve's 1-3 hero complexity
from each hero's npc file (dotabuff/d2vpkr), which the app shows and which
OpenDota doesn't carry. The app parses it exactly like the live endpoint.

A response that doesn't look like a full hero list is rejected and the
previous file stays, so a bad run never replaces good data. A hero whose npc
file can't be read keeps its complexity from the previous file.
"""

import re

import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'heroStats.json')
URL = 'https://api.opendota.com/api/heroStats'
MIN_HEROES = 120
# Fields the app reads (HeroModel, HeroMetaStat).
REQUIRED = ('id', 'localized_name', 'primary_attr', 'roles', 'pub_pick', 'pub_win')
NPC = 'https://raw.githubusercontent.com/dotabuff/d2vpkr/master/dota/scripts/npc/heroes/{}.txt'
COMPLEXITY = re.compile(r'"Complexity"\s*"(\d)"')


def complexity_of(name):
    request = urllib.request.Request(NPC.format(name), headers={'User-Agent': 'dotaforge-data'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            match = COMPLEXITY.search(response.read().decode('utf-8', 'replace'))
        return int(match.group(1)) if match else None
    except (urllib.error.URLError, TimeoutError):
        return None


def previous_complexity():
    try:
        with open(OUT) as f:
            return {h['id']: h['complexity'] for h in json.load(f) if h.get('complexity')}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def fetch():
    url = URL
    key = os.environ.get('OPENDOTA_API_KEY')
    if key:
        url += f'?api_key={key}'
    request = urllib.request.Request(url, headers={'User-Agent': 'dotaforge-data'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode('utf-8'))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            wait = 60 if getattr(e, 'code', None) == 429 else 15 * (attempt + 1)
            print(f'retry in {wait}s: {e}', file=sys.stderr)
            time.sleep(wait)
    sys.exit('OpenDota heroStats unavailable; keeping the previous file')


def main():
    heroes = fetch()
    if not isinstance(heroes, list) or len(heroes) < MIN_HEROES:
        sys.exit(f'Unexpected heroStats ({type(heroes).__name__}, '
                 f'{len(heroes) if isinstance(heroes, list) else "-"} entries); keeping the previous file')
    missing = [f for f in REQUIRED if any(f not in h for h in heroes)]
    if missing:
        sys.exit(f'heroStats lacks {missing}; keeping the previous file')
    heroes.sort(key=lambda h: h['id'])
    previous = previous_complexity()
    found = 0
    for hero in heroes:
        value = complexity_of(hero['name'])
        if value is not None:
            found += 1
        else:
            value = previous.get(hero['id'])
        if value is not None:
            hero['complexity'] = value
    print(f'complexity: {found} from npc files, '
          f'{sum("complexity" in h for h in heroes) - found} kept from the last run')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        f.write(json.dumps(heroes, separators=(',', ':'), ensure_ascii=False))
    print(f'{len(heroes)} heroes → data/heroStats.json')


if __name__ == '__main__':
    main()
