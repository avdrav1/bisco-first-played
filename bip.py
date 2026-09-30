"""Read Biscuits Internet Project data that /api/songs doesn't carry.

BIP is a React Router app. Each page also serves its loader data at `<path>.data` in turbo-stream
form: one flat JSON array in which objects are {"_<key index>": <value index>} and arrays hold value
indices, so shared values are written once. `decode` rebuilds the plain structure.
"""
import json
import urllib.request

SITE = "https://discobiscuits.net"
UA = {"User-Agent": "bisco-first-played/1.0 (fan timeline; github.com/avdrav1/bisco-first-played)"}


def decode(flat: list):
    memo: dict = {}

    def val(i):
        if not isinstance(i, int) or i < 0:  # negative indices encode undefined/null/NaN/holes
            return None
        if i in memo:
            return memo[i]
        raw = flat[i]
        if isinstance(raw, dict):
            out: dict = {}
            memo[i] = out
            for k, v in raw.items():
                out[flat[int(k[1:])]] = val(v)
            return out
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str):  # typed value, e.g. ["D", <epoch ms>] for a Date
                memo[i] = raw[1] if len(raw) > 1 else None
                return memo[i]
            out_list: list = []
            memo[i] = out_list
            out_list.extend(val(x) for x in raw)
            return out_list
        memo[i] = raw
        return raw

    return val(0)


def _get_data(path: str):
    with urllib.request.urlopen(urllib.request.Request(f"{SITE}{path}.data", headers=UA), timeout=45) as r:
        # split("\n"), not splitlines(): the latter also breaks on U+2028 and friends inside JSON strings.
        return decode(json.loads(r.read().decode("utf-8").split("\n")[0]))


def _find(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _find(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def song_performances(slug: str) -> list:
    """Every show BIP lists the song at: [{date, show, venue, city, state, country}], oldest first.
    A song played twice in one night (a sandwich) is one entry."""
    perfs = _find(_get_data(f"/songs/{slug}"), "performances") or []
    by_show = {}
    for p in perfs:
        show = (p or {}).get("show") or {}
        if not show.get("date") or show.get("slug") in by_show:
            continue
        venue = p.get("venue") or {}
        by_show[show.get("slug")] = {
            "date": show["date"][:10], "show": show.get("slug"), "venue": venue.get("name") or "Unknown venue",
            "city": venue.get("city") or "", "state": venue.get("state") or "", "country": venue.get("country") or "",
        }
    return sorted(by_show.values(), key=lambda x: x["date"])


def show_setlist(show_id: str) -> list:
    """Song ids of one show in performance order (sets, then encores)."""
    with urllib.request.urlopen(urllib.request.Request(f"{SITE}/api/tracks?showId={show_id}", headers=UA), timeout=45) as r:
        tracks = json.load(r)
    order = {"S1": 1, "S2": 2, "S3": 3, "S4": 4, "E1": 9, "E2": 10, "E3": 11}
    tracks.sort(key=lambda t: (order.get(t.get("set"), 5), t.get("position") or 0))
    return [t["songId"] for t in tracks]
