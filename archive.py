"""Find each song's debut performance in the Disco Biscuits archive.org collection.

A song gets audio only when a recording of its *debut show* exists and one of that recording's
tracks is titled as that song. No fallback to other performances or fuzzy guesses: a wrong clip
under a "first time played" heading is worse than no clip.

Per-item file listings are cached in .cache/ia/ (items are effectively immutable); delete the
directory to force a refresh. The collection index itself is fetched fresh on every build.
"""
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

COLLECTION = "DiscoBiscuits"
UA = {"User-Agent": "bisco-first-played/1.0 (fan timeline; github.com/avdrav1/bisco-first-played)"}
CACHE = Path(__file__).resolve().parent / ".cache" / "ia"
AUDIO_FORMATS = ("VBR MP3", "MP3", "64Kbps MP3")  # browser-playable derivatives, best first


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
        return json.load(r)


def items_by_date() -> dict:
    """date -> [{identifier, downloads}] for every item in the collection."""
    q = urllib.parse.urlencode([
        ("q", f"collection:{COLLECTION}"), ("fl[]", "identifier"), ("fl[]", "date"), ("fl[]", "downloads"),
        ("rows", "20000"), ("output", "json"),
    ])
    out: dict = {}
    for d in _get_json(f"https://archive.org/advancedsearch.php?{q}")["response"]["docs"]:
        if d.get("date"):
            out.setdefault(d["date"][:10], []).append({"identifier": d["identifier"], "downloads": d.get("downloads") or 0})
    return out


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
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slim))
    return slim


def _int(v) -> int:
    m = re.match(r"\d+", str(v or ""))
    return int(m.group()) if m else 1 << 30


# Trailing words tapers append to a song's name for a partial or variant performance. Deliberately no
# numbers or "part": "Rock and Roll Part 2" must not become a clip of "Rock and Roll".
_SUFFIXES = {"jam", "reprise", "inverted", "unfinished", "ending", "intro", "outro", "finish", "dyslexic", "tease", "segment"}


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


def find_debut_audio(songs: list, workers: int = 8) -> tuple:
    """songs: [{slug, title, date}] (debut performances).

    Returns ({song slug: audio dict}, {date: identifier of that night's recording}). Keyed by slug, not
    title: the catalog has two different songs called "Home", debuting fifteen years apart.
    Per date, every item's tracks are checked and the item matching the most of that night's debuts
    wins (ties: most downloaded), so one night's clips come from one recording where possible. Dates
    with a recording but no matched track still get the most-downloaded item, for a "full show" link."""
    by_date = items_by_date()
    wanted = {}
    for s in songs:
        if s["date"] in by_date:
            wanted.setdefault(s["date"], []).append(s)
    idents = [it["identifier"] for d in wanted for it in by_date[d]]
    with ThreadPoolExecutor(workers) as ex:
        tracks = dict(zip(idents, ex.map(_safe_tracks, idents)))

    found, recordings = {}, {}
    for date, night in wanted.items():
        best = None
        for it in by_date[date]:
            hits = {}
            for n, t in enumerate(tracks[it["identifier"]] or []):
                for s in night:
                    if s["slug"] not in hits and matches(s["title"], t["title"]):
                        hits[s["slug"]] = {"id": it["identifier"], "file": t["file"], "track": t["title"], "length": _clock(t["length"]), "n": n + 1}
            score = (len(hits), it["downloads"])
            if tracks[it["identifier"]] and (best is None or score > best[0]):
                best = (score, hits, it["identifier"])
        if best:
            found.update(best[1])
            recordings[date] = best[2]
    return found, recordings


def _clock(length: str) -> str:
    """archive.org lengths come as '06:02' or '361.23' seconds; show both as m:ss."""
    if re.fullmatch(r"\d+(\.\d+)?", length or ""):
        sec = round(float(length))
        return f"{sec // 60}:{sec % 60:02d}"
    return length or ""


def _safe_tracks(identifier: str):
    try:
        return item_tracks(identifier)
    except Exception as e:  # one bad item must not sink the build; it just contributes no audio
        print(f"archive.org: skipped {identifier}: {e}")
        return None
