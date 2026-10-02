#!/usr/bin/env python3
"""Writes the README art in docs/assets/ from real tagit runs. Stdlib only.

The two terminal transcripts in docs/assets/readme-src/ are what tagit printed, colours
included, on a copy of one MP3 whose tags had been cut down to a title, an artist and a
genre: first an interactive run answered "y", then `tagit -n` on the written file.

    python3 docs/make_svgs.py
"""
import os
import re
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets", "readme-src")
OUT = os.path.join(HERE, "assets")

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
ACCENT = "#1F883D"

THEMES = {
    "dark": {"window": "#0d1117", "bar": "#161b22", "edge": "#30363d", "text": "#e6edf3", "dim": "#7d8590",
             "32": "#3fb950", "33": "#d29922", "31": "#f85149", "prompt": "#3fb950"},
    "light": {"window": "#ffffff", "bar": "#f6f8fa", "edge": "#d0d7de", "text": "#1f2328", "dim": "#6e7781",
              "32": "#1a7f37", "33": "#9a6700", "31": "#cf222e", "prompt": "#1a7f37"},
}

# Terminal metrics: a monospace cell is 0.6 em wide.
FONT = 13
CHAR = FONT * 0.6
LINE = 18
PAD = 20
BAR = 36
COLS = 88
FILE = "Otto Knows – My Lover.mp3"
RUNS = [("write.ansi", f'tagit "{FILE}"'), ("check.ansi", f'tagit -n "{FILE}"')]
LOOP = 16  # seconds: each run holds ~7 s, then a 1 s crossfade


def runs_of(line: str, styles: set) -> tuple[list[tuple[int, str, set]], set]:
    """(column, text, styles) for each stretch of one SGR state in an ANSI line, and the
    state left at its end (a colour can run on to the next line)."""
    out, col = [], 0
    for part in re.split(r"(\x1b\[[\d;]*m)", line):
        code = re.fullmatch(r"\x1b\[([\d;]*)m", part)
        if code:
            for value in (code.group(1) or "0").split(";"):
                styles = set() if value == "0" else styles | {value}
        elif part:
            out.append((col, part, set(styles)))
            col += len(part)
    return out, styles


def transcript(name: str) -> list[str]:
    with open(os.path.join(SRC, name), encoding="utf-8") as handle:
        lines = handle.read().replace("\r", "").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def text_line(y: float, line: str, theme: dict, state: set) -> tuple[str, set]:
    spans = []
    runs, state = runs_of(line, state)
    for col, text, styles in runs:
        color = next((theme[c] for c in ("31", "32", "33") if c in styles), theme["dim"] if "2" in styles else theme["text"])
        weight = ' font-weight="700"' if "1" in styles else ""
        spans.append(f'<tspan x="{PAD + col * CHAR:.1f}" fill="{color}"{weight}>{escape(text)}</tspan>')
    return f'<text y="{y:.1f}" xml:space="preserve">{"".join(spans)}</text>', state


def screen(name: str, command: str, theme: dict, top: float) -> str:
    rows = [f'<text y="{top:.1f}" xml:space="preserve"><tspan x="{PAD}" fill="{theme["prompt"]}" font-weight="700">$</tspan>'
            f'<tspan x="{PAD + 2 * CHAR:.1f}" fill="{theme["text"]}">{escape(command)}</tspan></text>']
    state = set()
    for index, line in enumerate(transcript(name), 1):
        row, state = text_line(top + index * LINE, line, theme, state)
        rows.append(row)
    return "\n".join(rows)


def hero(theme_name: str) -> str:
    theme = THEMES[theme_name]
    rows = max(len(transcript(n)) for n, _ in RUNS) + 1
    width = PAD * 2 + COLS * CHAR
    height = BAR + PAD + rows * LINE + PAD - LINE + FONT
    top = BAR + PAD + FONT
    first, second = (screen(n, c, theme, top) for n, c in RUNS)
    hold = (LOOP / 2 - 1) / LOOP * 100
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}" role="img" aria-label="tagit in a terminal: it matches an MP3 on Deezer, Apple and LRCLIB, lists 21 tag changes and writes them; a second run with -n then finds every tag already matches.">
<style>
  .b {{ animation: swap {LOOP}s ease-in-out infinite; }}
  @keyframes swap {{ 0%, {hold:.2f}% {{ opacity: 0 }} 50%, {50 + hold:.2f}% {{ opacity: 1 }} 100% {{ opacity: 0 }} }}
  @media (prefers-reduced-motion: reduce) {{ .b {{ animation: none; }} }}
