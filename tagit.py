#!/usr/local/bin/python3
"""tagit: finds a song's real metadata, shows it, and writes it into the file on a yes.

Usage:
  tagit FILE_OR_FOLDER...            look each song up, show what was found, ask before writing
  tagit -q "Artist - Title" FILE     search for that instead of what the file says
  tagit -n FILE...                   only show, never write
  tagit -y FILE...                   write confident matches without asking
  tagit --send / --no-send FILE...   then send to the configured target without asking / never

Sources, none needing a key: Deezer finds the release (ISRC, UPC, label, date, position,
BPM, cover); Apple adds the genre, the copyright, the artist's spelling and a 1400px cover;
MusicBrainz adds its ids by ISRC; LRCLIB the lyrics, synced when it has them; ffmpeg
measures ReplayGain 2.0. Formats: MP3, FLAC, M4A/ALAC, Ogg Vorbis, Opus, WAV, AIFF.

Sending is optional and off until configured: with send.target set in
~/.config/tagit/config.toml (or TAGIT_SEND_TARGET), a tagged file can be copied with scp
to "user@host:/folder", uploaded under a hidden name and renamed once whole.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import difflib
import json
import os
import re
import shlex
import shutil
import ssl
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

try:
    import mutagen
    from mutagen.aiff import AIFF
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3, TXXX, UFID, USLT, Frames
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen.wave import WAVE
except ImportError:
    sys.exit("tagit needs mutagen:  python3 -m pip install mutagen")

try:
    import tomllib
except ImportError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

__version__ = "1.0.0"
CONFIG_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "tagit", "config.toml")
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wav", ".aif", ".aiff"}
UA = f"tagit/{__version__} ( https://github.com/adrbn/tagit )"
DEEZER = "https://api.deezer.com"
APPLE_COUNTRIES = ("US", "GB", "FR", "CA", "DE")
CONFIDENT = 0.7
RG_REFERENCE = -18.0  # ReplayGain 2.0, in LUFS
R128_REFERENCE = -23.0  # Opus' R128 gain tags

FIELDS = [
    ("title", "Title"), ("artist", "Artist"), ("artists", "Artists"),
    ("albumartist", "Album artist"), ("album", "Album"),
    ("tracknumber", "Track"), ("tracktotal", "Tracks"), ("discnumber", "Disc"), ("disctotal", "Discs"),
    ("date", "Date"), ("originaldate", "Original date"), ("genre", "Genre"),
    ("releasetype", "Release type"), ("label", "Label"), ("catalognumber", "Catalog #"),
    ("copyright", "Copyright"), ("isrc", "ISRC"), ("barcode", "Barcode"),
    ("releasecountry", "Country"), ("releasestatus", "Status"), ("media", "Media"),
    ("bpm", "BPM"), ("explicit", "Advisory"), ("lyrics", "Lyrics"),
    ("replaygain_track_gain", "RG track gain"), ("replaygain_track_peak", "RG track peak"),
    ("replaygain_album_gain", "RG album gain"), ("replaygain_album_peak", "RG album peak"),
    ("musicbrainz_trackid", "MB recording"), ("musicbrainz_albumid", "MB release"),
    ("musicbrainz_releasegroupid", "MB release group"), ("musicbrainz_artistid", "MB artists"),
    ("musicbrainz_albumartistid", "MB album artist"),
]
LABELS = dict(FIELDS)
LIST_FIELDS = {"artists", "musicbrainz_artistid", "musicbrainz_albumartistid"}

# ---------------------------------------------------------------- output

TTY = sys.stdout.isatty()


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def warn(text: str) -> None:
    print(paint("33", f"  ! {text}"), file=sys.stderr)


def mmss(seconds: float | None) -> str:
    if not seconds:
        return "?"
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def ask(prompt: str, default: str = "") -> str:
    """An answer from the keyboard; Enter takes the default. With no one left to answer
    (stdin closed), EOFError stops everything rather than taking defaults unasked."""
    return input(prompt).strip() or default


# ---------------------------------------------------------------- network


def tls() -> ssl.SSLContext:
    """python.org's Python ships without CA certificates: certifi's, else the system's."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        bundle = "/etc/ssl/cert.pem"
        return ssl.create_default_context(cafile=bundle if os.path.exists(bundle) else None)


TLS = tls()


