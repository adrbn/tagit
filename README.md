<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/hero-light.svg">
  <img src="docs/assets/hero.svg" width="100%" alt="tagit in a terminal. A first run on an MP3 tagged only with a title, an artist and a genre matches the song on Deezer at 100 %, lists 21 changes (title cleaned, album, track and disc numbers, date, genre, label, copyright, ISRC, barcode, ReplayGain, a 1400 by 1400 cover from Apple) and writes them on y. A second run with -n shows every field unchanged and ends with tags already match.">
</picture>

<h1 align="center">tagit</h1>

<p align="center">
  <b>Real tags for the songs you already have.</b><br>
  One command looks each file up on Deezer, Apple, MusicBrainz and LRCLIB, shows what would change, and writes it when you say yes.
</p>

<p align="center">
  <a href="#install"><img src="docs/assets/btn-install.svg" height="56" alt="Install: pipx or Homebrew"></a>
  &nbsp;
  <a href="#usage"><img src="docs/assets/btn-usage.svg" height="56" alt="Usage: flags and examples"></a>
  &nbsp;
  <a href="#configuration"><img src="docs/assets/btn-config.svg" height="56" alt="Configure: optional send step"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-1F883D?style=flat-square&labelColor=24292f&logo=python&logoColor=white" alt="Python 3.9 or later">
  <img src="https://img.shields.io/badge/formats-MP3%20%C2%B7%20FLAC%20%C2%B7%20M4A%20%C2%B7%20Ogg%20%C2%B7%20Opus%20%C2%B7%20WAV%20%C2%B7%20AIFF-1F883D?style=flat-square&labelColor=24292f" alt="Formats: MP3, FLAC, M4A, Ogg, Opus, WAV, AIFF">
  <img src="https://img.shields.io/badge/API%20keys-none-1F883D?style=flat-square&labelColor=24292f" alt="No API keys needed">
  <a href="https://github.com/adrbn/tagit/releases/latest"><img src="https://img.shields.io/github/v/release/adrbn/tagit?style=flat-square&labelColor=24292f&color=1F883D" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1F883D?style=flat-square&labelColor=24292f" alt="MIT licence"></a>
</p>

<p align="center">
  Free and open source · macOS and Linux · ffmpeg optional, for ReplayGain
  <br><br>
  <b>English</b> · <a href="README.fr.md">Français</a>
</p>

---

tagit is a command-line tagger for music files you bought, ripped or downloaded with thin or wrong tags. It finds the release the song came from, fills in the fields a library app actually uses, and never writes anything you haven't seen first. The sources are public APIs that need no account and no key.

## What it does

- **Finds the release.** Searches Deezer with the file's own tags, or its filename (`01 - Artist - Title.mp3`), or a query you give it. Each hit is scored on title, artist and duration; among near ties the earliest release wins, so a single beats the compilation that reissued it.
- **Fills the fields.** Title, artists, album artist, album, track and disc numbers, date and original date, genre, release type, label, catalog number, copyright, ISRC, barcode, country, BPM, explicit flag and MusicBrainz ids (recording, release, release group, artists).
- **Brings the cover.** Apple's 1400 × 1400 artwork, else Deezer's. A cover already in the file is replaced only by a larger one.
- **Adds lyrics.** From LRCLIB, synced (LRC) when it has them, plain otherwise. Instrumentals are skipped.
- **Measures loudness.** ReplayGain 2.0 track gain and peak from ffmpeg's EBU R128 meter, plus album gain for singles. Opus files get R128 gains instead.
- **Asks first.** Every field is shown as current → new. Answer `y` to write, `c` to pick another match, `s` to search again, `e` to edit a field, `q` to quit.
- **Optionally sends the file on.** With a target configured, a tagged file can be copied to a server with scp, for example into a folder a library importer watches.

Formats: MP3, WAV and AIFF (ID3v2.4), FLAC, Ogg Vorbis and Opus (Vorbis comments), M4A/ALAC (MP4 atoms). Field names follow MusicBrainz Picard's, so Navidrome, Plex, Jellyfin, beets and most players read them.

## Install

**pipx** (any system with Python 3.9+):

```sh
pipx install git+https://github.com/adrbn/tagit
```

**Homebrew** (macOS, Linux):

```sh
brew install adrbn/tap/tagit
```

**From source:**

```sh
git clone https://github.com/adrbn/tagit
cd tagit
python3 -m pip install .
```

