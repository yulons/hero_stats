# Dota Forge data

Public data files for the Dota Forge app, refreshed by GitHub Actions and
served from `raw.githubusercontent.com`. Many players can't reach OpenDota
directly (it sits behind Cloudflare, which some ISPs throttle), and it can
take several seconds to answer; a static copy here loads in well under one.

| File | Source | Refreshed |
|---|---|---|
| `data/heroStats.json` | [OpenDota `/api/heroStats`](https://docs.opendota.com/), as is | daily |
| `data/hero_matchups.bin` | built from OpenDota `/api/publicMatches` (last 14 days, 3 rank brackets), sealed | weekly |

A run that gets a bad or partial response keeps the previous file.

Data comes from [OpenDota](https://www.opendota.com/).

Repository secrets:
- `PACK_KEY` (required by the matrix workflow): seals the matrix the way the
  app opens it (gzip, then AES-256-GCM).
- `OPENDOTA_API_KEY` (optional): raises OpenDota's rate limits.

## Running by hand

```
python3 scripts/fetch_hero_stats.py
python3 scripts/build_matchup_matrix.py --out build/hero_matchups.json \
    --hero-stats data/heroStats.json --cache build/matchup_pages
PACK_KEY=... python3 scripts/seal_pack.py build/hero_matchups.json data/hero_matchups.bin
```

Both workflows can also be started from the Actions tab (Run workflow).
