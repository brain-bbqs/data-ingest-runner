#!/usr/bin/env python3
"""Record disk usage of the drive holding WORK_DIR in the tracking dataset.

Each call appends a row to disk-usage/disk-usage.csv, redraws
disk-usage/disk-usage.svg (the current usage plus its history), makes sure
the README's "Runner disk usage" section embeds that image, and commits the
result. Inside a GitHub Actions
step it also sets the outputs `percent` (used, as a bare number) and
`summary` (one line for notifications).

    update_disk_usage.py TRACKING_DIR WORK_DIR [--threshold PERCENT]

Standard library only, since it runs on the runner host's own python3.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

DIRECTORY = "disk-usage"
CSV_NAME = f"{DIRECTORY}/disk-usage.csv"
SVG_NAME = f"{DIRECTORY}/disk-usage.svg"
# Where earlier versions of this script kept them, at the dataset root.
LEGACY_NAMES = ("disk-usage.csv", "disk-usage.svg")
FIELDS = ("timestamp_utc", "filesystem", "mount", "size_bytes", "used_bytes", "avail_bytes", "use_percent")
START, END = "<!-- runner-disk-usage:start -->", "<!-- runner-disk-usage:end -->"
SECTION = f"""{START}
## Runner disk usage

![Runner disk usage]({SVG_NAME})
{END}"""

WIDTH, HEIGHT = 720, 290
PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, PLOT_BOTTOM = 56, 690, 136, 232


def human(n: int, /) -> str:
    """Bytes in powers of 1000, rounded up the way `df -H` prints them."""
    value = float(n)
    for unit in ("B", "K", "M", "G", "T", "P", "E"):
        if value < 1000 or unit == "E":
            break
        value /= 1000
    if unit == "B":
        return f"{int(value)}B"
    text = f"{math.ceil(value * 10) / 10:.1f}{unit}" if value < 10 else f"{math.ceil(value)}{unit}"
    return text


def measure(work_dir: Path, /) -> dict[str, str]:
    output = subprocess.run(
        ["df", "-B1", "--output=source,size,used,avail,pcent,target", str(work_dir)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    source, size, used, avail, percent, mount = output.strip().splitlines()[-1].split(None, 5)
    row = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filesystem": source,
        "mount": mount,
        "size_bytes": size,
        "used_bytes": used,
        "avail_bytes": avail,
        "use_percent": percent.rstrip("%"),
    }
    return row


def append_row(csv_path: Path, /, *, row: dict[str, str]) -> list[dict[str, str]]:
    is_new = not csv_path.exists()
    with csv_path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows


def render_svg(*, rows: list[dict[str, str]], threshold: int) -> str:
    latest = rows[-1]
    percent = int(latest["use_percent"])
    used, size, avail = (int(latest[key]) for key in ("used_bytes", "size_bytes", "avail_bytes"))
    times = [datetime.strptime(r["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ") for r in rows]
    values = [int(r["use_percent"]) for r in rows]

    span = (times[-1] - times[0]).total_seconds() or 1.0

    def x(t: datetime) -> float:
        offset = (t - times[0]).total_seconds() / span if len(times) > 1 else 1.0
        position = PLOT_LEFT + offset * (PLOT_RIGHT - PLOT_LEFT)
        return position

    def y(v: float) -> float:
        position = PLOT_BOTTOM - v / 100 * (PLOT_BOTTOM - PLOT_TOP)
        return position

    points = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(times, values))
    over = percent >= threshold
    status = (
        f'<text class="critical" x="{WIDTH - 24}" y="44" text-anchor="end">⚠ At or above the {threshold}% alert threshold</text>'
        if over
        else ""
    )
    last_x, last_y = x(times[-1]), y(values[-1])
    gridlines = "".join(
        f'<line class="grid" x1="{PLOT_LEFT}" x2="{PLOT_RIGHT}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
        f'<text class="tick" x="{PLOT_LEFT - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v}%</text>'
        for v in (0, 100)
    )
    first_label, last_label = times[0].strftime("%Y-%m-%d"), times[-1].strftime("%Y-%m-%d")
    x_labels = f'<text class="tick" x="{PLOT_RIGHT}" y="{PLOT_BOTTOM + 18}" text-anchor="end">{last_label}</text>'
    if len(times) > 1 and first_label != last_label:
        x_labels += f'<text class="tick" x="{PLOT_LEFT}" y="{PLOT_BOTTOM + 18}">{first_label}</text>'
    mount = escape(latest["mount"])
    updated = times[-1].strftime("%Y-%m-%d %H:%M UTC")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">
<title id="title">Runner disk usage</title>
<desc id="desc">{percent}% used: {human(used)} of {human(size)}, {human(avail)} free on {mount}. Updated {updated}.</desc>
<style>
  svg {{ --surface: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e; --grid: #e2e1dc; --series: #2a78d6; --critical: #d03b3b; }}
  @media (prefers-color-scheme: dark) {{
    svg {{ --surface: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7; --grid: #383835; --series: #3987e5; --critical: #e66767; }}
  }}
  text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: var(--text-secondary); font-size: 12px; }}
  .surface {{ fill: var(--surface); }}
  .label {{ font-size: 13px; }}
  .hero {{ fill: var(--text-primary); font-size: 40px; font-weight: 600; }}
  .detail {{ fill: var(--text-primary); font-size: 15px; }}
  .tick {{ font-size: 11px; font-variant-numeric: tabular-nums; }}
  .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .threshold {{ stroke: var(--text-secondary); stroke-width: 1; stroke-dasharray: 4 4; }}
  .series {{ fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
  .dot {{ fill: var(--series); stroke: var(--surface); stroke-width: 2; }}
  .critical {{ fill: var(--critical); font-size: 13px; font-weight: 600; }}
</style>
<rect class="surface" width="{WIDTH}" height="{HEIGHT}" rx="8"/>
<text class="label" x="24" y="28">Runner work drive · {mount}</text>
{status}
<text class="hero" x="24" y="74">{percent}% used</text>
<text class="detail" x="24" y="98">{human(used)} of {human(size)} used · {human(avail)} free</text>
<text class="label" x="24" y="124">Use over time</text>
{gridlines}
<line class="threshold" x1="{PLOT_LEFT}" x2="{PLOT_RIGHT}" y1="{y(threshold):.1f}" y2="{y(threshold):.1f}"/>
<text class="tick" x="{PLOT_LEFT - 8}" y="{y(threshold) + 4:.1f}" text-anchor="end">{threshold}%</text>
<text class="tick" x="{PLOT_LEFT + 6}" y="{y(threshold) - 6:.1f}">alert threshold</text>
{f'<polyline class="series" points="{points}"/>' if len(values) > 1 else ""}
<circle class="dot" cx="{last_x:.1f}" cy="{last_y:.1f}" r="4"/>
{x_labels}
<text x="24" y="{HEIGHT - 12}">Updated {updated}</text>
</svg>
"""
    return svg


