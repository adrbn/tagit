# Changelog

## 1.0.0 (2026-10-02)

First public release.

- Matches each file on Deezer by title, artist and duration; among near ties the earliest release wins.
- Adds Apple's genre, copyright and 1400 px cover, MusicBrainz ids by ISRC, LRCLIB lyrics (synced when available) and ReplayGain 2.0 measured by ffmpeg.
- Shows every field as current → new and writes only on a yes; `-n` shows without writing, `-y` writes matches of 70 % or more without asking.
- Reads and writes MP3, FLAC, M4A/ALAC, Ogg Vorbis, Opus (R128 gains), WAV and AIFF.
- Optional send step: with `send.target` set in `~/.config/tagit/config.toml` or `TAGIT_SEND_TARGET`, a tagged file is copied with scp under a hidden name, renamed once whole, and optionally dated in the past.
