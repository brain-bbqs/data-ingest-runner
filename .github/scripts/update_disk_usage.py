#!/usr/bin/env python3
"""Record disk usage of the drive holding WORK_DIR on the tracking dataset's
disk-usage branch.

Each call appends a row to disk-usage.csv and redraws disk-usage.svg (the
current usage plus its history), then replaces the local disk-usage branch
with a single parentless commit holding just those two files. The workflow
force-pushes that branch, so it never accumulates history. The CSV is the
history. The README on the dataset's main branch embeds the chart by URL.

Inside a GitHub Actions step it also sets the outputs `percent` (used, as a
bare number) and `summary` (one line for notifications).

    update_disk_usage.py TRACKING_DIR WORK_DIR [--threshold PERCENT]

Standard library only, since it runs on the runner host's own python3.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

BRANCH = "disk-usage"
CSV_NAME = "disk-usage.csv"
SVG_NAME = "disk-usage.svg"
# Where earlier versions committed these on the records branch itself.
LEGACY_PATHS = ("disk-usage/disk-usage.csv", "disk-usage/disk-usage.svg", "disk-usage.csv", "disk-usage.svg")
FIELDS = ("timestamp_utc", "filesystem", "mount", "size_bytes", "used_bytes", "avail_bytes", "use_percent")
START, END = "<!-- runner-disk-usage:start -->", "<!-- runner-disk-usage:end -->"

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


def git(tracking: Path, /, *args: str, input: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(tracking), *args], input=input, capture_output=True, text=True, check=check
    )
    output = result.stdout if result.returncode == 0 else ""
    return output


def previous_csv(tracking: Path, /) -> str:
    """The history so far: the local branch (newest, if a push failed), the
    pushed branch, or the CSV an earlier version committed on HEAD."""
    git(tracking, "fetch", "-q", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", check=False)
    candidates = [f"refs/heads/{BRANCH}:{CSV_NAME}", f"refs/remotes/origin/{BRANCH}:{CSV_NAME}"]
    candidates += [f"HEAD:{path}" for path in LEGACY_PATHS if path.endswith(".csv")]
    for candidate in candidates:
        text = git(tracking, "show", candidate, check=False)
        if text:
            return text
    return ""


def append_row(previous: str, /, *, row: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
    rows = list(csv.DictReader(io.StringIO(previous))) if previous else []
    rows.append(row)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue(), rows


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


def replace_branch(tracking: Path, /, *, files: dict[str, str], message: str) -> None:
    """Point the local disk-usage branch at one parentless commit holding *files*."""
    entries = []
    for name, content in sorted(files.items()):
        blob = git(tracking, "hash-object", "-w", "--stdin", input=content).strip()
        entries.append(f"100644 blob {blob}\t{name}\n")
    tree = git(tracking, "mktree", input="".join(entries)).strip()
    commit = git(tracking, "commit-tree", tree, "-m", message).strip()
    git(tracking, "update-ref", f"refs/heads/{BRANCH}", commit)


def remove_legacy_files(tracking: Path, /) -> None:
    """Take the files and README section earlier versions committed off the records branch, once."""
    tracked = [path for path in LEGACY_PATHS if git(tracking, "ls-files", "--", path).strip()]
    readme = tracking / "README.md"
    text = readme.read_text() if readme.exists() else ""
    if START in text:
        before, rest = text.split(START, 1)
        after = rest.split(END, 1)[1] if END in rest else ""
        readme.write_text(f"{before.rstrip()}\n{after.lstrip()}" if after.strip() else f"{before.rstrip()}\n")
        git(tracking, "add", "--", "README.md")
    if tracked:
        git(tracking, "rm", "-q", "--", *tracked)
    if tracked or START in text:
        git(tracking, "commit", "-q", "-m", f"Moved runner disk usage to the {BRANCH} branch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tracking", type=Path, help="The tracking dataset's local clone.")
    parser.add_argument("work", type=Path, help="Any path on the drive to measure.")
    parser.add_argument("--threshold", type=int, default=50, help="Alert threshold drawn on the chart, in percent.")
    args = parser.parse_args()

    row = measure(args.work)
    text, rows = append_row(previous_csv(args.tracking), row=row)
    svg = render_svg(rows=rows, threshold=args.threshold)
    replace_branch(
        args.tracking,
        files={CSV_NAME: text, SVG_NAME: svg},
        message=f"Runner disk usage ({row['use_percent']}% used)",
    )
    remove_legacy_files(args.tracking)

    percent = row["use_percent"]
    summary = f"{human(int(row['used_bytes']))} of {human(int(row['size_bytes']))} used ({percent}%), {human(int(row['avail_bytes']))} free on {row['mount']}"
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write(f"percent={percent}\nsummary={summary}\n")
    print(f"Runner work drive: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