def ensure_section(readme: Path, /) -> None:
    text = readme.read_text()
    if START in text:
        before, rest = text.split(START, 1)
        # A section whose end marker was edited away runs to the end of the file.
        after = rest.split(END, 1)[1] if END in rest else "\n"
        text = f"{before}{SECTION}{after}"
    else:
        text = f"{text.rstrip()}\n\n{SECTION}\n"
    readme.write_text(text)


def move_legacy_files(tracking: Path, /) -> list[str]:
    """Move files written at the dataset root by earlier versions, keeping
    the CSV's history. Returns the old paths, so the commit records them."""
    (tracking / DIRECTORY).mkdir(exist_ok=True)
    moved = []
    for old, new in zip(LEGACY_NAMES, (CSV_NAME, SVG_NAME)):
        if (tracking / old).exists() and not (tracking / new).exists():
            subprocess.run(["git", "-C", str(tracking), "mv", old, new], check=True)
            moved.append(old)
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tracking", type=Path, help="The tracking dataset's local clone.")
    parser.add_argument("work", type=Path, help="Any path on the drive to measure.")
    parser.add_argument("--threshold", type=int, default=50, help="Alert threshold drawn on the chart, in percent.")
    args = parser.parse_args()

    moved = move_legacy_files(args.tracking)
    row = measure(args.work)
    rows = append_row(args.tracking / CSV_NAME, row=row)
    (args.tracking / SVG_NAME).write_text(render_svg(rows=rows, threshold=args.threshold))
    ensure_section(args.tracking / "README.md")

    paths = ["README.md", CSV_NAME, SVG_NAME]
    subprocess.run(["git", "-C", str(args.tracking), "add", "--", *paths], check=True)
    message = f"Updated runner disk usage ({row['use_percent']}% used)"
    subprocess.run(["git", "-C", str(args.tracking), "commit", "-q", "-m", message, "--", *paths, *moved], check=True)

    percent = row["use_percent"]
    summary = f"{human(int(row['used_bytes']))} of {human(int(row['size_bytes']))} used ({percent}%), {human(int(row['avail_bytes']))} free on {row['mount']}"
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write(f"percent={percent}\nsummary={summary}\n")
    print(f"Runner work drive: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
