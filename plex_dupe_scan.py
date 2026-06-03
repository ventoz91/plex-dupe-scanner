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


def validate_config(config: dict) -> None:
    ssh = config.get("ssh", {})
    errors: list[str] = []
    if not ssh.get("host"):
        errors.append("  ssh.host is required")
    if not ssh.get("username"):
        errors.append("  ssh.username is required")
    if not isinstance(config.get("media_paths"), list) or not config.get("media_paths"):
        errors.append("  media_paths must be a non-empty list")
    if not isinstance(config.get("extensions"), list) or not config.get("extensions"):
        errors.append("  extensions must be a non-empty list")
    if errors:
        sys.exit("Config errors:\n" + "\n".join(errors))


def connect_ssh(config: dict) -> paramiko.SSHClient:
    ssh_cfg = config["ssh"]

    host = ssh_cfg["host"]
    port = ssh_cfg.get("port", 22)
    username = ssh_cfg["username"]

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    try:
        client.load_host_keys(os.path.expanduser("~/.ssh/known_hosts"))
    except (FileNotFoundError, OSError):
        pass
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

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
    except paramiko.BadHostKeyException as e:
        sys.exit(f"Host key mismatch for {host}: {e}\nCheck ~/.ssh/known_hosts")
    except paramiko.AuthenticationException as e:
        print(f"Key/agent auth failed ({e}), trying password...")
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
    except (paramiko.SSHException, OSError) as e:
        if "not found in known_hosts" in str(e):
            sys.exit(
                f"Host key for {host} not in known_hosts.\n"
                f"Run once to add it:  ssh-keyscan -H {host} >> ~/.ssh/known_hosts"
            )
        sys.exit(f"SSH connection failed: {e}")


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
    r"\batmos\b", r"\bweb\b",
    # Streaming service tags
    r"\bamzn\b", r"\bdsnp\b", r"\bnf\b", r"\bhmax\b", r"\bpmtp\b",
    # Additional codec / format tags not in QUALITY_JUNK
    r"\bav1\b", r"\bopus\b", r"\bh[\s\-]?265\b", r"\bh[\s\-]?264\b",
    r"\bddp\d*\b", r"\bdd\d+[\s\-]\d+\b",
]

# Where the actual title ends and "technical" info begins.
# Used to extract a bare show/movie name for broad alternate matching.
_TITLE_BOUNDARY = re.compile(
    r"\b(?:"
    r"19\d{2}|20[0-2]\d|"               # year
    r"2160p|1080p|720p|480p|4k|uhd|"    # resolution
    r"bluray|blu.ray|webrip|web.dl|hdtv|dvdrip|"  # source
    r"x265|hevc|x264|h264|"             # codec
    r"s\d{1,2}e\d{1,2}|s\d{1,2}(?=\b)|"  # SxxExx / Sxx
    r"complete|series|collection|trilogy|pack|"  # pack descriptors
    r"season|stagione|saison|staffel|temporada"  # "season" in common languages
    r")",
    re.IGNORECASE,
)


_MEDIA_EXTS = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".ts", ".iso", ".m2ts",
})


def _clean_torrent_name(name: str) -> str:
    """
    Pre-clean a raw torrent file/folder name before normalisation.

    Order matters:
      1. Strip URL watermarks first (may contain periods that confuse splitext)
         e.g. "www.SceneTime.com - " or "www.UIndex.org    -    "
      2. Strip leading [Group] brackets  e.g. "[SubsPlease] ", "[HorribleSubs] "
      3. Strip media file extension (must happen before trailing bracket strip
         so "[F2AC7AA0].mkv" → "[F2AC7AA0]" → then the bracket is at the end)
      4. Strip trailing tracker tags  e.g. "[EZTVx.to]", "[TGx]", "[rarbg]"
      5. Strip trailing release group  e.g. "-FGT", "-MeGusta"
      6. Strip broadcaster prefixes  e.g. "PBS " prepended to NOVA/Frontline etc.
    """
    # 1. URL watermarks
    name = re.sub(r"^www\.\S+\s*[-–]+\s*", "", name, flags=re.IGNORECASE)
    # 2. Leading group brackets
    name = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", name)
    # 3. Media extension
    root, ext = os.path.splitext(name)
    if ext.lower() in _MEDIA_EXTS:
        name = root
    # 4. Trailing tracker/index tags
    name = re.sub(r"(?:\s*\[[^\]]+\])+\s*$", "", name)
    # 5. Trailing release group
    name = re.sub(r"-[A-Za-z0-9]+\s*$", "", name)
    # 6. Broadcaster prefixes (handle both space and dot as separator)
    name = re.sub(r"^(?:PBS|BBC|ABC|NBC|CBS)[.\s]+", "", name, flags=re.IGNORECASE)
    return name.strip()


