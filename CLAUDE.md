# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the scanner

```bash
# Activate the venv first
source .venv/bin/activate

# Run with the default config.json
python plex_dupe_scan.py

# Dry run — stats only, no files written
python plex_dupe_scan.py --dry-run

# Run with a custom config
python plex_dupe_scan.py -c /path/to/config.json

# Apply a reviewed script remotely
python plex_dupe_scan.py --apply reports/<script>.sh
```

## Installing dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # only dependency: paramiko
```

## Architecture

Everything lives in a single file: `plex_dupe_scan.py`.

**Startup:**
- `validate_config()` checks required fields and exits with a clear message on misconfiguration
- `connect_ssh()` loads `~/.ssh/known_hosts` and uses `RejectPolicy` (no blind key acceptance); falls back to password prompt only on `AuthenticationException`

**Duplicate scan flow:**
1. Load `config.json` (SSH creds, media paths, file extensions, scoring preferences)
2. SSH into the Plex server via `connect_ssh()`
3. Execute a minified Python one-liner on the remote host via `run_remote_python()` that walks `media_paths` and returns a JSON list of `{path, name, size, mtime}` for every matching file
4. Files below `min_size_mb` (if set) are filtered out before grouping
5. `group_duplicates()` builds a key per file via `make_group_key()`:
   - `normalize_title()` strips resolution/codec tags and punctuation
   - `_season_episode_from_name()` picks up `SxxExx` already embedded in the normalised string
   - When a filename has no season marker, `_season_from_path()` reads it from the directory path (e.g. `/Season 7/`) so featurettes stored across multiple season folders don't falsely collide
6. `score_file()` ranks each group by `resolution_order` → `codec_order` → file size, with the highest-scoring file marked KEEP
7. `write_reports()` produces two files in `./reports/`:
   - `plex_duplicate_report_<stamp>.md` — human-readable table of keep/purge decisions
   - `plex_purge_candidates_<stamp>.sh` — `rm -Iv` commands to run on the Plex server

**Torrent cleanup flow** (runs when `torrent_paths` is set in config):
1. `run_remote_torrent_scan()` lists top-level entries (files and folders) in each torrent directory, including recursive size
2. `build_torrent_match_sets()` builds four key sets from the media scan — exact keys, season-only keys (for season packs), title-only keys (for torrents that omit year), and base-title keys (for broad alternate matching)
3. `normalize_for_torrent_match()` strips release group suffixes (`-FGT`, `-RARBG`, etc.) and extra torrent terms (`complete`, `proper`, etc.) in addition to the standard quality tags
4. `classify_torrents()` splits entries into three tiers: **matched** (close Plex match), **has_alternate** (title in Plex but naming differs), **true_orphans** (nothing in Plex)
5. `write_torrent_report()` produces:
   - `plex_torrent_cleanup_<stamp>.md` — report with matched/alternate/orphan tables
   - `plex_torrent_cleanup_<stamp>.sh` — matched: `rm -rIv`; alternate: `rm -rf`; orphans: `mv` + `# rm` pair

**`--apply` mode:**
1. Checks `HasBeenChecked=true` in the script; exits with instructions if not set
2. Extracts `__TARGETS__` JSON metadata for pre-computed size totals
3. Strips `-I` interactive flags, replaces `set -euo pipefail` with `set -u`
4. SSHes in, runs via `bash -s`, parses `rm -v`/`mv -v` output
5. Computes space freed and affected dirs from **actually-processed** targets only (not from metadata totals)

## Config fields

| Field | Purpose |
|---|---|
| `ssh.host/port/username` | SSH connection target |
| `media_paths` | Directories walked on the remote host for the media scan |
| `min_size_mb` | Skip files below this size (MB) when detecting duplicates; `0` disables (default) |
| `torrent_paths` | Top-level torrent download directories; omit to skip torrent cleanup |
| `torrent_orphan_move_dest` | Destination path used in the `mv` line for orphan entries (default `MOVE_DESTINATION/`) |
| `output_dir` | Local directory for reports (default `./reports`) |
| `extensions` | File extensions to include (e.g. `.mkv`, `.mp4`) |
| `prefer.resolution_order` | Ranked list; first entry is most preferred |
| `prefer.codec_order` | Ranked list; first entry is most preferred |
| `prefer.prefer_larger_file` | Adds file size (MB) to score when `true`; see TODO in `score_file()` for known weighting issue |