def fetch(url: str, params: dict | None = None, raw: bool = False):
    """JSON (or bytes) from a URL; None on a 404 or once retries run out."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    host = urllib.parse.urlsplit(url).netloc
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15, context=TLS) as response:
                body = response.read()
            return body if raw else json.loads(body)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            warn(f"{host}: HTTP {error.code}")
            return None
        except (urllib.error.URLError, OSError, ValueError) as error:
            if attempt < 2:
                time.sleep(1)
                continue
            warn(f"{host}: {error}")
            return None
    return None


# ---------------------------------------------------------------- matching

BRACKET_FEAT = re.compile(r"\s*[\(\[](?:feat|ft|featuring|with)\b[^\)\]]*[\)\]]", re.I)
TAIL_FEAT = re.compile(r"\s+(?:feat|ft|featuring)\b\.?\s.*$", re.I)
NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:official|lyrics?|audio|video|visuali[sz]er|clip|paroles|hq|hd|4k)\b[^\)\]]*[\)\]]",
    re.I)
ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|\bx\b|\band\b|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\bvs\b\.?)\s*", re.I)


def fold(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower().replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def strip_feat(title: str) -> str:
    return TAIL_FEAT.sub("", BRACKET_FEAT.sub("", NOISE.sub("", title or ""))).strip()


def core_title(title: str | None) -> str:
    return fold(strip_feat(title or ""))


def core_album(album: str | None) -> str:
    return fold(re.sub(r"\s+-\s+(single|ep)$", "", album or "", flags=re.I))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def artist_names(text: str | None) -> list[str]:
    return [part for part in ARTIST_SPLIT.split(text or "") if part and part.strip()]


def artist_score(candidate: str | None, wanted: str | None) -> float:
    if not wanted:
        return 0.5
    have, want = fold(candidate), fold(wanted)
    if have and (have in want or want in have):
        return 1.0
    return max([similarity(have, fold(part)) for part in artist_names(wanted)] + [similarity(have, want)])


def duration_score(a: float | None, b: float | None) -> float:
    if not a or not b:
        return 0.5
    off = abs(a - b)
    return 1.0 if off <= 2 else max(0.0, 1 - (off - 2) / 10)


def split_query(text: str) -> tuple[str, str]:
    for sep in (" - ", " – ", " — ", " -- "):
        if sep in text:
            artist, title = text.split(sep, 1)
            return artist.strip(), title.strip()
    return "", text.strip()


def guess_from_name(path: str) -> tuple[str, str]:
    stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
    stem = re.sub(r"^\s*\d{1,3}\s*[-.)]\s*", "", stem)  # a leading "01 - "
    return split_query(NOISE.sub("", stem))


# ---------------------------------------------------------------- Deezer


def deezer_hits(artist: str, title: str, duration: float) -> list[dict]:
    """Deezer's songs for a guess, best first, each with its `score` from 0 to 1."""
    bare = strip_feat(title)
    queries = []
    if artist:
        queries.append(f'artist:"{artist}" track:"{bare}"')
        names = artist_names(artist)
        if names and fold(names[0]) != fold(artist):
            queries.append(f'artist:"{names[0]}" track:"{bare}"')
    queries.append(f"{artist} {bare}".strip())
    seen, hits = set(), []
    for query in queries:
        for hit in (fetch(f"{DEEZER}/search", {"q": query, "limit": 25}) or {}).get("data", []):
            if hit.get("id") in seen or not hit.get("title"):
                continue
            seen.add(hit["id"])
            named = max(similarity(core_title(hit["title"]), core_title(title)),
                        similarity(core_title(hit.get("title_short")), core_title(title)))
            score = (0.5 * named + 0.3 * artist_score(hit["artist"]["name"], artist)
                     + 0.2 * duration_score(hit.get("duration"), duration))
            hits.append({**hit, "score": score})
    return sorted(hits, key=lambda h: (-h["score"], -h.get("rank", 0)))


def deezer_track(hits: list[dict]) -> tuple[dict, dict] | None:
    """The best hit's full record. Among near ties — the same song on its single and on
    later compilations — the earliest release wins."""
    if not hits:
        return None
    top = [h for h in hits if h["score"] >= hits[0]["score"] - 0.03][:5]
    found = []
    for hit in top:
        track = fetch(f"{DEEZER}/track/{hit['id']}")
        if track and "error" not in track:
            found.append((hit, track))
    if not found:
        return None
    return min(found, key=lambda pair: (pair[1].get("release_date") or "9999", -pair[0].get("rank", 0)))


# ---------------------------------------------------------------- Apple, MusicBrainz, LRCLIB


def apple(artist: str, title: str, album: str, duration: float) -> dict | None:
    term = f"{artist} {strip_feat(title)}"
    for country in APPLE_COUNTRIES:
        page = fetch("https://itunes.apple.com/search",
                     {"term": term, "entity": "song", "limit": 25, "country": country}) or {}
        best, best_score = None, 0.0
        for song in page.get("results", []):
            length = (song.get("trackTimeMillis") or 0) / 1000
            if song.get("wrapperType") != "track" or (duration and length and abs(length - duration) > 4):
                continue
            score = (0.55 * similarity(core_title(song.get("trackName")), core_title(title))
                     + 0.3 * artist_score(song.get("artistName"), artist)
                     + 0.15 * similarity(core_album(song.get("collectionName")), core_album(album)))
            if score > best_score:
                best, best_score = song, score
        if best and best_score >= CONFIDENT:
            lookup = fetch("https://itunes.apple.com/lookup", {"id": best["collectionId"], "country": country})
            collection = ((lookup or {}).get("results") or [{}])[0]
            return {"song": best, "collection": collection, "country": country}
    return None


def credit_phrase(credit: list[dict]) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credit)


