#!/usr/bin/env python3

import argparse
import getpass
import json
import os
import re
import shlex
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import paramiko


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def connect_ssh(config: dict) -> paramiko.SSHClient:
    ssh_cfg = config["ssh"]

    host = ssh_cfg["host"]
    port = ssh_cfg.get("port", 22)
    username = ssh_cfg["username"]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            look_for_keys=True,
            allow_agent=True,
            timeout=15,
        )
        return client
    except Exception:
        password = getpass.getpass(f"SSH password for {username}@{host}: ")
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
        return client


def run_remote_python(client: paramiko.SSHClient, config: dict) -> list[dict]:
    payload = json.dumps({
        "media_paths": config["media_paths"],
        "extensions": config["extensions"],
    })

    command = "python3 -c " + shlex.quote(
        "import sys, json, os\n"
        "config=json.loads(sys.stdin.read())\n"
        "media_paths=config['media_paths']\n"
        "extensions=set(e.lower() for e in config['extensions'])\n"
        "results=[]\n"
        "for root_path in media_paths:\n"
        "    for root, dirs, files in os.walk(root_path):\n"
        "        for name in files:\n"
        "            ext=os.path.splitext(name)[1].lower()\n"
        "            if ext not in extensions:\n"
        "                continue\n"
        "            full_path=os.path.join(root,name)\n"
        "            try:\n"
        "                stat=os.stat(full_path)\n"
        "            except OSError:\n"
        "                continue\n"
        "            results.append({'path':full_path,'name':name,'size':stat.st_size,'mtime':stat.st_mtime})\n"
        "print(json.dumps(results))\n"
    )

    stdin, stdout, stderr = client.exec_command(command, timeout=None)
    stdin.write(payload)
    stdin.channel.shutdown_write()

    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")

    exit_code = stdout.channel.recv_exit_status()

    if exit_code != 0:
        raise RuntimeError(f"Remote scan failed:\n{error}")

    return json.loads(output)


def run_remote_torrent_scan(client: paramiko.SSHClient, config: dict) -> list[dict]:
    torrent_paths = config.get("torrent_paths", [])
    if not torrent_paths:
        return []

    payload = json.dumps({"torrent_paths": torrent_paths})

    command = "python3 -c " + shlex.quote(
        "import json,os,sys\n"
        "cfg=json.loads(sys.stdin.read())\n"
        "def sz(p):\n"
        "    if os.path.isfile(p):\n"
        "        try:return os.path.getsize(p)\n"
        "        except OSError:return 0\n"
        "    t=0\n"
        "    for r,_,fs in os.walk(p):\n"
        "        for f in fs:\n"
        "            fp=os.path.join(r,f)\n"
        "            try:\n"
        "                if not os.path.islink(fp):t+=os.path.getsize(fp)\n"
        "            except OSError:pass\n"
        "    return t\n"
        "out=[]\n"
        "for base in cfg.get('torrent_paths',[]):\n"
        "    if not os.path.isdir(base):continue\n"
        "    try:entries=sorted(os.listdir(base))\n"
        "    except OSError:continue\n"
        "    for e in entries:\n"
        "        fp=os.path.join(base,e)\n"
        "        try:\n"
        "            out.append({'path':fp,'name':e,'size':sz(fp),'is_dir':os.path.isdir(fp)})\n"
        "        except OSError:pass\n"
        "print(json.dumps(out))\n"
    )

    stdin, stdout, stderr = client.exec_command(command, timeout=None)
    stdin.write(payload)
    stdin.channel.shutdown_write()

    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")

    exit_code = stdout.channel.recv_exit_status()

    if exit_code != 0:
        raise RuntimeError(f"Remote torrent scan failed:\n{error}")

    return json.loads(output) if output.strip() else []


# ── Title normalisation ───────────────────────────────────────────────────────

_QUALITY_JUNK = [
    r"\b2160p\b", r"\b1080p\b", r"\b720p\b", r"\b480p\b",
    r"\buhd\b", r"\bbluray\b", r"\bblu-ray\b", r"\bwebrip\b",
    r"\bweb-dl\b", r"\bhdtv\b", r"\bdvdrip\b",
    r"\bx265\b", r"\bhevc\b", r"\bx264\b", r"\bh264\b",
    r"\b10bit\b", r"\b8bit\b", r"\baac\b", r"\bdts\b",
    r"\byify\b", r"\brarbg\b",
]

# Extra terms common in torrent names but never in Plex filenames
_TORRENT_JUNK = [
    r"\bcomplete\b", r"\bproper\b", r"\brepack\b",
    r"\bextended\b", r"\btheatrical\b", r"\bredux\b",
    r"\bremux\b", r"\bsdr\b", r"\bhdr\b", r"\bdolby\b",
    r"\batmos\b",
]


