#!/usr/bin/env python3
"""Builds the hero matchup matrix from a sample of recent pub matches.

OpenDota's /heroes/{id}/matchups is a small, undated sample that RU users
often can't reach (it sits behind Cloudflare). This samples /publicMatches
(100 matches a call, both lineups and the average rank) spread evenly over
the last --days, and counts every hero pair, against and with each other,
split by rank bracket. The app reads the result instead of OpenDota.

    python3 scripts/build_matchup_matrix.py            # ~2,000 calls, ~35 min
    python3 scripts/build_matchup_matrix.py --pages 300 # quick, small sample

The dotaforge data repo runs the same script weekly from a GitHub Action,
with --out/--hero-stats/--cache pointing into that repo and the key, when
there is one, in OPENDOTA_API_KEY.

Free tier: 60 calls a minute and ~3,000 a day per IP, so one full run a day.
Pages are kept under build/matchup_pages/, so a run that stops resumes where
it left off (same --days, within 36 hours). A page that keeps failing is
skipped; ten in a row stop the run.

Output, compact JSON:
    heroes    sorted hero ids
    brackets  ["low", "mid", "high"]: Herald–Crusader, Archon–Legend, Ancient+
    matches   matches counted per bracket
    vs, with  one flat list per bracket: games, wins for each pair (i, j),
              i < j, in row order. vs wins are heroes[i]'s wins against
              heroes[j]; with wins are the wins of a team that had both.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Plain JSON; the app bundles it sealed:
#     dart run tool/matrix_pack.dart seal build/hero_matchups.json
OUT = os.path.join(ROOT, 'build', 'hero_matchups.json')
HERO_STATS = os.path.join(ROOT, 'assets', 'data', 'heroStats.json')
CACHE = os.path.join(ROOT, 'build', 'matchup_pages')
API = 'https://api.opendota.com/api'

# All Pick, Captains Mode, Random Draft, Single Draft, All Random,
# Captains Draft, Ranked All Pick. Leaves out Turbo, Ability Draft and the
# event modes, where hero matchups play out differently.
GAME_MODES = {1, 2, 3, 4, 5, 16, 22}
LOBBY_TYPES = {0, 7}  # public, ranked
MIN_DURATION = 600  # drops early abandons
BRACKETS = ['low', 'mid', 'high']
# The newest matches come without lineups for a while.
SETTLE_SECONDS = 3 * 3600


def bracket_of(avg_rank_tier):
    if not avg_rank_tier:
        return None
    medal = avg_rank_tier // 10
    if medal <= 3:
        return 0
    if medal <= 5:
        return 1
    return 2


def fetch(path):
    url = f'{API}/{path}'
    key = os.environ.get('OPENDOTA_API_KEY')
    if key:
        url += ('&' if '?' in path else '?') + f'api_key={key}'
    request = urllib.request.Request(url, headers={'User-Agent': 'dota-forge-build'})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                left = response.headers.get('x-rate-limit-remaining-day')
                if left is not None and int(left) < 50:
                    sys.exit(f'OpenDota daily limit almost used up ({left} left): '
                             'run again tomorrow, pages so far are kept.')
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            wait = 60 if e.code == 429 else 5 * (attempt + 1)
        except (urllib.error.URLError, TimeoutError):
            wait = 5 * (attempt + 1)
        print(f'  retry in {wait}s: {path}', file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f'gave up on {path}')


def page(less_than):
    return fetch(f'publicMatches?less_than_match_id={less_than}')


def match_id_at(timestamp, low, high):
    """Smallest page boundary whose matches start at or after [timestamp]."""
    while high - low > 50_000:
        mid = (low + high) // 2
        rows = page(mid)
        time.sleep(1)
        if rows and max(m['start_time'] for m in rows) < timestamp:
            low = mid
        else:
            high = mid
    return high


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pages', type=int, default=2000)
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--out', default=OUT)
    parser.add_argument('--hero-stats', default=HERO_STATS)
    parser.add_argument('--cache', default=CACHE)
    args = parser.parse_args()

    cache_dir = os.path.join(args.cache, f'{args.days}d')
    window_file = os.path.join(cache_dir, 'window.json')
    if (os.path.exists(window_file)
            and time.time() - os.path.getmtime(window_file) > 36 * 3600):
        shutil.rmtree(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    if os.path.exists(window_file):
        with open(window_file) as f:
            start_time, end_time, start_id, end_id = json.load(f)
    else:
        latest = fetch('publicMatches')
        newest_id = max(m['match_id'] for m in latest)
        newest_time = max(m['start_time'] for m in latest)
        end_time = newest_time - SETTLE_SECONDS
        start_time = end_time - args.days * 86400
        # Match ids grow ~100k an hour; bound the search generously.
        end_id = match_id_at(end_time, newest_id - 3_000_000, newest_id)
        start_id = match_id_at(start_time, end_id - args.days * 24 * 400_000, end_id)
        with open(window_file, 'w') as f:
            json.dump([start_time, end_time, start_id, end_id], f)
    print(f'window {datetime.date.fromtimestamp(start_time)} → '
          f'{datetime.date.fromtimestamp(end_time)}, ids {start_id}–{end_id}')

    step = (end_id - start_id) // args.pages
    matches = {}
    skipped = failing = 0
    for n in range(args.pages):
        # Fixed page boundaries within the day, so a resumed run reuses them.
        boundary = start_id + step * (n + 1)
        boundary -= boundary % 1000
        cached = os.path.join(cache_dir, f'{boundary}.json')
        if os.path.exists(cached):
            with open(cached) as f:
                rows = json.load(f)
        else:
            try:
                rows = page(boundary)
            except RuntimeError as e:
                skipped += 1
                failing += 1
                print(f'  skipped: {e}', file=sys.stderr)
                if failing >= 10:
                    sys.exit('10 pages in a row failed: OpenDota looks down, '
                             'run again later, pages so far are kept.')
                continue
            failing = 0
            with open(cached, 'w') as f:
                json.dump(rows, f)
            time.sleep(1.05)
        for m in rows:
            matches[m['match_id']] = m
        if (n + 1) % 100 == 0:
            print(f'  {n + 1}/{args.pages} pages, {len(matches)} matches')

    with open(args.hero_stats) as f:
        hero_ids = {h['id'] for h in json.load(f)}

    counted = []
    for m in matches.values():
        lineup = m.get('radiant_team', []) + m.get('dire_team', [])
        bracket = bracket_of(m.get('avg_rank_tier'))
        if (bracket is None
                or m.get('game_mode') not in GAME_MODES
                or m.get('lobby_type') not in LOBBY_TYPES
                or (m.get('duration') or 0) < MIN_DURATION
                or len(lineup) != 10 or 0 in lineup
                or len(set(lineup)) != 10):
            continue
        hero_ids.update(lineup)
        counted.append((bracket, m['radiant_team'], m['dire_team'], m['radiant_win']))

    heroes = sorted(hero_ids)
    index = {h: i for i, h in enumerate(heroes)}
    n = len(heroes)
    pairs = n * (n - 1) // 2

    def slot(i, j):  # i < j
        return i * n - i * (i + 1) // 2 + (j - i - 1)

    vs = [[0] * (2 * pairs) for _ in BRACKETS]
    with_ = [[0] * (2 * pairs) for _ in BRACKETS]
    per_bracket = [0] * len(BRACKETS)
    for bracket, radiant, dire, radiant_win in counted:
        per_bracket[bracket] += 1
        for a in radiant:
            for b in dire:
                i, j = index[a], index[b]
                a_won = radiant_win
                if i > j:
                    i, j, a_won = j, i, not a_won
                k = 2 * slot(i, j)
                vs[bracket][k] += 1
                vs[bracket][k + 1] += a_won
        for team, won in ((radiant, radiant_win), (dire, not radiant_win)):
            ids = sorted(index[h] for h in team)
            for x in range(5):
                for y in range(x + 1, 5):
                    k = 2 * slot(ids[x], ids[y])
                    with_[bracket][k] += 1
                    with_[bracket][k + 1] += won

    if len(counted) < 1000:
        sys.exit(f'only {len(counted)} usable matches: kept the old file')

    out = {
        'v': 1,
        'generated_at': datetime.datetime.now(datetime.timezone.utc)
                        .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'from': start_time,
        'to': end_time,
        'heroes': heroes,
        'brackets': BRACKETS,
        'matches': per_bracket,
        'vs': vs,
        'with': with_,
    }
    body = json.dumps(out, separators=(',', ':'))
    with open(args.out, 'w') as f:
        f.write(body)

    def coverage(table, bracket=None):
        rows = range(len(BRACKETS)) if bracket is None else [bracket]
        games = [sum(table[b][2 * p] for b in rows) for p in range(pairs)]
        return sum(g >= 30 for g in games) / pairs

    if skipped:
        print(f'{skipped} pages skipped after failing')
    print(f'{len(counted)} matches ({per_bracket}), {n} heroes, '
          f'{len(body) // 1024} KB → {args.out}')
    print(f'pairs with 30+ games against: all {coverage(vs):.0%}, '
          + ', '.join(f'{b} {coverage(vs, i):.0%}' for i, b in enumerate(BRACKETS)))
    print(f'pairs with 30+ games together: all {coverage(with_):.0%}')


if __name__ == '__main__':
    main()