def musicbrainz(isrc: str | None, album: str) -> dict:
    if not isrc:
        return {}
    found = fetch(f"https://musicbrainz.org/ws/2/isrc/{isrc}", {"inc": "artist-credits+releases", "fmt": "json"})
    recordings = (found or {}).get("recordings") or []
    if not recordings:
        return {}
    recording = recordings[0]
    out = {
        "musicbrainz_trackid": recording["id"],
        "musicbrainz_artistid": [c["artist"]["id"] for c in recording.get("artist-credit", [])],
        "originaldate": recording.get("first-release-date"),
    }
    releases = sorted(recording.get("releases", []), key=lambda r: (
        -similarity(core_album(r.get("title")), core_album(album)),
        r.get("status") != "Official", r.get("date") or "9999"))
    if not releases:
        return out
    time.sleep(1.1)  # MusicBrainz allows one request a second
    release = fetch(f"https://musicbrainz.org/ws/2/release/{releases[0]['id']}",
                    {"inc": "release-groups+labels+artist-credits+media", "fmt": "json"}) or {}
    if not release:
        return out
    group = release.get("release-group") or {}
    labels = release.get("label-info") or [{}]
    kinds = [group.get("primary-type")] + (group.get("secondary-types") or [])
    return {**out,
            "musicbrainz_albumid": release.get("id"),
            "musicbrainz_releasegroupid": group.get("id"),
            "musicbrainz_albumartistid": [c["artist"]["id"] for c in release.get("artist-credit", [])],
            "albumartist": credit_phrase(release.get("artist-credit", [])) or None,
            "label": (labels[0].get("label") or {}).get("name"),
            "catalognumber": labels[0].get("catalog-number"),
            "barcode": release.get("barcode") or None,
            "releasecountry": release.get("country"),
            "releasestatus": (release.get("status") or "").lower() or None,
            "releasetype": "compilation" if "Compilation" in kinds else (group.get("primary-type") or "").lower() or None,
            "disctotal": str(len(release["media"])) if release.get("media") else None}


def lyrics(artists: list[str], title: str, album: str, duration: float) -> tuple[str, str] | None:
    """(text, "synced" | "plain") from LRCLIB."""
    def usable(entry):
        if not entry or entry.get("instrumental"):
            return None
        if entry.get("syncedLyrics"):
            return entry["syncedLyrics"].strip(), "synced"
        if entry.get("plainLyrics"):
            return entry["plainLyrics"].strip(), "plain"
        return None

    bare = strip_feat(title)
    for artist in artists:
        entry = fetch("https://lrclib.net/api/get", {"artist_name": artist, "track_name": bare,
                                                     "album_name": album, "duration": round(duration)})
        if usable(entry):
            return usable(entry)
    for artist in artists:
        for entry in fetch("https://lrclib.net/api/search", {"artist_name": artist, "track_name": bare}) or []:
            close = abs((entry.get("duration") or 0) - duration) <= 3
            if close and similarity(core_title(entry.get("trackName")), core_title(bare)) > 0.85 and usable(entry):
                return usable(entry)
    return None


# ---------------------------------------------------------------- loudness, cover


def loudness(path: str) -> tuple[float, float] | None:
    """(integrated LUFS, true peak dBTP) by ffmpeg's EBU R128 meter."""
    ffmpeg = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
    try:
        run = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
                              "-af", "ebur128=peak=true", "-f", "null", "-"],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        warn(f"ffmpeg: {error}")
        return None
    summary = run.stderr.rsplit("Summary:", 1)[-1]
    integrated = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    peak = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf)", summary)
    if not integrated or not peak:
        warn("ffmpeg couldn't measure the loudness")
        return None
    return float(integrated.group(1)), float(peak.group(1)) if peak.group(1) != "-inf" else -120.0