For ReplayGain, install ffmpeg too (`brew install ffmpeg`, `apt install ffmpeg`). Without it tagit still tags everything else; pass `--no-gain` to skip the measurement.

## Requirements

- Python 3.9 or later. Dependencies install with it: [mutagen](https://github.com/quodlibet/mutagen), certifi, and tomli on Python older than 3.11.
- ffmpeg, optional, for ReplayGain.
- scp and ssh, only if you use the send step.

## Usage

```sh
tagit song.mp3                      # look it up, show the changes, ask before writing
tagit ~/Music/New                   # every audio file in a folder, recursively
tagit -n song.flac                  # dry run: show what was found, write nothing
tagit -y ~/Music/New                # write matches of 70 % or more without asking
tagit -q "Artist - Title" file.m4a  # search for this instead of what the file says
tagit --no-gain song.mp3            # skip the ReplayGain measurement
```

| Flag | What it does |
|---|---|
| `-n`, `--dry-run` | Show what was found, write nothing |
| `-y`, `--yes` | Write confident matches (70 % or more) without asking; skip the rest |
| `-q`, `--query "Artist - Title"` | Search for this instead of the file's tags or name |
| `--no-gain` | Don't measure ReplayGain |
| `--send` | After writing, send to the configured target without asking |
| `--no-send` | Never offer to send |
| `--version` | Print the version |

In the change list, `+` is a field the file doesn't have yet, `~` one that changes, `=` one that already matches, and `(kept)` one the sources didn't fill, left as it is.

## Configuration

Nothing needs configuring to tag files. The only setting is the optional send step, read from `~/.config/tagit/config.toml` (or `$XDG_CONFIG_HOME/tagit/config.toml`, or the path in `TAGIT_CONFIG`):

```toml
[send]
# where to copy tagged files: user@host:/absolute/folder (scp syntax)
target = "me@nas.local:/srv/music/incoming"
# what prompts call it (default: the host)
name = "the NAS"
# after the copy, set the file's modification time this many minutes in the past (GNU touch on the server); 0 = leave it
backdate_minutes = 0
# a line printed after each successful send
note = "the importer picks it up within 10 minutes"
```

`TAGIT_SEND_TARGET` overrides `target`. With no target, tagit never offers to send. When one is set, tagit asks `send to the NAS? [Y/n]` after each file it writes or finds already tagged; `--send` skips the question and `--no-send` skips the step.

A send copies the file under a hidden `.tagit-….part` name, then renames it on the server once it is whole, as `Song (2).mp3` if `Song.mp3` is taken, so a watcher never picks up half a file. ssh runs with `BatchMode=yes`, so it needs key-based login. Backdating helps importers that ignore files modified in the last few minutes, such as a cron job that waits for downloads to settle.

## Build from source

```sh
git clone https://github.com/adrbn/tagit && cd tagit
python3 tests/test_tagit.py    # plain asserts, or: python3 -m pytest
python3 tagit.py -n some-song.mp3
```

tagit is one file, `tagit.py`. The README art is generated by `python3 docs/make_svgs.py` from two real terminal transcripts kept in `docs/assets/readme-src/`.

## Privacy

tagit sends the song's title, artist, album, duration and ISRC to the services listed under Credits to look it up, and nothing else. No account, no telemetry. The audio itself never leaves your machine unless you configure a send target.

## Support

tagit is free. If it saved you an evening of fixing tags, you can buy me a coffee:

<a href="https://ko-fi.com/adrbn"><img src="docs/assets/btn-kofi.svg" height="56" alt="Buy me a coffee on Ko-fi"></a>

A star on the repo helps others find it.

## License

MIT: use, change and share it freely, keeping the copyright notice. See [LICENSE](LICENSE).

## Credits

- [Deezer API](https://developers.deezer.com/api): search, release, ISRC, UPC, label, BPM, cover
- [Apple iTunes Search API](https://performance-partners.apple.com/search-api): genre, copyright, artist spelling, 1400 px cover
- [MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API): recording, release and artist ids by ISRC, release details
- [LRCLIB](https://lrclib.net): synced and plain lyrics
- [mutagen](https://github.com/quodlibet/mutagen) reads and writes the tags; [FFmpeg](https://ffmpeg.org) measures loudness

<sub>tagit is not affiliated with Deezer, Apple, the MetaBrainz Foundation or LRCLIB. Please respect each service's terms of use.</sub>
