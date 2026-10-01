"""Find playable archive.org recordings for each song: its debut if possible, and its latest version.

Three passes for the debut clip, most trustworthy first; each song keeps the first that succeeds, tagged with `how`:

  "title"    a track on the debut show's recording is titled as the song.
  "setlist"  the debut recording has no such track, but BIP's setlist puts the song in a one-track gap
             between two neighbours that did match, and that track's title resembles the song or is a
             placeholder ("New Song", "-unknown-"). A gap track titled as some *other* song is refused.
  "later"    the debut was never recorded (or never found); the earliest *other* show BIP lists the song
             at that has a recording with a track titled as the song. BIP's performance list is what
             makes this safe: a title match alone would give the 2015 "Home" the 2000 "Home".

No guessing beyond that: a wrong clip is worse than none.

A fourth, independent pass finds the "latest" clip: the newest show BIP lists the song at that has a
recording with a track titled as the song (the "later" rule, walked newest first).

Per-item file listings are cached in .cache/ia/ (items are effectively immutable once their MP3s are
derived and titled; a listing added in the last 30 days without titled audio is not cached). Each song's BIP performance list is cached
in .cache/bip/ until its last-played show or play count changes. Delete .cache/ to force a refresh.
The collection index and the BIP song catalog are fetched fresh on every build.
"""
import difflib
import functools
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import bip

COLLECTION = "DiscoBiscuits"
UA = bip.UA
CACHE = Path(__file__).resolve().parent / ".cache" / "ia"
PERF_CACHE = CACHE.parent / "bip"
AUDIO_FORMATS = ("VBR MP3", "MP3", "64Kbps MP3")  # browser-playable derivatives, best first
WORKERS = 8
FRESH_DAYS = 30  # an untitled or underived item this new may still be in progress; don't cache it


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
        return json.load(r)


def items_by_date() -> dict:
    """date -> [{identifier, downloads}] for every item in the collection, most downloaded first."""
    q = urllib.parse.urlencode([
        ("q", f"collection:{COLLECTION}"), ("fl[]", "identifier"), ("fl[]", "date"), ("fl[]", "downloads"),
        ("rows", "20000"), ("output", "json"),
    ])
    out: dict = {}
    for d in _get_json(f"https://archive.org/advancedsearch.php?{q}")["response"]["docs"]:
        if d.get("date"):
            out.setdefault(d["date"][:10], []).append({"identifier": d["identifier"], "downloads": d.get("downloads") or 0})
    for items in out.values():
        items.sort(key=lambda it: -it["downloads"])
    return out


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written via rename: parallel workers may write the same file, and a reader must never see half a file.
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


@functools.cache  # per build: an uncached listing would otherwise be fetched once per song that walks past it
def item_tracks(identifier: str) -> list:
    """Playable audio files of one item, in track order: [{file, title, length}]."""
    path = CACHE / f"{identifier}.json"
    if path.exists():
        return json.loads(path.read_text())
    meta = _get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    files = meta.get("files") or []
    tracks = []
    for fmt in AUDIO_FORMATS:
        tracks = [f for f in files if f.get("format") == fmt]
        if tracks:
            break
    tracks.sort(key=lambda f: (_int(f.get("track")), f["name"]))
    slim = [{"file": f["name"], "title": f.get("title") or "", "length": f.get("length") or ""} for f in tracks]
    # A fresh upload may still be deriving its MP3s or awaiting track titles (the 2026-07-03 SPAC items
    # appeared with every title blank), so leave it uncached and look again next build. Older items that
    # are still untitled stay that way and are cached like any other.
    added = str((meta.get("metadata") or {}).get("addeddate") or "")[:10]
    if any(t["title"] for t in slim) or added < (date.today() - timedelta(days=FRESH_DAYS)).isoformat():
        _write_json(path, slim)
    return slim


def performances(song: dict) -> list:
    """bip.song_performances, cached until the song's last-played show or play count changes."""
    key = f"{song['last_show']}|{song['plays']}"
    path = PERF_CACHE / f"{song['slug']}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if cached["key"] == key:
            return cached["perfs"]
    perfs = bip.song_performances(song["slug"])
    _write_json(path, {"key": key, "perfs": perfs})
    return perfs


def _safe_tracks(identifier: str):
    try:
        return item_tracks(identifier)
    except Exception as e:  # one bad item must not sink the build; it just contributes no audio
        print(f"archive.org: skipped {identifier}: {e}")
        return None