def image_info(data: bytes) -> tuple[str | None, int, int]:
    """(mime, width, height) of a JPEG or PNG."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data[:2] != b"\xff\xd8":
        return None, 0, 0
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF or marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 1 if marker == 0xFF else 2
            continue
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return "image/jpeg", width, height
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return "image/jpeg", 0, 0


def cover(sources: list[tuple[str, str]]) -> dict | None:
    for source, url in sources:
        data = fetch(url, raw=True)
        if data:
            mime, width, height = image_info(data)
            if mime:
                return {"data": data, "mime": mime, "width": width, "height": height, "source": source}
    return None


# ---------------------------------------------------------------- the file's tags


def one(value):
    if isinstance(value, list):
        value = [str(v) for v in value if str(v).strip()]
        return value[0] if len(value) == 1 else (value or None)
    return value if value not in ("", None) else None


def split_pair(text: str) -> tuple[str | None, str | None]:
    number, _, total = str(text).partition("/")
    return number.strip() or None, total.strip() or None


ID3_TEXT = {"title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB", "date": "TDRC",
            "originaldate": "TDOR", "genre": "TCON", "label": "TPUB", "copyright": "TCOP", "isrc": "TSRC",
            "bpm": "TBPM", "media": "TMED"}
# Picard's names, which TagLib (so Navidrome) maps back to the same properties.
ID3_TXXX = {"artists": "ARTISTS", "barcode": "BARCODE", "catalognumber": "CATALOGNUMBER",
            "releasetype": "MusicBrainz Album Type", "releasecountry": "MusicBrainz Album Release Country",
            "releasestatus": "MusicBrainz Album Status", "explicit": "ITUNESADVISORY",
            "replaygain_track_gain": "REPLAYGAIN_TRACK_GAIN", "replaygain_track_peak": "REPLAYGAIN_TRACK_PEAK",
            "replaygain_album_gain": "REPLAYGAIN_ALBUM_GAIN", "replaygain_album_peak": "REPLAYGAIN_ALBUM_PEAK",
            "musicbrainz_albumid": "MusicBrainz Album Id",
            "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
            "musicbrainz_artistid": "MusicBrainz Artist Id",
            "musicbrainz_albumartistid": "MusicBrainz Album Artist Id"}
MB_OWNER = "http://musicbrainz.org"
MP4_ATOMS = {"title": "©nam", "artist": "©ART", "albumartist": "aART", "album": "©alb", "date": "©day",
             "genre": "©gen", "copyright": "cprt", "lyrics": "©lyr"}
MP4_FREEFORM = {**ID3_TXXX, "label": "LABEL", "isrc": "ISRC", "media": "MEDIA", "originaldate": "ORIGINALDATE",
                "musicbrainz_trackid": "MusicBrainz Track Id"}
for _key in ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak"):
    MP4_FREEFORM[_key] = _key
del MP4_FREEFORM["explicit"]
VORBIS = {key: ("ITUNESADVISORY" if key == "explicit" else key.upper()) for key, _ in FIELDS}
FREEFORM = "----:com.apple.iTunes:"


class Tags:
    """A file's tags through one vocabulary, whatever the format."""

    def __init__(self, path: str):
        self.path = path
        self.audio = mutagen.File(path)
        if self.audio is None:
            raise ValueError("not an audio file mutagen can read")
        audio = self.audio
        if isinstance(audio, MP4):
            self.kind = "mp4"
        elif isinstance(audio, FLAC):
            self.kind = "flac"
        elif isinstance(audio, (OggVorbis, OggOpus)):
            self.kind = "ogg"
        elif isinstance(audio, (MP3, WAVE, AIFF)):
            self.kind = "id3"
        else:
            raise ValueError(f"{type(audio).__name__} files aren't supported")
        if audio.tags is None:
            audio.add_tags()
        self.opus = isinstance(audio, OggOpus)

    @property
    def duration(self) -> float:
        return float(getattr(self.audio.info, "length", 0) or 0)

    @property
    def format(self) -> str:
        return {"mp4": "M4A", "flac": "FLAC", "ogg": "Opus" if self.opus else "Ogg"}.get(
            self.kind, type(self.audio).__name__)

    # reading

    def read(self) -> tuple[dict, bytes | None]:
        return getattr(self, f"_read_{'vorbis' if self.kind in ('flac', 'ogg') else self.kind}")()

    def _read_id3(self):
        tags, out = self.audio.tags, {}
        for key, frame_id in ID3_TEXT.items():
            if tags.getall(frame_id):
                out[key] = one([str(t) for t in tags.getall(frame_id)[0].text])
        for frame_id, keys in (("TRCK", ("tracknumber", "tracktotal")), ("TPOS", ("discnumber", "disctotal"))):
            if tags.getall(frame_id):
                out[keys[0]], out[keys[1]] = split_pair(tags.getall(frame_id)[0].text[0])
        by_desc = {desc.lower(): key for key, desc in ID3_TXXX.items()}
        for frame in tags.getall("TXXX"):
            if frame.desc.lower() in by_desc:
                out[by_desc[frame.desc.lower()]] = one(list(frame.text))
        if tags.getall("USLT"):
            out["lyrics"] = str(tags.getall("USLT")[0].text) or None
        for frame in tags.getall("UFID"):
            if frame.owner == MB_OWNER:
                out["musicbrainz_trackid"] = frame.data.decode("utf-8", "replace")
        pictures = sorted(tags.getall("APIC"), key=lambda p: p.type != 3)
        return out, (pictures[0].data if pictures else None)

    def _read_vorbis(self):
        tags, out = self.audio.tags, {}
        for key, name in VORBIS.items():
            if name in tags:
                out[key] = one(tags[name])
        if self.opus:
            for scope in ("track", "album"):
                r128 = one(tags.get(f"R128_{scope.upper()}_GAIN", []))
                if r128 and str(r128).lstrip("-").isdigit():
                    out[f"replaygain_{scope}_gain"] = f"{int(r128) / 256 + RG_REFERENCE - R128_REFERENCE:+.2f} dB"
        out["tracktotal"] = out.get("tracktotal") or one(tags.get("TOTALTRACKS", []))
        out["disctotal"] = out.get("disctotal") or one(tags.get("TOTALDISCS", []))
        for key in ("tracknumber", "discnumber"):
            if out.get(key) and "/" in str(out[key]):
                out[key], total = split_pair(out[key])
                out[key.replace("number", "total")] = out.get(key.replace("number", "total")) or total
        if self.kind == "flac":
            pictures = sorted(self.audio.pictures, key=lambda p: p.type != 3)
            return out, (pictures[0].data if pictures else None)
        blocks = tags.get("METADATA_BLOCK_PICTURE", [])
        try:
            return out, (Picture(base64.b64decode(blocks[0])).data if blocks else None)
        except (ValueError, mutagen.MutagenError):
            return out, None

    def _read_mp4(self):
        tags, out = self.audio.tags, {}
        for key, atom in MP4_ATOMS.items():
            if atom in tags:
                out[key] = one(list(tags[atom]))
        for atom, keys in (("trkn", ("tracknumber", "tracktotal")), ("disk", ("discnumber", "disctotal"))):
            if tags.get(atom):
                number, total = tags[atom][0]
                out[keys[0]], out[keys[1]] = str(number) or None, (str(total) if total else None)
        if tags.get("tmpo"):
            out["bpm"] = str(tags["tmpo"][0])
        if tags.get("rtng"):
            out["explicit"] = str(tags["rtng"][0])
        by_name = {(FREEFORM + name).lower(): key for key, name in MP4_FREEFORM.items()}
        for atom, values in tags.items():
            if atom.lower() in by_name:
                out[by_name[atom.lower()]] = one([bytes(v).decode("utf-8", "replace") for v in values])
        return out, (bytes(tags["covr"][0]) if tags.get("covr") else None)

    # writing

    def write(self, fields: dict, art: dict | None) -> None:
        values = {k: v for k, v in fields.items() if v not in (None, "", [])}
        getattr(self, f"_write_{'vorbis' if self.kind in ('flac', 'ogg') else self.kind}")(values, art)
        self.audio.save()

    def _write_id3(self, values, art):
        tags = self.audio.tags
        for key, frame_id in ID3_TEXT.items():
            if key in values:
                tags.setall(frame_id, [Frames[frame_id](encoding=3, text=as_list(values[key]))])
        for frame_id, number, total in (("TRCK", "tracknumber", "tracktotal"), ("TPOS", "discnumber", "disctotal")):
            if number in values:
                text = f"{values[number]}/{values[total]}" if total in values else str(values[number])
                tags.setall(frame_id, [Frames[frame_id](encoding=3, text=[text])])
        for key, desc in ID3_TXXX.items():
            if key in values:
                for frame in tags.getall("TXXX"):
                    if frame.desc.lower() == desc.lower():
                        del tags[frame.HashKey]
                tags.add(TXXX(encoding=3, desc=desc, text=as_list(values[key])))
        if "lyrics" in values:
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="XXX", desc="", text=values["lyrics"]))
        if "musicbrainz_trackid" in values:
            tags.delall(f"UFID:{MB_OWNER}")
            tags.add(UFID(owner=MB_OWNER, data=values["musicbrainz_trackid"].encode()))
        if art:
            for frame in tags.getall("APIC"):
                if frame.type in (0, 3):
                    del tags[frame.HashKey]
            tags.add(APIC(encoding=3, mime=art["mime"], type=3, desc="Cover", data=art["data"]))

    def _write_vorbis(self, values, art):
        tags = self.audio.tags
        for key, value in values.items():
            if self.opus and key.startswith("replaygain_"):
                continue
            tags[VORBIS[key]] = as_list(value)
        if self.opus:
            for scope in ("track", "album"):
                gain = values.get(f"replaygain_{scope}_gain")
                if gain:
                    # R128 gains are Q7.8 fixed point against -23 LUFS, not ReplayGain's -18.
                    r128 = (float(gain.split()[0]) + R128_REFERENCE - RG_REFERENCE) * 256
                    tags[f"R128_{scope.upper()}_GAIN"] = [str(round(r128))]
        if not art:
            return
        picture = Picture()
        picture.type, picture.mime, picture.desc = 3, art["mime"], "Cover"
        picture.width, picture.height, picture.depth = art["width"], art["height"], 24
        picture.data = art["data"]
        if self.kind == "flac":
            kept = [p for p in self.audio.pictures if p.type not in (0, 3)]
            self.audio.clear_pictures()
            for p in kept + [picture]:
                self.audio.add_picture(p)
        else:
            tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(picture.write()).decode("ascii")]

    def _write_mp4(self, values, art):
        tags = self.audio.tags
        for key, atom in MP4_ATOMS.items():
            if key in values:
                tags[atom] = as_list(values[key])
        for atom, number, total in (("trkn", "tracknumber", "tracktotal"), ("disk", "discnumber", "disctotal")):
            if number in values:
                tags[atom] = [(int(values[number]), int(values.get(total) or 0))]
        if "bpm" in values:
            tags["tmpo"] = [int(float(values["bpm"]))]
        if "explicit" in values:
            tags["rtng"] = [int(values["explicit"])]
        for key, name in MP4_FREEFORM.items():
            if key in values:
                for atom in [a for a in tags.keys() if a.lower() == (FREEFORM + name).lower()]:
                    del tags[atom]
                tags[FREEFORM + name] = [MP4FreeForm(v.encode()) for v in as_list(values[key])]
        if art:
            kind = MP4Cover.FORMAT_PNG if art["mime"] == "image/png" else MP4Cover.FORMAT_JPEG
            tags["covr"] = [MP4Cover(art["data"], imageformat=kind)]


