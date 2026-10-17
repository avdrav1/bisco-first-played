# FIRST TIME PLAYED: The Disco Biscuits

An interactive timeline of every song The Disco Biscuits have played, placed on the night it debuted. Click a song to hear that debut, streamed from the band's live archive on archive.org.

**[Open the timeline →](https://avdrav1.github.io/bisco-first-played/)**

The data comes from the [Biscuits Internet Project](https://discobiscuits.net) (BIP), the fan-run database of every show and setlist. The page rebuilds itself every day, so new debuts and "last played" dates follow the tour without anyone touching it.

## What you can do

- **See the catalog by debut.** Each square is one song. Filter by year or drag a date range.
- **Filter** by era (Sammy, Allen, or Marlon on drums), by original or cover, by songwriter, or by title.
- **Hear the debut.** 436 of the 552 songs have a recording on archive.org. Where the debut was never recorded, you get the earliest recording that exists.
- **Hear the latest version** next to the first one, to hear how a song grew.
- **Check each song's history**: venue and city of the debut, times played, the last show, and how many shows have passed since.
- **Download the data** as [`songs_first_played.csv`](songs_first_played.csv).

## How it works

```
discobiscuits.net/api/songs ──┐
                              ├─ build.py ─► index.html (self-contained page)
archive.org DiscoBiscuits ────┘            └► songs_first_played.csv
```

`build.py` pulls the full song catalog from BIP in one request. Each song carries its debut show, last show, and play count.

`archive.py` finds a playable clip for each song. It runs three passes, most trustworthy first, and each song keeps the first one that succeeds:

1. **By title.** A track on the debut night's recording carries the song's name. When a night has several recordings, the one that matches the most debuts wins, so one night's clips come from one tape.
2. **By setlist position.** The tape has no track with that name, but BIP's setlist puts the song in a one-track gap between two tracks that did match. The script accepts that track only if its title resembles the song or is a placeholder like "New Song". A track titled as a different song is refused.
3. **Earliest later recording.** The debut was never taped. The script walks BIP's list of later performances, oldest first, and takes the first one with a matching track. Using BIP's list, not a bare title search, keeps the 2015 song "Home" from picking up a 2000 track of the same name.

A fourth pass walks the same list newest first to find the latest recording.

Matching handles how tapers actually label tracks: segue arrows (`Basis for a Day > Helicopters`), dropped g's (`goin'`), bracketed notes like `[unfinished]`, and suffixes like "jam" or "reprise". The rule throughout is that a wrong clip is worse than no clip.

`bip.py` reads data that BIP's public API doesn't expose. BIP is a React Router app, and each page also serves its loader data in turbo-stream form. `bip.py` decodes that format back into plain JSON.

## Running it

Python 3 and the standard library. No dependencies.

```sh
python3 build.py
open index.html
```

The first run fetches file listings for every recording in the archive, so it takes a few minutes. Later runs read from `.cache/` and finish fast. Delete `.cache/` to force a full refresh.

## Daily rebuild

[`.github/workflows/rebuild.yml`](.github/workflows/rebuild.yml) runs `build.py` every morning after US shows end. It commits only when the CSV changes, then asks GitHub Pages to republish.

## Eras

Era boundaries match BIP's own filter:

| Era | Drummer | Dates |
|---|---|---|
| Sammy | Sam Altman | through 2005-08-27 |
| Allen | Allen Aucoin | 2005-12-28 to 2025-09-07 |
| Marlon | Marlon Lewis | from 2025-10-31 |

Debuts during the fall 2005 drummer search show as "Between eras."

## Credits

Setlist data: the [Biscuits Internet Project](https://discobiscuits.net). Recordings: the tapers who share shows in the [Disco Biscuits collection](https://archive.org/details/DiscoBiscuits) on archive.org. This is a fan project with no affiliation with the band.

---

Built by [Avniel Dravid](https://github.com/avdrav1) · [LinkedIn](https://www.linkedin.com/in/dravid)