def _int(v) -> int:
    m = re.match(r"\d+", str(v or ""))
    return int(m.group()) if m else 1 << 30


def _clock(length: str) -> str:
    """archive.org lengths come as '06:02' or '361.23' seconds; show both as m:ss."""
    if re.fullmatch(r"\d+(\.\d+)?", length or ""):
        sec = round(float(length))
        return f"{sec // 60}:{sec % 60:02d}"
    return length or ""


# ---------- title matching ----------

# Trailing words tapers append to a song's name for a partial or variant performance. Deliberately no
# numbers or "part": "Rock and Roll Part 2" must not become a clip of "Rock and Roll".
_SUFFIXES = {"jam", "reprise", "inverted", "unfinished", "ending", "intro", "outro", "finish", "dyslexic", "tease", "segment"}
_PLACEHOLDERS = {"new song", "new", "unknown", "untitled", "unknown song", "new tune", "new jam"}


def norm(s: str, keep_brackets: bool = False) -> str:
    s = s.lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"in'(?=\W|$)", "ing", s)  # goin' -> going, startin' -> starting
    s = re.sub(r"(?<![a-z])'?til(?![a-z])", "till", s)  # 'til / til -> till
    s = s.replace("'", "").replace(".", "")  # don't -> dont, L.A. -> la, M.E.M.P.H.I.S. -> memphis
    # Tapers put (1), [unfinished], (w/ guest) in brackets; a few song titles keep real words there.
    s = re.sub(r"[()\[\]]", " ", s) if keep_brackets else re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())
    return s[4:] if s.startswith("the ") else s


def track_parts(title: str) -> list:
    """'05. Basis for a Day > Helicopters ->' -> ['basis for a day', 'helicopters'].
    Only segue arrows separate songs; commas and slashes occur inside real titles."""
    title = re.sub(r"^\s*\d{1,2}\s*[.)]\s+", "", title)
    return [p for p in (norm(x) for x in re.split(r"-?>", title)) if p]


def _strip_suffixes(p: str) -> str:
    words = p.split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def matches(song_title: str, track_title: str) -> bool:
    wants = {norm(song_title), norm(song_title, keep_brackets=True)}
    wants |= {w[:-4] for w in wants if w.endswith(" jam")}  # BIP's "The Fifth Element Jam" is a taper's "The Fifth Element"
    wants.discard("")
    return any(part in wants or _strip_suffixes(part) in wants for part in track_parts(track_title))


def resembles(song_title: str, track_title: str) -> bool:
    """Looser test, used only for a track already pinned down by setlist position: a misspelling
    ('Haleakala Crate'), an early working title ('Pit Bull Ricky'), part of a mashup's name, or a placeholder."""
    want = norm(song_title)
    for part in track_parts(track_title) or [""]:
        bare = re.sub(r"\d+$", "", _strip_suffixes(part)).strip()
        if bare in _PLACEHOLDERS or not bare:
            return True
        if want.startswith(bare + " ") or bare.startswith(want + " "):
            return True
        if difflib.SequenceMatcher(None, want, bare).ratio() >= 0.6:
            return True
    return False


# ---------- passes ----------

def _hit(identifier: str, n: int, t: dict, how: str) -> dict:
    return {"id": identifier, "file": t["file"], "track": t["title"], "length": _clock(t["length"]), "n": n + 1, "how": how}


def _debut_by_title(songs: list, by_date: dict, tracks: dict) -> tuple:
    """Pass 1. Per date the item matching the most of that night's debuts wins (ties: most downloaded),
    so one night's clips come from one recording; that item is also the night's "full show" link."""
    wanted: dict = {}
    for s in songs:
        if s["date"] in by_date:
            wanted.setdefault(s["date"], []).append(s)
    found, recordings = {}, {}
    for date, night in wanted.items():
        best = None
        for it in by_date[date]:
            hits = {}
            for n, t in enumerate(tracks.get(it["identifier"]) or []):
                for s in night:
                    if s["slug"] not in hits and matches(s["title"], t["title"]):
                        hits[s["slug"]] = _hit(it["identifier"], n, t, "title")
            score = (len(hits), it["downloads"])
            if tracks.get(it["identifier"]) and (best is None or score > best[0]):
                best = (score, hits, it["identifier"])
        if best:
            found.update(best[1])
            recordings[date] = best[2]
    return found, recordings