def as_list(value) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


# ---------------------------------------------------------------- putting it together

RECORD_TYPES = {"single": "single", "ep": "ep", "album": "album", "compile": "compilation"}


def join_artists(mains: list[str], featured: list[str], title: str) -> str:
    names = mains[0] if len(mains) == 1 else ", ".join(mains[:-1]) + " & " + mains[-1]
    if featured and not re.search(r"\b(feat|ft|featuring|with)\b", title, re.I):
        names += " feat. " + " & ".join(featured)
    return names


def respell(name: str | None, display: str) -> str | None:
    """A name as the display artist spells it ("Eben" in "EBEN & Izzy Bizu" → "EBEN")."""
    if not name:
        return name
    match = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", display, re.I)
    return match.group(0) if match else name


def advisory(track: dict) -> str | None:
    code = track.get("explicit_content_lyrics")
    return "1" if code in (1, 4) else "2" if code == 3 else None


def gather(hit: dict, track: dict, duration: float, pool, loud_job) -> dict:
    """Every source's answer for one Deezer track, as fields to write and what they came from."""
    album = fetch(f"{DEEZER}/album/{track['album']['id']}") or {}
    credits = track.get("contributors") or []
    mains = [c["name"] for c in credits if c.get("role") == "Main"] or [track["artist"]["name"]]
    featured = [c["name"] for c in credits if c.get("role") != "Main" and c["name"] not in mains]
    title, album_title = track["title"], album.get("title") or track["album"]["title"]
    apple_job = pool.submit(apple, mains[0], title, album_title, duration)
    mb_job = pool.submit(musicbrainz, track.get("isrc"), album_title)
    lyrics_job = pool.submit(lyrics, list(dict.fromkeys([join_artists(mains, featured, title), mains[0]])),
                             title, album_title, duration)
    found_apple, mb, found_lyrics = apple_job.result(), mb_job.result(), lyrics_job.result()
    loud = loud_job.result() if loud_job else None
    song, collection = (found_apple or {}).get("song", {}), (found_apple or {}).get("collection", {})
    genres = [g["name"] for g in (album.get("genres") or {}).get("data", [])]
    display = song.get("artistName") or join_artists(mains, featured, title)
    fields = {
        "title": title,
        "artist": display,
        "artists": list(dict.fromkeys(respell(name, display) for name in mains + featured)),
        "albumartist": mb.get("albumartist") or respell((album.get("artist") or {}).get("name"), display),
        "album": album_title,
        "tracknumber": str(track.get("track_position") or "") or None,
        "tracktotal": str(album.get("nb_tracks") or "") or None,
        "discnumber": str(track.get("disk_number") or "") or None,
        "disctotal": mb.get("disctotal") or (str(song["discCount"]) if song.get("discCount") else None),
        "date": album.get("release_date") or track.get("release_date"),
        "genre": (song.get("primaryGenreName") if song.get("primaryGenreName") != "Music" else None)
                 or (genres[0] if genres else None),
        "releasetype": mb.get("releasetype") or RECORD_TYPES.get(album.get("record_type")),
        "label": album.get("label") or mb.get("label"),
        "copyright": collection.get("copyright"),
        "isrc": track.get("isrc"),
        "barcode": album.get("upc") or mb.get("barcode"),
        "media": "Digital Media",
        "bpm": str(round(track["bpm"])) if track.get("bpm") else None,
        "explicit": advisory(track),
        "lyrics": found_lyrics[0] if found_lyrics else None,
    }
    for key in ("catalognumber", "releasecountry", "releasestatus", "musicbrainz_trackid", "musicbrainz_albumid",
                "musicbrainz_releasegroupid", "musicbrainz_artistid", "musicbrainz_albumartistid"):
        fields[key] = mb.get(key)
    original = mb.get("originaldate")
    fields["originaldate"] = original if original and original < (fields["date"] or "9999") else None
    if loud:
        gain, peak = f"{RG_REFERENCE - loud[0]:+.2f} dB", f"{10 ** (loud[1] / 20):.6f}"
        fields.update(replaygain_track_gain=gain, replaygain_track_peak=peak)
        if album.get("nb_tracks") == 1:
            fields.update(replaygain_album_gain=gain, replaygain_album_peak=peak)
    art_urls = []
    if song.get("artworkUrl100"):
        art_urls.append(("Apple", re.sub(r"/\d+x\d+bb\.", "/1400x1400bb.", song["artworkUrl100"])))
    if album.get("cover_xl"):
        art_urls.append(("Deezer", album["cover_xl"]))
    return {"fields": fields, "cover": cover(art_urls), "album": album, "apple": found_apple,
            "mb": bool(mb), "lyrics": found_lyrics[1] if found_lyrics else None, "loud": loud}


