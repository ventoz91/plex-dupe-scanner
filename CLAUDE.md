# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the scanner

```bash
# Activate the venv first
source .venv/bin/activate

# Run with the default config.json
python plex_dupe_scan.py

# Run with a custom config
python plex_dupe_scan.py -c /path/to/config.json
```

## Installing dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # only dependency: paramiko
```

## Architecture

Everything lives in a single file: `plex_dupe_scan.py`.

**Duplicate scan flow:**
1. Load `config.json` (SSH creds, media paths, file extensions, scoring preferences)
2. SSH into the Plex server via `connect_ssh()` — tries key/agent auth first, falls back to password prompt
3. Execute a minified Python one-liner on the remote host via `run_remote_python()` that walks `media_paths` and returns a JSON list of `{path, name, size, mtime}` for every matching file
4. `group_duplicates()` builds a key per file via `make_group_key()`:
   - `normalize_title()` strips resolution/codec tags and punctuation
   - `_season_episode_from_name()` picks up `SxxExx` already embedded in the normalised string
   - When a filename has no season marker, `_season_from_path()` reads it from the directory path (e.g. `/Season 7/`) so featurettes stored across multiple season folders don't falsely collide
5. `score_file()` ranks each group by `resolution_order` → `codec_order` → file size, with the highest-scoring file marked KEEP
6. `write_reports()` produces two files in `./reports/`:
   - `plex_duplicate_report_<stamp>.md` — human-readable table of keep/purge decisions
   - `plex_purge_candidates_<stamp>.sh` — `rm -Iv` commands to run on the Plex server

**Torrent cleanup flow** (runs when `torrent_paths` is set in config):
1. `run_remote_torrent_scan()` lists top-level entries (files and folders) in each torrent directory, including recursive size
2. `build_torrent_match_sets()` builds three key sets from the media scan — exact keys, season-only keys (for season packs), and title-only keys (for torrents that omit year)
3. `normalize_for_torrent_match()` strips release group suffixes (`-FGT`, `-RARBG`, etc.) and extra torrent terms (`complete`, `proper`, etc.) in addition to the standard quality tags
4. `classify_torrents()` splits entries into **matched** (found in Plex) and **orphans** (not found)
5. `write_torrent_report()` produces:
   - `plex_torrent_cleanup_<stamp>.md` — report with matched/orphan tables
   - `plex_torrent_cleanup_<stamp>.sh` — for matched: `rm -rIv` (interactive); for orphans: `mv` line active + `rm` line commented out so you pick one before running

## Config fields

| Field | Purpose |
|---|---|
| `ssh.host/port/username` | SSH connection target |
| `media_paths` | Directories walked on the remote host for the media scan |
| `torrent_paths` | Top-level torrent download directories; omit to skip torrent cleanup |
| `torrent_orphan_move_dest` | Destination path used in the `mv` line for orphan entries (default `MOVE_DESTINATION/`) |
| `output_dir` | Local directory for reports (default `./reports`) |
| `extensions` | File extensions to include (e.g. `.mkv`, `.mp4`) |
| `prefer.resolution_order` | Ranked list; first entry is most preferred |
| `prefer.codec_order` | Ranked list; first entry is most preferred |
| `prefer.prefer_larger_file` | Adds file size (MB) to score when `true` |