def _debut_by_setlist(song: dict, catalog: dict, recording: str) -> dict:
    """Pass 2 for one song whose debut recording exists but has no track titled as it."""
    ids = bip.show_setlist(song["show_id"])
    if song["id"] not in ids:
        return None
    tracks = item_tracks(recording)
    pos = [next((j for j, t in enumerate(tracks) if catalog.get(sid) and matches(catalog[sid], t["title"])), None) for sid in ids]
    k = ids.index(song["id"])
    prev_j = next((pos[m] for m in range(k - 1, -1, -1) if pos[m] is not None), None)
    next_j = next((pos[m] for m in range(k + 1, len(pos)) if pos[m] is not None), None)
    if prev_j is None or next_j is None or next_j - prev_j != 2:
        return None
    j = prev_j + 1
    if j in pos:  # that track already belongs to another song in the setlist
        return None
    t = tracks[j]
    if any(matches(title, t["title"]) for sid, title in catalog.items() if sid != song["id"]):
        return None  # titled as a different song: the taper and BIP disagree, so don't pick a side
    if not resembles(song["title"], t["title"]):
        return None
    return _hit(recording, j, t, "setlist")


def _perf_hit(song: dict, perf: dict, by_date: dict, how: str) -> dict:
    """A track titled as the song on any recording of one BIP performance (most downloaded item first)."""
    for it in by_date.get(perf["date"], []):
        for n, t in enumerate(_safe_tracks(it["identifier"]) or []):
            if matches(song["title"], t["title"]):
                region = perf["state"] if perf["country"] in ("", "United States") else perf["country"]
                return {**_hit(it["identifier"], n, t, how), "date": perf["date"], "show": perf["show"],
                        "venue": perf["venue"], "place": ", ".join(p for p in (perf["city"], region) if p)}
    return None


def _earliest_later(song: dict, by_date: dict) -> dict:
    """Pass 3 for one song: walk BIP's performances oldest first, skipping the debut."""
    for perf in performances(song):
        if perf["date"] > song["date"]:
            hit = _perf_hit(song, perf, by_date, "later")
            if hit:
                return hit
    return None


def _latest(song: dict, by_date: dict) -> dict:
    """Latest pass for one song: walk BIP's performances newest first."""
    for perf in reversed(performances(song)):
        hit = _perf_hit(song, perf, by_date, "latest")
        if hit:
            return hit
    return None


def find_audio(songs: list) -> tuple:
    """songs: [{slug, id, title, date, show_id, last_show, plays}] with `date` the debut.

    Returns ({song slug: debut-preferred audio dict}, {song slug: latest audio dict}, {debut date: identifier
    of that night's recording}). A song's latest clip is omitted when it comes from the same show as its
    first clip, i.e. when no newer recording exists."""
    by_date = items_by_date()
    catalog = {s["id"]: s["title"] for s in songs}

    debut_idents = sorted({it["identifier"] for s in songs for it in by_date.get(s["date"], [])})
    with ThreadPoolExecutor(WORKERS) as ex:
        tracks = dict(zip(debut_idents, ex.map(_safe_tracks, debut_idents)))
    found, recordings = _debut_by_title(songs, by_date, tracks)

    def setlist_pass(s):
        try:
            return s["slug"], _debut_by_setlist(s, catalog, recordings[s["date"]])
        except Exception as e:
            print(f"setlist alignment skipped {s['slug']}: {e}")
            return s["slug"], None

    todo = [s for s in songs if s["slug"] not in found and s["date"] in recordings]
    with ThreadPoolExecutor(WORKERS) as ex:
        found.update({slug: hit for slug, hit in ex.map(setlist_pass, todo) if hit})

    def later_pass(s):
        try:
            return s["slug"], _earliest_later(s, by_date)
        except Exception as e:
            print(f"later-recording search skipped {s['slug']}: {e}")
            return s["slug"], None

    todo = [s for s in songs if s["slug"] not in found and s["plays"] > 1]
    with ThreadPoolExecutor(WORKERS) as ex:
        found.update({slug: hit for slug, hit in ex.map(later_pass, todo) if hit})

    def latest_pass(s):
        try:
            return s["slug"], _latest(s, by_date)
        except Exception as e:
            print(f"latest-recording search skipped {s['slug']}: {e}")
            return s["slug"], None

    first_date = {s["slug"]: found[s["slug"]].get("date", s["date"]) for s in songs if s["slug"] in found}
    todo = [s for s in songs if s["plays"] > 1]
    with ThreadPoolExecutor(WORKERS) as ex:
        latest = {slug: hit for slug, hit in ex.map(latest_pass, todo) if hit and hit["date"] != first_date.get(slug)}
    return found, latest, recordings