def _strip_apostrophes(name: str) -> str:
    """Remove all apostrophe variants so Grey’s == Greys, Bob’s == Bobs."""
    return re.sub(r"[\u0027\u2018\u2019\u02bc]", "", name)


def normalize_title(filename: str) -> str:
    name = os.path.splitext(filename)[0].lower()
    name = _strip_apostrophes(name)

    for pat in _QUALITY_JUNK:
        name = re.sub(pat, " ", name, flags=re.IGNORECASE)

    name = re.sub(r"[\._\-\[\]\(\),]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


def normalize_for_torrent_match(name: str) -> str:
    """Like normalize_title but pre-cleans torrent-specific name patterns first."""
    name = _clean_torrent_name(name)
    name = _strip_apostrophes(name).lower()

    for pat in _QUALITY_JUNK + _TORRENT_JUNK:
        name = re.sub(pat, " ", name, flags=re.IGNORECASE)

    name = re.sub(r"[\._\-\[\]\(\),]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


def extract_base_title(name: str) -> str:
    """
    Extract just the show/movie title, stopping at the first year, resolution,
    codec, season marker, or pack descriptor.  Used for broad alternate matching.
    """
    name = _clean_torrent_name(name)
    name = _strip_apostrophes(name)
    name = re.sub(r"[\._\-\[\]\(\),]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().lower()

    m = _TITLE_BOUNDARY.search(name)
    title = name[:m.start()].strip() if m else name
    # Strip trailing bare episode number left by anime "- 24" style naming
    title = re.sub(r"\s+\d{1,3}\s*$", "", title).strip()
    # Strip trailing country/region disambiguator added by Plex or release groups
    # e.g. "Ghosts (US)" → "ghosts us" → "ghosts", "The Traitors (US)" → "the traitors"
    title = re.sub(r"\s+\b(?:us|uk|au|nz|ca)\b$", "", title)
    return title


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

    # TODO: The MB-scale bonus can let a very large lower-res file outscore a smaller
    # higher-res one (e.g. 50 GB 720p vs 2 GB 1080p). Consider log-scaling or capping.
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

def build_torrent_match_sets(files: list[dict]) -> tuple[set[str], set[str], set[str], set[str]]:
    """
    Build four key sets from scanned media files for torrent matching:

    exact_keys       – full normalised key per file (title + year, or title + SxxExx)
    season_keys      – show + season only, e.g. "friends s01" (for season-pack torrents)
    title_only_keys  – bare title without year or season (for torrents that omit year)
    base_title_keys  – show/movie name only, stopping before any technical token;
                       used for broad "alternate exists in Plex" matching
    """
    exact_keys: set[str] = set()
    season_keys: set[str] = set()
    title_only_keys: set[str] = set()
    base_title_keys: set[str] = set()

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

        # Broadest form: just the show/movie name before any technical token
        base = extract_base_title(item["name"])
        if base:
            base_title_keys.add(base)

    return exact_keys, season_keys, title_only_keys, base_title_keys


def torrent_in_plex(
    name: str,
    exact_keys: set[str],
    season_keys: set[str],
    title_only_keys: set[str],
) -> bool:
    """True when the torrent closely matches a specific Plex entry."""
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


def torrent_has_alternate(name: str, base_title_keys: set[str]) -> bool:
    """
    True when the torrent's show/movie title exists in Plex under any version.

    Used for orphans that slipped through exact matching (e.g. a complete-series
    pack or a differently-labelled edition) — Plex already has the content, so
    the torrent is redundant even if the names don't align precisely.
    """
    base = extract_base_title(name)
    return bool(base) and base in base_title_keys


def classify_torrents(
    torrents: list[dict],
    exact_keys: set[str],
    season_keys: set[str],
    title_only_keys: set[str],
    base_title_keys: set[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Returns (matched, has_alternate, true_orphans).

    matched       – close match to a Plex entry → interactive rm
    has_alternate – title exists in Plex but naming didn't align exactly
                    (different quality, edition, complete-series pack, etc.) → direct rm
    true_orphans  – nothing in Plex for this title → mv/rm choice
    """
    matched: list[dict] = []
    has_alternate: list[dict] = []
    true_orphans: list[dict] = []

    for t in torrents:
        if torrent_in_plex(t["name"], exact_keys, season_keys, title_only_keys):
            matched.append(t)
        elif torrent_has_alternate(t["name"], base_title_keys):
            has_alternate.append(t)
        else:
            true_orphans.append(t)

    return matched, has_alternate, true_orphans


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

def _script_header(targets_meta: list[dict]) -> str:
    """Return the standard header block written at the top of every generated script."""
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by plex-dupe-scanner",
        "# To run remotely: python plex_dupe_scan.py --apply <this-file>",
        "",
        "# Review this script, remove any lines you do not want, then set",
        "# HasBeenChecked=true below.  The --apply command will refuse to run",
        "# until that flag is set.",
        "HasBeenChecked=false",
        "",
        'if [[ "$HasBeenChecked" != "true" ]]; then',
        "    echo 'Set HasBeenChecked=true in this file once you have reviewed it.' >&2",
        "    exit 1",
        "fi",
        "",
        f"# __TARGETS__={json.dumps(targets_meta)}",
        "",
        "set -euo pipefail",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_reports(config: dict, duplicate_groups: dict[str, list[dict]]) -> tuple[Path, Path]:
    output_dir = Path(config.get("output_dir", "./reports"))
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = output_dir / f"plex_duplicate_report_{stamp}.md"
    purge_path = output_dir / f"plex_purge_candidates_{stamp}.sh"

    prefer = config.get("prefer", {})

    # Pre-compute sorted groups and collect delete targets for script metadata
    sorted_groups: list[tuple[str, dict, list[dict]]] = []
    targets_meta: list[dict] = []
    total_delete_bytes = 0

    for key, items in sorted(duplicate_groups.items()):
        ranked = sorted(items, key=lambda item: score_file(item, prefer), reverse=True)
        keep = ranked[0]
        candidates = ranked[1:]
        sorted_groups.append((key, keep, candidates))
        for item in candidates:
            total_delete_bytes += item["size"]
            targets_meta.append({"path": item["path"], "size": item["size"], "is_dir": False})

    with open(report_path, "w", encoding="utf-8") as report, \
         open(purge_path, "w", encoding="utf-8") as purge:

        report.write("# Plex Duplicate Report\n\n")
        report.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        report.write(f"Duplicate groups found: **{len(duplicate_groups)}**\n\n")

        purge.write(_script_header(targets_meta))

        for key, keep, candidates in sorted_groups:
            report.write(f"## {key}\n\n")
            report.write(f"**Suggested keep:** `{keep['path']}`\n\n")
            report.write("| Decision | Size | File |\n")
            report.write("|---|---:|---|\n")
            report.write(f"| KEEP | {human_size(keep['size'])} | `{keep['path']}` |\n")

            purge.write(f"\n# Duplicate group: {key}\n")
            purge.write(f"# KEEP: {keep['path']}\n")

            for item in candidates:
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
    has_alternate: list[dict],
    true_orphans: list[dict],
) -> tuple[Path, Path]:
    output_dir = Path(config.get("output_dir", "./reports"))
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = output_dir / f"plex_torrent_cleanup_{stamp}.md"
    script_path = output_dir / f"plex_torrent_cleanup_{stamp}.sh"

    all_entries = matched + has_alternate + true_orphans
    targets_meta = [{"path": t["path"], "size": t["size"], "is_dir": t["is_dir"]} for t in all_entries]

    total_matched = sum(t["size"] for t in matched)
    total_alternate = sum(t["size"] for t in has_alternate)
    total_orphan = sum(t["size"] for t in true_orphans)
    total_all = total_matched + total_alternate + total_orphan

    with open(report_path, "w", encoding="utf-8") as report, \
         open(script_path, "w", encoding="utf-8") as script:

        report.write("# Plex Torrent Cleanup Report\n\n")
        report.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        report.write(f"- Exact Plex match (review before deleting): **{len(matched)}** ({human_size(total_matched)})\n")
        report.write(f"- Plex has alternate version (safe to delete): **{len(has_alternate)}** ({human_size(total_alternate)})\n")
        report.write(f"- True orphans (no Plex title match): **{len(true_orphans)}** ({human_size(total_orphan)})\n\n")

        script.write(_script_header(targets_meta))

        if matched:
            report.write("## Exact Plex Match\n\n")
            report.write("These torrents match an entry in your Plex library directly. ")
            report.write("Delete them if you no longer need to seed.\n\n")
            report.write("| Size | Path |\n")
            report.write("|---:|---|\n")

            script.write("# ── Exact Plex match — delete interactively ─────────────────────\n\n")

            for t in sorted(matched, key=lambda x: x["path"]):
                report.write(f"| {human_size(t['size'])} | `{t['path']}` |\n")
                flag = "-rIv" if t["is_dir"] else "-Iv"
                script.write(f"rm {flag} -- {shell_quote(t['path'])}\n")

            report.write("\n")

        if has_alternate:
            report.write("## Plex Has Alternate Version\n\n")
            report.write("Plex already has this title but the torrent name did not align precisely ")
            report.write("(different edition, quality tier, complete-series pack, etc.). ")
            report.write("The content is covered — these are safe to delete.\n\n")
            report.write("| Size | Path |\n")
            report.write("|---:|---|\n")

            script.write("\n# ── Plex has alternate version — delete directly ─────────────────\n\n")

            for t in sorted(has_alternate, key=lambda x: x["path"]):
                report.write(f"| {human_size(t['size'])} | `{t['path']}` |\n")
                flag = "-rfv" if t["is_dir"] else "-fv"
                script.write(f"rm {flag} -- {shell_quote(t['path'])}\n")

            report.write("\n")

        if true_orphans:
            move_dest = config.get("torrent_orphan_move_dest", "MOVE_DESTINATION/")

            report.write("## True Orphans\n\n")
            report.write("No version of this title was found in your Plex library. ")
            report.write("They may be failed downloads, in-progress imports, or content removed from Plex.\n\n")
            report.write("| Size | Path |\n")
            report.write("|---:|---|\n")

            script.write("\n# ── True orphans — no Plex title match ──────────────────────────\n")
            script.write("# For each entry: keep the action you want and comment out the other.\n\n")

            for t in sorted(true_orphans, key=lambda x: x["path"]):
                report.write(f"| {human_size(t['size'])} | `{t['path']}` |\n")
                rm_flag = "-rf" if t["is_dir"] else "-f"
                script.write(f"mv -v -- {shell_quote(t['path'])} {shell_quote(move_dest)}\n")
                script.write(f"# rm {rm_flag} -- {shell_quote(t['path'])}\n\n")

        report.write(f"\n---\n\n")
        report.write(f"Total recoverable: **{human_size(total_all)}**\n")

    script_path.chmod(0o755)
    return report_path, script_path


def prune_old_reports(output_dir: Path, keep: int = 5) -> None:
    """Delete report/script sets beyond the most recent `keep` runs of each type."""
    families = [
        # (glob anchor, companion prefixes to delete alongside)
        ("plex_duplicate_report", ["plex_duplicate_report", "plex_purge_candidates"]),
        ("plex_torrent_cleanup",  ["plex_torrent_cleanup"]),
    ]

    for anchor_prefix, cleanup_prefixes in families:
        anchors = sorted(output_dir.glob(f"{anchor_prefix}_*.md"), reverse=True)
        if len(anchors) <= keep:
            continue
        for old_anchor in anchors[keep:]:
            ts = old_anchor.stem[len(anchor_prefix) + 1:]
            for prefix in cleanup_prefixes:
                for old_file in output_dir.glob(f"{prefix}_{ts}.*"):
                    old_file.unlink(missing_ok=True)


# ── Remote apply ──────────────────────────────────────────────────────────────

def apply_script(config: dict, script_path_str: str) -> None:
    script_path = Path(script_path_str)
    if not script_path.exists():
        print(f"Script not found: {script_path}")
        sys.exit(1)

    content = script_path.read_text(encoding="utf-8")

    if not re.search(r"^HasBeenChecked=true\s*$", content, re.MULTILINE):
        print("Script has not been marked as reviewed.")
        print(f"Open {script_path} and change  HasBeenChecked=false  to  HasBeenChecked=true")
        sys.exit(1)

    meta_match = re.search(r"^# __TARGETS__=(.+)$", content, re.MULTILINE)
    if not meta_match:
        print("Script is missing target metadata — was it generated by this tool?")
        sys.exit(1)

    targets: list[dict] = json.loads(meta_match.group(1))
    target_paths = {t["path"] for t in targets}
    planned_size = sum(t["size"] for t in targets)

    use_sudo = config.get("ssh", {}).get("sudo", False)
    sudo_password: str | None = None
    if use_sudo:
        sudo_password = getpass.getpass("Sudo password for remote server: ")

    print(f"Script:  {script_path.name}")
    print(f"Targets: {len(targets)} entries ({human_size(planned_size)})")
    if use_sudo:
        print("Mode:    sudo")
    print()

    # Prepare for non-interactive remote execution:
    # • Strip -I (interactive confirm) — user already reviewed
    # • Swap set -euo pipefail for set -u so every command runs even if one fails
    exec_lines = []
    for line in content.splitlines():
        line = re.sub(r"\brm -rIv\b", "rm -rv", line)
        line = re.sub(r"\brm -Iv\b",  "rm -v",  line)
        if line.strip() == "set -euo pipefail":
            line = "set -u"
        exec_lines.append(line)
    exec_script = "\n".join(exec_lines)

    print("Connecting to Plex machine...")
    client = connect_ssh(config)

    try:
        print("Running script on server...")
        remote_cmd = "sudo -S bash -s" if use_sudo else "bash -s"
        stdin, stdout, stderr_ch = client.exec_command(remote_cmd, timeout=None)
        if use_sudo and sudo_password is not None:
            stdin.write(sudo_password + "\n")
        stdin.write(exec_script)
        stdin.channel.shutdown_write()

        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr_ch.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        # Count removed/moved items from verbose output
        files_removed = 0
        dirs_removed = 0
        top_level_done: set[str] = set()
        moved_count = 0

        for line in out_text.splitlines():
            m = re.match(r"removed directory '(.+)'$", line)
            if m:
                dirs_removed += 1
                if m.group(1) in target_paths:
                    top_level_done.add(m.group(1))
                continue
            m = re.match(r"removed '(.+)'$", line)
            if m:
                files_removed += 1
                if m.group(1) in target_paths:
                    top_level_done.add(m.group(1))
                continue
            if " -> " in line:
                moved_count += 1
                # mv -v: '/src' -> '/dest'
                mv_m = re.match(r"'(.+)' -> ", line)
                if mv_m and mv_m.group(1) in target_paths:
                    top_level_done.add(mv_m.group(1))

        # Filter out the sudo password prompt that lands on stderr
        err_lines = [
            l for l in err_text.splitlines()
            if l.strip() and not re.match(r"^\[sudo\] password", l)
        ]

        actual_size_freed = sum(t["size"] for t in targets if t["path"] in top_level_done)
        affected_dirs = {str(Path(p).parent) for p in top_level_done}

        w = 28
        print()
        print("── Summary " + "─" * 46)
        print(f"  {'Targets processed:':{w}} {len(top_level_done)} / {len(targets)}")
        print(f"  {'Files removed:':{w}} {files_removed}")
        print(f"  {'Directories removed:':{w}} {dirs_removed}")
        print(f"  {'Items moved:':{w}} {moved_count}")
        print(f"  {'Space freed (estimated):':{w}} {human_size(actual_size_freed)}")
        print()
        print(f"  Directories affected ({len(affected_dirs)}):")
        for d in sorted(affected_dirs):
            print(f"    {d}")

        if err_lines:
            print()
            print("── Errors " + "─" * 47)
            for line in err_lines:
                print(f"  {line}")

        print()
        print("Done." if exit_code == 0 else f"Script exited with code {exit_code}.")

    finally:
        client.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scan Plex media over SSH for duplicate media files.")
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="Path to config JSON file.",
    )
    parser.add_argument(
        "--apply",
        metavar="SCRIPT",
        help="SSH into the server and run a reviewed script (HasBeenChecked must be true).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print duplicate/torrent stats without writing any report files.",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    validate_config(config)

    if args.apply:
        apply_script(config, args.apply)
        return

    print("Connecting to Plex machine...")
    client = connect_ssh(config)

    try:
        print("Scanning media files...")
        files = run_remote_python(client, config)
        print(f"Found {len(files)} media files.")

        min_size_bytes = int(config.get("min_size_mb", 0)) * 1024 * 1024
        if min_size_bytes:
            before = len(files)
            files = [f for f in files if f["size"] >= min_size_bytes]
            filtered = before - len(files)
            if filtered:
                print(f"Filtered {filtered} files below {config['min_size_mb']} MB.")

        duplicate_groups = group_duplicates(files)
        print(f"Found {len(duplicate_groups)} possible duplicate groups.")

        torrents: list[dict] = []
        matched: list[dict] = []
        has_alternate: list[dict] = []
        true_orphans: list[dict] = []

        if config.get("torrent_paths"):
            print("Scanning torrent directories...")
            torrents = run_remote_torrent_scan(client, config)
            if torrents:
                print(f"Found {len(torrents)} torrent entries.")
                exact_keys, season_keys, title_only_keys, base_title_keys = build_torrent_match_sets(files)
                matched, has_alternate, true_orphans = classify_torrents(
                    torrents, exact_keys, season_keys, title_only_keys, base_title_keys
                )
                print(f"  Exact match: {len(matched)}, Plex has alternate: {len(has_alternate)}, True orphans: {len(true_orphans)}")
            else:
                print("No torrent entries found.")

        if args.dry_run:
            prefer = config.get("prefer", {})
            purge_bytes = sum(
                item["size"]
                for items in duplicate_groups.values()
                for item in sorted(items, key=lambda i: score_file(i, prefer), reverse=True)[1:]
            )
            purge_count = sum(len(items) - 1 for items in duplicate_groups.values())
            print()
            print("── Dry run summary " + "─" * 38)
            print(f"  Duplicate groups:   {len(duplicate_groups)}")
            print(f"  Files to purge:     {purge_count} ({human_size(purge_bytes)})")
            if torrents:
                print(f"  Torrent matched:    {len(matched)} ({human_size(sum(t['size'] for t in matched))})")
                print(f"  Plex has alternate: {len(has_alternate)} ({human_size(sum(t['size'] for t in has_alternate))})")
                print(f"  True orphans:       {len(true_orphans)} ({human_size(sum(t['size'] for t in true_orphans))})")
            return

        report_path, purge_path = write_reports(config, duplicate_groups)
        print()
        print(f"Duplicate report:  {report_path}")
        print(f"Purge script:      {purge_path}")

        if torrents:
            torrent_report, torrent_script = write_torrent_report(config, matched, has_alternate, true_orphans)
            print()
            print(f"Torrent cleanup report: {torrent_report}")
            print(f"Torrent cleanup script: {torrent_script}")

        output_dir = Path(config.get("output_dir", "./reports"))
        prune_old_reports(output_dir)

        print()
        print("Review the report(s) first, then run:")
        print("  python plex_dupe_scan.py --apply <script.sh>")

    finally:
        client.close()


if __name__ == "__main__":
    main()