</style>
<defs><clipPath id="w"><rect width="{width:.0f}" height="{height:.0f}" rx="12"/></clipPath></defs>
<g clip-path="url(#w)" font-family="{MONO}" font-size="{FONT}">
  <rect width="{width:.0f}" height="{height:.0f}" fill="{theme["window"]}"/>
  <rect width="{width:.0f}" height="{BAR}" fill="{theme["bar"]}"/>
  <line x1="0" y1="{BAR - .5}" x2="{width:.0f}" y2="{BAR - .5}" stroke="{theme["edge"]}"/>
  <circle cx="20" cy="{BAR / 2}" r="6" fill="#ff5f57"/><circle cx="40" cy="{BAR / 2}" r="6" fill="#febc2e"/><circle cx="60" cy="{BAR / 2}" r="6" fill="#28c840"/>
  <text x="{width / 2:.0f}" y="{BAR / 2 + 4.5}" text-anchor="middle" font-family="{SANS}" font-size="13" font-weight="600" fill="{theme["dim"]}">tagit</text>
  <g>{first}</g>
  <g class="b" opacity="0"><rect y="{BAR}" width="{width:.0f}" height="{height - BAR:.0f}" fill="{theme["window"]}"/>{second}</g>
</g>
<rect x=".5" y=".5" width="{width - 1:.0f}" height="{height - 1:.0f}" rx="11.5" fill="none" stroke="{theme["edge"]}"/>
</svg>
'''


# Pill buttons: (file, title, subtitle, icon path drawn in a 24-unit box centred on 0,0)
TERMINAL_ICON = '<path d="M-10 -8H10V8H-10Z" fill="#fff" fill-opacity=".18"/><path d="M-6 -3L-2 0L-6 3M0 4H5"/>'
BOOK_ICON = '<path d="M-10 -8H-2A2 2 0 0 1 0 -6V9A2 2 0 0 0 -2 7H-10Z M10 -8H2A2 2 0 0 0 0 -6V9A2 2 0 0 1 2 7H10Z"/>'
GEAR_ICON = ('<circle r="3.5"/><path d="M0 -10V-7M0 7V10M-10 0H-7M7 0H10M-7 -7L-5 -5M5 5L7 7M-7 7L-5 5M5 -5L7 -7"/>'
             '<circle r="7" fill="#fff" fill-opacity=".12"/>')
CUP_ICON = ('<path d="M-11 -5H7V4A7 7 0 0 1 0 11H-4A7 7 0 0 1 -11 4Z" fill="#fff" fill-opacity=".18"/>'
            '<path d="M7 -2H9.5A3.5 3.5 0 0 1 9.5 5H7"/><path d="M-6 -10.5Q-4 -12.5 -6 -14.5M-1 -10.5Q1 -12.5 -1 -14.5" stroke-width="2"/>')
BUTTONS = [
    ("btn-install", ACCENT, TERMINAL_ICON, "Install", "pipx or Homebrew"),
    ("btn-install-fr", ACCENT, TERMINAL_ICON, "Installer", "pipx ou Homebrew"),
    ("btn-usage", "#24292f", BOOK_ICON, "Usage", "Flags and examples"),
    ("btn-usage-fr", "#24292f", BOOK_ICON, "Utilisation", "Options et exemples"),
    ("btn-config", "#24292f", GEAR_ICON, "Configure", "Optional send step"),
    ("btn-config-fr", "#24292f", GEAR_ICON, "Configurer", "Envoi facultatif"),
    ("btn-kofi", "#FF5E5B", CUP_ICON, "Buy me a coffee", "Tip the dev on Ko-fi"),
    ("btn-kofi-fr", "#FF5E5B", CUP_ICON, "Offrez-moi un café", "Un pourboire sur Ko-fi"),
]


def button(fill: str, icon: str, title: str, subtitle: str) -> str:
    title_w, sub_w = len(title) * 9.4, len(subtitle) * 6.6
    width = round(60 + max(title_w, sub_w) + 26)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="64" viewBox="0 0 {width} 64" role="img" aria-label="{escape(title)}: {escape(subtitle)}">
  <defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".2"/><stop offset=".5" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>
  <rect width="{width}" height="64" rx="16" fill="{fill}"/>
  <rect width="{width}" height="64" rx="16" fill="url(#s)"/>
  <rect x=".5" y=".5" width="{width - 1}" height="63" rx="15.5" fill="none" stroke="#fff" stroke-opacity=".25"/>
  <g transform="translate(34 33)" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">{icon}</g>
  <g font-family="{SANS}" fill="#fff">
    <text x="60" y="30" font-size="17" font-weight="700">{escape(title)}</text>
    <text x="60" y="48" font-size="12.5" font-weight="500" fill-opacity=".9">{escape(subtitle)}</text>
  </g>
</svg>
'''


def main() -> None:
    files = {"hero.svg": hero("dark"), "hero-light.svg": hero("light")}
    files.update({f"{name}.svg": button(fill, icon, title, sub) for name, fill, icon, title, sub in BUTTONS})
    for name, svg in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as handle:
            handle.write(svg)
        print(f"{name:<22} {len(svg.encode()) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