def normalize_title(filename: str) -> str:
    name = os.path.splitext(filename)[0].lower()

    for pat in _QUALITY_JUNK:
        name = re.sub(pat, " ", name, flags=re.IGNORECASE)

    name = re.sub(r"[\._\-\[\]\(\)]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


def normalize_for_torrent_match(name: str) -> str:
    """Like normalize_title but also strips release group suffix and torrent-specific terms."""
    name = os.path.splitext(name)[0]
    # Strip trailing release group, e.g. "-FGT", "-YIFY", "-NTG"
    name = re.sub(r"-[A-Za-z0-9]+\s*$", "", name)

    name = name.lower()
    for pat in _QUALITY_JUNK + _TORRENT_JUNK:
        name = re.sub(pat, " ", name, flags=re.IGNORECASE)

    name = re.sub(r"[\._\-\[\]\(\)]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


# ── Season / episode extraction ───────────────────────────────────────────────

def _season_episode_from_name(name: str) -> tuple[str | None, str | None]:
    """Return (season_tag, episode_tag) from filename, e.g. ('s01', 'e03')."""
    # SxxExx
    m = re.search(r"\bs(\d{1,2})e(\d{1,2})\b", name, re.IGNORECASE)
    if m:
        return f"s{int(m.group(1)):02d}", f"e{int(m.group(2)):02d}"
    # Season X Episode Y
    m = re.search(r"\bseason\s*(\d{1,2})\s*episode\s*(\d{1,2})\b", name, re.IGNORECASE)
    if m:
        return f"s{int(m.group(1)):02d}", f"e{int(m.group(2)):02d}"
    # Sxx only (season pack in filename)
    m = re.search(r"\bs(\d{1,2})\b", name, re.IGNORECASE)
    if m:
        return f"s{int(m.group(1)):02d}", None
    return None, None


def _season_from_path(path: str) -> str | None:
    """Extract season number from the directory path when the filename has none."""
    # /Season 7/ or /Season7/
    m = re.search(r"/[Ss]eason\s*(\d{1,2})/", path)
    if m:
        return f"s{int(m.group(1)):02d}"
    # /S07/ style directory component
    m = re.search(r"/[Ss](\d{2})/", path)
    if m:
        return f"s{int(m.group(1)):02d}"
    return None


def make_group_key(item: dict) -> str:
    """
    Produce the deduplication key for a media file.

    Includes season info from the path when it is absent from the filename,
    so featurettes stored in Season X subdirectories do not collide across
    different seasons.
    """
    name = item["name"]
    path = item["path"]

    normalized = normalize_title(name)
    if not normalized:
        return ""

    season, episode = _season_episode_from_name(name)

    if season is None:
        # Filename has no season marker — check the directory tree
        season = _season_from_path(path)
        if season:
            return f"{normalized} {season}"

    # season already embedded in normalized string via SxxExx token
    return normalized


# ── Duplicate grouping ────────────────────────────────────────────────────────

def detect_resolution(filename: str) -> str | None:
    match = re.search(r"\b(2160p|1080p|720p|480p)\b", filename, re.IGNORECASE)
    return match.group(1).lower() if match else None


def detect_codec(filename: str) -> str | None:
    match = re.search(r"\b(x265|hevc|x264|h264)\b", filename, re.IGNORECASE)
    return match.group(1).lower() if match else None


def score_file(item: dict, prefer: dict) -> int:
    score = 0

    resolution = detect_resolution(item["name"])
    codec = detect_codec(item["name"])

    resolution_order = prefer.get("resolution_order", [])
    codec_order = prefer.get("codec_order", [])

    if resolution in resolution_order:
        score += (len(resolution_order) - resolution_order.index(resolution)) * 1000

    if codec in codec_order:
        score += (len(codec_order) - codec_order.index(codec)) * 100

    if prefer.get("prefer_larger_file", True):
        score += int(item["size"] / 1024 / 1024)

    return score


def group_duplicates(files: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)

    for item in files:
        key = make_group_key(item)
        if key:
            groups[key].append(item)

    return {
        key: items
        for key, items in groups.items()
        if len(items) > 1
    }


# ── Torrent-to-Plex matching ──────────────────────────────────────────────────

def build_torrent_match_sets(files: list[dict]) -> tuple[set[str], set[str], set[str]]:
    """
    Build three key sets from scanned media files for torrent matching:

    exact_keys      – full normalised key per file (title + year, or title + SxxExx)
    season_keys     – show + season only, e.g. "friends s01" (for season-pack torrents)
    title_only_keys – bare title without year or season (for torrents that omit year)
    """
    exact_keys: set[str] = set()
    season_keys: set[str] = set()
    title_only_keys: set[str] = set()

    for item in files:
        norm = normalize_for_torrent_match(item["name"])
        if not norm:
            continue

        exact_keys.add(norm)

        # "friends s01e03" → season key "friends s01"
        season_stripped = re.sub(r"\b(s\d{2})e\d{2}\b", r"\1", norm)
        if season_stripped != norm:
            season_keys.add(season_stripped)

        # Strip trailing year or season/episode to get bare title
        title_only = re.sub(r"\s+(?:s\d{2}(?:e\d{2})?|\d{4})\s*$", "", norm).strip()
        if title_only and title_only != norm:
            title_only_keys.add(title_only)

    return exact_keys, season_keys, title_only_keys


def torrent_in_plex(
    name: str,
    exact_keys: set[str],
    season_keys: set[str],
    title_only_keys: set[str],
) -> bool:
    norm = normalize_for_torrent_match(name)
    if not norm:
        return False
    if norm in exact_keys:
        return True
    # Season-pack torrent matches any episode from that season
    if norm in season_keys:
        return True
    # Torrent name lacks year — match bare title
    if norm in title_only_keys:
        return True
    # Strip year from torrent key and try bare-title match
    norm_no_year = re.sub(r"\s+\d{4}\s*$", "", norm).strip()
    if norm_no_year != norm and norm_no_year in title_only_keys:
        return True
    return False


def classify_torrents(
    torrents: list[dict],
    exact_keys: set[str],
    season_keys: set[str],
    title_only_keys: set[str],
) -> tuple[list[dict], list[dict]]:
    matched: list[dict] = []   # found in Plex — ask before deleting
    orphans: list[dict] = []   # not in Plex — delete directly

    for t in torrents:
        if torrent_in_plex(t["name"], exact_keys, season_keys, title_only_keys):
            matched.append(t)
        else:
            orphans.append(t)

    return matched, orphans


# ── Formatting helpers ────────────────────────────────────────────────────────

def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)

    for unit in units:
        if value < 1024:
            return f"{value:.2f} {unit}"
        value /= 1024

    return f"{value:.2f} PB"


def shell_quote(path: str) -> str:
    return "'" + path.replace("'", "'\"'\"'") + "'"


# ── Report writers ────────────────────────────────────────────────────────────

def write_reports(config: dict, duplicate_groups: dict[str, list[dict]]) -> tuple[Path, Path]:
    output_dir = Path(config.get("output_dir", "./reports"))
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = output_dir / f"plex_duplicate_report_{stamp}.md"
    purge_path = output_dir / f"plex_purge_candidates_{stamp}.sh"

    prefer = config.get("prefer", {})

    with open(report_path, "w", encoding="utf-8") as report, open(purge_path, "w", encoding="utf-8") as purge:
        report.write(f"# Plex Duplicate Report\n\n")
        report.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        report.write(f"Duplicate groups found: **{len(duplicate_groups)}**\n\n")

        purge.write("#!/usr/bin/env bash\n")
        purge.write("set -euo pipefail\n\n")
        purge.write("# REVIEW THIS FILE BEFORE RUNNING.\n")
        purge.write("# This script uses rm -Iv so deletion is interactive.\n")
        purge.write("# Run this ON THE PLEX SERVER, not your local machine.\n\n")

        total_delete_bytes = 0

        for key, items in sorted(duplicate_groups.items()):
            sorted_items = sorted(
                items,
                key=lambda item: score_file(item, prefer),
                reverse=True,
            )

            keep = sorted_items[0]
            delete_candidates = sorted_items[1:]

            report.write(f"## {key}\n\n")
            report.write(f"**Suggested keep:** `{keep['path']}`\n\n")
            report.write("| Decision | Size | File |\n")
            report.write("|---|---:|---|\n")
            report.write(f"| KEEP | {human_size(keep['size'])} | `{keep['path']}` |\n")

            purge.write(f"\n# Duplicate group: {key}\n")
            purge.write(f"# KEEP: {keep['path']}\n")

            for item in delete_candidates:
                total_delete_bytes += item["size"]
                report.write(f"| PURGE? | {human_size(item['size'])} | `{item['path']}` |\n")
                purge.write(f"rm -Iv -- {shell_quote(item['path'])}\n")

            report.write("\n")

        report.write(f"\n---\n\n")
        report.write(f"Potential space recoverable: **{human_size(total_delete_bytes)}**\n")

    purge_path.chmod(0o755)

    return report_path, purge_path


def write_torrent_report(
    config: dict,
    matched: list[dict],
    orphans: list[dict],
) -> tuple[Path, Path]:
    output_dir = Path(config.get("output_dir", "./reports"))
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = output_dir / f"plex_torrent_cleanup_{stamp}.md"
    script_path = output_dir / f"plex_torrent_cleanup_{stamp}.sh"

    total_matched = sum(t["size"] for t in matched)
    total_orphan = sum(t["size"] for t in orphans)

    with open(report_path, "w", encoding="utf-8") as report, \
         open(script_path, "w", encoding="utf-8") as script:

        report.write("# Plex Torrent Cleanup Report\n\n")
        report.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        report.write(f"- Already in Plex (review before deleting): **{len(matched)}** ({human_size(total_matched)})\n")
        report.write(f"- Orphans (not found in Plex): **{len(orphans)}** ({human_size(total_orphan)})\n\n")

        script.write("#!/usr/bin/env bash\n")
        script.write("set -euo pipefail\n\n")
        script.write("# REVIEW THIS FILE BEFORE RUNNING.\n")
        script.write("# Run ON THE PLEX SERVER, not your local machine.\n\n")

        if matched:
            report.write("## Already in Plex\n\n")
            report.write("These torrents have a matching entry in your Plex library. ")
            report.write("Delete them if you no longer need to seed.\n\n")
            report.write("| Size | Path |\n")
            report.write("|---:|---|\n")

            script.write("# ── Already in Plex — delete interactively ──────────────────────\n\n")

            for t in sorted(matched, key=lambda x: x["path"]):
                report.write(f"| {human_size(t['size'])} | `{t['path']}` |\n")
                flag = "-rIv" if t["is_dir"] else "-Iv"
                script.write(f"rm {flag} -- {shell_quote(t['path'])}\n")

            report.write("\n")

        if orphans:
            move_dest = config.get("torrent_orphan_move_dest", "MOVE_DESTINATION/")

            report.write("## Orphans\n\n")
            report.write("These torrents have no matching entry in your Plex library. ")
            report.write("They may be failed downloads, incomplete imports, or mislabelled files.\n\n")
            report.write("| Size | Path |\n")
            report.write("|---:|---|\n")

            script.write("\n# ── Orphans — no Plex match found ───────────────────────────────\n")
            script.write("# For each entry: keep the action you want and comment out the other.\n\n")

            for t in sorted(orphans, key=lambda x: x["path"]):
                report.write(f"| {human_size(t['size'])} | `{t['path']}` |\n")
                rm_flag = "-rf" if t["is_dir"] else "-f"
                script.write(f"mv -v -- {shell_quote(t['path'])} {shell_quote(move_dest)}\n")
                script.write(f"# rm {rm_flag} -- {shell_quote(t['path'])}\n\n")

        report.write(f"\n---\n\n")
        report.write(f"Total recoverable: **{human_size(total_matched + total_orphan)}**\n")

    script_path.chmod(0o755)

    return report_path, script_path


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scan Plex media over SSH for duplicate media files.")
    parser.add_argument(
        "-c",
        "--config",
        default="config.json",
        help="Path to config JSON file.",
    )

    args = parser.parse_args()
    config = load_config(args.config)

    print("Connecting to Plex machine...")
    client = connect_ssh(config)

    try:
        print("Scanning media files...")
        files = run_remote_python(client, config)
        print(f"Found {len(files)} media files.")

        duplicate_groups = group_duplicates(files)
        print(f"Found {len(duplicate_groups)} possible duplicate groups.")
        report_path, purge_path = write_reports(config, duplicate_groups)

        print()
        print(f"Duplicate report:  {report_path}")
        print(f"Purge script:      {purge_path}")

        if config.get("torrent_paths"):
            print()
            print("Scanning torrent directories...")
            torrents = run_remote_torrent_scan(client, config)

            if torrents:
                print(f"Found {len(torrents)} torrent entries.")
                exact_keys, season_keys, title_only_keys = build_torrent_match_sets(files)
                matched, orphans = classify_torrents(torrents, exact_keys, season_keys, title_only_keys)
                print(f"  Matched to Plex: {len(matched)}, Orphans: {len(orphans)}")
                torrent_report, torrent_script = write_torrent_report(config, matched, orphans)
                print()
                print(f"Torrent cleanup report: {torrent_report}")
                print(f"Torrent cleanup script: {torrent_script}")
            else:
                print("No torrent entries found.")

        print()
        print("Review the report(s) first, then run the script(s) on the Plex server.")

    finally:
        client.close()


if __name__ == "__main__":
    main()