# ---------------------------------------------------------------- showing it


def shown(key: str, value) -> str:
    if value in (None, "", []):
        return ""
    if key == "lyrics":
        lines = [line for line in str(value).splitlines() if line.strip()]
        synced = bool(lines) and re.match(r"\[\d+:\d+", lines[0])
        return f"{'synced' if synced else 'plain'} · {len(lines)} lines"
    if key == "explicit":
        return {"1": "explicit", "2": "clean", "0": "none"}.get(str(value), str(value))
    if key.startswith("musicbrainz_"):
        return " · ".join(v[:8] + "…" for v in as_list(value))
    return " · ".join(as_list(value))


def same(a, b) -> bool:
    return [fold(v) for v in as_list(a)] == [fold(v) for v in as_list(b)] if a and b else a == b


def cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(width - 1, 1)] + "…"


def better_art(art: dict | None, current_art: bytes | None) -> bool:
    """Whether the cover found should replace the file's: only a larger one does."""
    if not art:
        return False
    if not current_art:
        return True
    _, width, height = image_info(current_art)
    return art["data"] != current_art and art["width"] * art["height"] > width * height


def show(current: dict, fields: dict, current_art: bytes | None, art: dict | None) -> int:
    """Prints current → new for every field; returns how many would change."""
    width = shutil.get_terminal_size((100, 20)).columns
    left = max(18, min(34, (width - 24) // 2))
    changes = 0
    for key, label in FIELDS:
        now, new = shown(key, current.get(key)), shown(key, fields.get(key))
        if not new and not now:
            continue
        if not new:
            print(paint("2", f"    {label:<16} {cut(now, left):<{left}}   (kept)"))
        elif same(current.get(key), fields.get(key)):
            print(paint("2", f"  = {label:<16} {cut(now, left + 3 + width)}"))
        else:
            changes += 1
            mark, color = ("+", "32") if not now else ("~", "33")
            print(f"  {paint(color, mark)} {label:<16} {paint('2', cut(now or '—', left)):<{left}} "
                  f"→ {paint(color, cut(new, max(width - left - 26, 20)))}")
    have = image_info(current_art) if current_art else (None, 0, 0)
    if better_art(art, current_art):
        changes += 1
        now = f"{have[1]}×{have[2]}" if current_art else "—"
        new = "{}×{} from {}, {} KB".format(art["width"], art["height"], art["source"], len(art["data"]) // 1024)
        mark, color = ("+", "32") if not current_art else ("~", "33")
        print(f"  {paint(color, mark)} {'Cover':<16} {paint('2', now):<{left}} → {paint(color, new)}")
    elif current_art:
        print(paint("2", f"    {'Cover':<16} {have[1]}×{have[2]}   (kept)"))
    return changes


def summary_line(found: dict) -> str:
    def status(ok, text):
        return paint("32", f"✓ {text}") if ok else paint("2", f"✗ {text}")
    apple_text = f"Apple ({found['apple']['country']})" if found["apple"] else "Apple"
    lyrics_text = f"LRCLIB {found['lyrics']}" if found["lyrics"] else "LRCLIB"
    loud = found["loud"]
    gain_text = f"ReplayGain ({loud[0]:.1f} LUFS)" if loud else "ReplayGain"
    return "  ".join([status(True, "Deezer"), status(found["apple"], apple_text), status(found["mb"], "MusicBrainz"),
                      status(found["lyrics"], lyrics_text), status(loud, gain_text)])


# ---------------------------------------------------------------- one file


def edit(fields: dict) -> dict:
    print(paint("2", "  fields: " + ", ".join(k for k, _ in FIELDS)))
    print(paint("2", "  lists take ; between values; an empty value leaves the file's own"))
    line = ask("  field = value: ")
    if "=" not in line:
        return fields
    key, value = (part.strip() for part in line.split("=", 1))
    key = next((k for k, label in FIELDS if key.lower() in (k, label.lower())), None)
    if not key:
        warn("no such field")
        return fields
    new = [v.strip() for v in value.split(";") if v.strip()] if key in LIST_FIELDS else value
    return {**fields, key: new or None}


def choose(hits: list[dict]) -> dict | None:
    for index, hit in enumerate(hits[:12], 1):
        print(f"  {index:>2}. {hit['artist']['name']} — {hit['title']} · {hit['album']['title']} · "
              f"{mmss(hit.get('duration'))} · {round(hit['score'] * 100)}%")
    picked = ask("  number: ")
    return hits[int(picked) - 1] if picked.isdigit() and 0 < int(picked) <= min(len(hits), 12) else None


def process(path: str, args) -> bool:
    """Looks one file up, shows what was found and writes it on a yes. True once the file's
    tags are what was found — written, or already so."""
    try:
        tags = Tags(path)
    except (ValueError, mutagen.MutagenError, OSError) as error:
        warn(f"{os.path.basename(path)}: {error}")
        return False
    current, current_art = tags.read()
    duration = tags.duration
    name_artist, name_title = guess_from_name(path)
    artist = args.query and split_query(args.query)[0] or one(current.get("artist")) or name_artist
    title = args.query and split_query(args.query)[1] or one(current.get("title")) or name_title
    artist, title = as_list(artist)[0] if artist else "", as_list(title)[0] if title else ""
    print(paint("1", f"\n━━ {os.path.basename(path)}") + paint("2", f"   {tags.format} · {mmss(duration)}"))
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        loud_job = None if args.no_gain else pool.submit(loudness, path)
        hits, forced = deezer_hits(artist, title, duration), None
        while True:
            print(paint("2", f"  looking for  {artist or '?'} — {title}"))
            picked = (forced, fetch(f"{DEEZER}/track/{forced['id']}")) if forced else deezer_track(hits)
            if not picked or not picked[1] or "error" in picked[1]:
                warn("nothing on Deezer for that")
                answer = ask("  [s]earch again, or Enter to skip: ", "n").lower()
                if answer.startswith("s"):
                    artist, title = split_query(ask("  Artist - Title: "))
                    hits, forced = deezer_hits(artist, title, duration), None
                    continue
                return False
            hit, track = picked
            found = gather(hit, track, duration, pool, loud_job)
            fields, art = found["fields"], found["cover"]
            if tags.opus:  # R128 gains carry no peak
                fields = {k: v for k, v in fields.items() if not k.endswith("_peak")}
            confidence = hit["score"]
            color = "32" if confidence >= CONFIDENT else "31"
            print(f"  match  {paint('1', fields['artist'] + ' — ' + fields['title'])}  "
                  + paint("2", f"{fields['album']} ({fields.get('releasetype') or '?'}, {fields.get('date') or '?'})"
                          f" · {mmss(track.get('duration'))} vs {mmss(duration)} · ")
                  + paint(color, f"{round(confidence * 100)}%"))
            print("  " + summary_line(found))
            while True:
                changes = show(current, fields, current_art, art)
                if not changes:
                    print(paint("32", "  tags already match"))
                    return True
                if args.dry_run:
                    return False
                if args.yes:
                    if confidence < CONFIDENT:
                        warn("not sure enough of this match to write it unasked — skipped")
                        return False
                    answer = "y"
                else:
                    default = "y" if confidence >= CONFIDENT else "n"
                    answer = ask(f"  write {changes} change{'s' * (changes > 1)}? "
                                 f"[{'Y/n' if default == 'y' else 'y/N'}] [c]hoose another match "
                                 f"[s]earch again [e]dit [q]uit: ", default).lower()
                if answer.startswith("e"):
                    fields = edit(fields)
                    continue
                break
            if answer.startswith("y"):
                tags.write(fields, art if better_art(art, current_art) else None)
                print(paint("32", "  ✓ written"))
                return True
            if answer.startswith("c"):
                forced = choose(hits) or forced
                continue
            if answer.startswith("s"):
                artist, title = split_query(ask("  Artist - Title: "))
                hits, forced = deezer_hits(artist, title, duration), None
                continue
            if answer.startswith("q"):
                raise KeyboardInterrupt
            return False


# ---------------------------------------------------------------- sending


def load_send(path: str | None = None, env=None) -> dict | None:
    """The [send] settings from the config file, with TAGIT_SEND_TARGET over its target;
    None when no target is set, so sending is never offered."""
    env = os.environ if env is None else env
    path = path or env.get("TAGIT_CONFIG") or CONFIG_PATH
    settings = {}
    if os.path.exists(path):
        if tomllib is None:
            warn(f"{path}: reading it needs Python 3.11+ or tomli (python3 -m pip install tomli)")
        else:
            try:
                with open(path, "rb") as handle:
                    settings = tomllib.load(handle).get("send") or {}
            except (OSError, tomllib.TOMLDecodeError) as error:
                warn(f"{path}: {error}")
    target = env.get("TAGIT_SEND_TARGET") or settings.get("target") or ""
    if not target:
        return None
    host, _, folder = str(target).partition(":")
    backdate = settings.get("backdate_minutes", 0)
    if not host or not folder or any(ch.isspace() for ch in host):
        warn(f'send target "{target}" isn\'t user@host:/folder; sending is off')
        return None
    if not isinstance(backdate, int) or isinstance(backdate, bool) or backdate < 0:
        warn("send.backdate_minutes must be a whole number of minutes, 0 or more; sending is off")
        return None
    return {"host": host, "folder": folder.rstrip("/") or "/", "name": str(settings.get("name") or host),
            "backdate": backdate, "note": str(settings.get("note") or "")}


def send(path: str, target: dict) -> bool:
    """Copies the file into the target folder under a hidden name, then renames it once it's
    whole (as "Name (2).ext" if the name is taken) and, if asked, dates it in the past."""
    name = os.path.basename(path)
    host, folder = target["host"], target["folder"]
    staging = f"{folder}/.tagit-{os.getpid()}-{int(time.time())}.part"
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    upload = subprocess.run(["scp", "-q", *ssh_opts, path, f"{host}:{staging}"], stdin=subprocess.DEVNULL)
    if upload.returncode:
        warn(f"couldn't copy to {target['name']}")
        return False
    backdate = f' && touch -d "{target["backdate"]} minutes ago" "$f"' if target["backdate"] else ""
    script = (f'f={shlex.quote(folder + "/" + name)}; b="${{f%.*}}"; e="${{f##*.}}"; n=2; '
              f'while [ -e "$f" ]; do f="$b ($n).$e"; n=$((n+1)); done; '
              f'mv -- {shlex.quote(staging)} "$f"{backdate} && basename "$f"')
    moved = subprocess.run(["ssh", *ssh_opts, host, script], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
    if moved.returncode:
        warn(f"{target['name']}: {moved.stderr.strip() or 'the rename failed'}")
        return False
    note = paint("2", f" · {target['note']}") if target["note"] else ""
    print(paint("32", f"  ✓ sent to {target['name']} as {moved.stdout.strip()}") + note)
    return True


# ---------------------------------------------------------------- main


def audio_files(paths: list[str]) -> list[str]:
    found = []
    for path in paths:
        if os.path.isdir(path):
            for root, _, names in os.walk(path):
                found += [os.path.join(root, n) for n in sorted(names)
                          if os.path.splitext(n)[1].lower() in AUDIO_EXTS and not n.startswith(".")]
        elif os.path.isfile(path):
            found.append(path)
        else:
            warn(f"{path}: no such file")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(prog="tagit", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="audio files or folders")
    parser.add_argument("--version", action="version", version=f"tagit {__version__}")
    parser.add_argument("-q", "--query", help='search for "Artist - Title" instead of what the file says')
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what was found, write nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="write confident matches without asking")
    parser.add_argument("--no-gain", action="store_true", help="skip measuring ReplayGain")
    sending = parser.add_mutually_exclusive_group()
    sending.add_argument("--send", action="store_true", help="send to the configured target after writing, without asking")
    sending.add_argument("--no-send", action="store_true", help="never offer to send")
    args = parser.parse_args()

    files = audio_files(args.paths)
    if not files:
        return 1
    target = None if args.dry_run or args.no_send else load_send()
    if args.send and not target and not args.dry_run:
        warn(f"--send needs a send target: set send.target in {CONFIG_PATH} or TAGIT_SEND_TARGET")
        return 2
    try:
        for path in files:
            if not process(path, args) or not target:
                continue
            if args.send or (not args.yes and ask(f"  send to {target['name']}? [Y/n] ", "y").lower().startswith("y")):
                send(path, target)
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
