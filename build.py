#!/usr/bin/env python3
"""Pull every song from the Biscuits Internet Project and build the FIRST TIME PLAYED timeline.

Sources: https://discobiscuits.net/api/songs (one request, full catalog, each song embeds its
firstPlayedShow + venue), and the archive.org DiscoBiscuits collection for recordings of each
song's debut performance (see archive.py). Outputs, next to this script:
  index.html               self-contained interactive timeline (audio streams from archive.org)
  songs_first_played.csv   flat export of the same data
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import archive

API = "https://discobiscuits.net/api/songs"
SITE = "https://discobiscuits.net"
HERE = Path(__file__).resolve().parent

# Era boundaries exactly as BIP's own "Sammy/Allen/Marlon Era" filter defines them
# (assets/segue-run-*.js). Dates outside every range (the fall-2005 drummer search) are
# reported as "Between eras" rather than forced into a neighbour.
ERAS = [
    ("Sammy", "Sam Altman", None, "2005-08-27"),
    ("Allen", "Allen Aucoin", "2005-12-28", "2025-09-07"),
    ("Marlon", "Marlon Lewis", "2025-10-31", None),
]


def era_of(date: str) -> str:
    for name, _, start, end in ERAS:
        if (start is None or date >= start) and (end is None or date <= end):
            return name
    return "Between"


def place(venue: dict) -> str:
    city, state, country = venue.get("city") or "", venue.get("state") or "", venue.get("country") or ""
    region = state if country in ("", "United States") else country
    return ", ".join(p for p in (city, region) if p)


def fetch() -> list:
    req = urllib.request.Request(API, headers={"User-Agent": "bisco-first-played/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> None:
    raw = fetch()
    songs, missing = [], []
    for s in raw:
        show = s.get("firstPlayedShow")
        if not show:
            missing.append(s["title"])
            continue
        venue = show.get("venue") or {}
        songs.append({
            "title": s["title"],
            "slug": s["slug"],
            "kind": s.get("kind") or "unknown",
            "authors": [
                {"name": a["name"], "href": f"/musicians/{a['musicianSlug']}" if a.get("musicianSlug") else f"/authors/{a['slug']}"}
                for a in s.get("authors") or []
            ],
            "date": show["date"],
            "era": era_of(show["date"]),
            "show": show["slug"],
            "venue": venue.get("name") or "Unknown venue",
            "place": place(venue),
            "plays": s.get("timesPlayed") or 0,
        })
    songs.sort(key=lambda x: (x["date"], x["title"].lower()))

    audio, recordings = archive.find_debut_audio(songs)
    for x in songs:
        if x["slug"] in audio:
            x["audio"] = audio[x["slug"]]

    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = {
        "fetched": fetched,
        "site": SITE,
        "eras": [{"name": n, "drummer": d, "start": s, "end": e} for n, d, s, e in ERAS],
        "songs": songs,
        "recordings": recordings,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = (HERE / "template.html").read_text(encoding="utf-8")
    marker = "/*__DATA__*/null"
    if marker not in template:
        sys.exit("template.html is missing the data marker")
    (HERE / "index.html").write_text(template.replace(marker, data), encoding="utf-8")

    with open(HERE / "songs_first_played.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["song", "type", "authors", "first_played", "era", "venue", "location", "times_played", "show_url", "song_url",
                    "debut_audio_url"])
        for x in songs:
            a = x.get("audio")
            w.writerow([x["title"], x["kind"], "; ".join(a_["name"] for a_ in x["authors"]), x["date"], x["era"],
                        x["venue"], x["place"], x["plays"], f"{SITE}/shows/{x['show']}", f"{SITE}/songs/{x['slug']}",
                        f"https://archive.org/download/{a['id']}/{urllib.parse.quote(a['file'])}" if a else ""])

    print(f"{len(raw)} songs from API, {len(songs)} with a first-played show, "
          f"{len(audio)} with debut audio on archive.org, fetched {fetched}")
    if missing:
        print(f"no first-played show: {', '.join(missing)}")


if __name__ == "__main__":
    main()
