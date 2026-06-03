# plex-dupe-scanner

> [!WARNING]
> **This tool generates scripts that permanently delete files from your server.**
> Always review every generated `.sh` file line by line before running it.
> Deleted files are **not** moved to trash — they are gone. The authors accept no
> responsibility for data loss. You have been warned.

Scans your Plex media library over SSH, groups duplicate files by title, and optionally compares your torrent download directories against the library. Generates a human-readable Markdown report and a ready-to-run shell script for each task.

## Requirements

- Python 3.10+
- SSH access to your Plex server (key-based or password)
- `python3` available on the Plex server (standard on most Linux installs)
- The Plex server's host key must be in your local `~/.ssh/known_hosts`

> **First-time setup:** If you have not SSHed into the server before, run  
> `ssh-keyscan -H <host> >> ~/.ssh/known_hosts`  
> or simply `ssh <user>@<host>` once and accept the fingerprint.

## Setup

```bash
git clone https://github.com/ventoz91/plex-dupe-scanner.git
cd plex-dupe-scanner

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp config.example.json config.json
# Edit config.json with your server details
```

## Configuration

`config.json` is gitignored — it stays local. Use `config.example.json` as the template.

| Field | Required | Description |
|---|:---:|---|
| `ssh.host` | ✓ | IP address or hostname of your Plex server |
| `ssh.port` | | SSH port (default: `22`) |
| `ssh.username` | ✓ | SSH username |
| `ssh.sudo` | | Set `true` if the SSH user needs `sudo` to delete torrent files (default: `false`) |
| `media_paths` | ✓ | Directories on the server to scan for media files |
| `extensions` | ✓ | File extensions to include in the scan |
| `output_dir` | | Local directory for generated reports (default: `./reports`) |
| `min_size_mb` | | Skip files below this size in MB when detecting duplicates (default: `0` — no filter) |
| `prefer.resolution_order` | | Ranked list of resolutions — first is most preferred |
| `prefer.codec_order` | | Ranked list of codecs — first is most preferred |
| `prefer.prefer_larger_file` | | Add file size to the score when comparing duplicates (default: `true`) |
| `torrent_paths` | | Torrent download directories on the server — omit to skip torrent cleanup |
| `torrent_orphan_move_dest` | | Destination path used in the `mv` line for orphan torrents |

> **Note on `prefer_larger_file`:** The size bonus is in raw MB, which can let a very large
> lower-resolution file outscore a smaller higher-resolution one. If you find the scoring
> counter-intuitive, set `prefer_larger_file: false` and rely on resolution/codec order alone.
> See TODO in `score_file()` for a planned fix.

### Example config

```json
{
  "ssh": {
    "host": "192.168.1.100",
    "port": 22,
    "username": "admin"
  },
  "media_paths": [
    "/mnt/plex/media/movies",
    "/mnt/plex/media/tv"
  ],
  "torrent_paths": [
    "/mnt/plex/torrents/movies",
    "/mnt/plex/torrents/tv"
  ],
  "torrent_orphan_move_dest": "/mnt/plex/staging/",
  "output_dir": "./reports",
  "extensions": [".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv"],
  "min_size_mb": 100,
  "prefer": {
    "resolution_order": ["2160p", "1080p", "720p", "480p"],
    "codec_order": ["x265", "hevc", "x264", "h264"],
    "prefer_larger_file": true
  }
}
```

## Running

```bash
source .venv/bin/activate
python plex_dupe_scan.py
```

To use a different config file:

```bash
python plex_dupe_scan.py -c /path/to/my-config.json
```

SSH authentication tries your SSH agent / keys first. If that fails, you will be prompted for a password.

### Dry run

To see what would be found without writing any files:

```bash
python plex_dupe_scan.py --dry-run
```

This connects, scans, and prints a summary table — no reports or scripts are written.

### Applying a script remotely

Once you have reviewed a generated script, you can run it on the server directly from your machine instead of copying it over manually:

1. Open the generated `.sh` file in any editor.
2. Remove any lines you do not want to execute.
3. Change `HasBeenChecked=false` to `HasBeenChecked=true`.
4. Run:

```bash
python plex_dupe_scan.py --apply reports/plex_purge_candidates_<timestamp>.sh
```

If your SSH user doesn't own the torrent files (e.g. they're owned by the torrent client), set `ssh.sudo: true` in config. You'll be prompted for the sudo password before connecting.

The script will SSH into the server, run the cleanup, and print a summary:

```
Script:  plex_purge_candidates_2026-06-03_14-00-00.sh
Targets: 12 entries (47.30 GB)

── Summary ──────────────────────────────────────────────
  Targets processed:           12 / 12
  Files removed:               12
  Directories removed:         0
  Items moved:                 0
  Space freed (estimated):     47.30 GB

  Directories affected (3):
    /home/data/plex/data/media/movies
    /home/data/plex/data/media/tv/Breaking Bad
    /home/data/plex/data/media/tv/The Office
```

> The `-I` interactive confirmation flag is stripped automatically since you already reviewed the file.
> Space freed is computed from targets that were actually confirmed removed — if any operations failed,
> they will appear in the Errors section and are excluded from the total.

### Running scripts manually

As an alternative to `--apply`, copy the script to your server and run it there:

```bash
scp reports/plex_purge_candidates_<timestamp>.sh admin@192.168.1.100:~/
ssh admin@192.168.1.100
bash ~/plex_purge_candidates_<timestamp>.sh
```

## Output

All output files are written to `output_dir` (default: `./reports`).

### Duplicate scan

| File | Description |
|---|---|
| `plex_duplicate_report_<timestamp>.md` | Grouped table of duplicates with KEEP / PURGE? labels and sizes |
| `plex_purge_candidates_<timestamp>.sh` | Shell script using `rm -Iv` (interactive) — run on the Plex server |

Files within each duplicate group are ranked by your `prefer` settings. The highest-scoring file is marked KEEP; the rest are marked PURGE?. Review the report before running the script.

### Torrent cleanup (when `torrent_paths` is configured)

| File | Description |
|---|---|
| `plex_torrent_cleanup_<timestamp>.md` | Report listing matched, alternate, and orphan torrent entries |
| `plex_torrent_cleanup_<timestamp>.sh` | Shell script for cleanup — run on the Plex server |

Torrents are classified into three tiers:

| Tier | Meaning | Script action |
|---|---|---|
| **Exact match** | Torrent name closely matches a Plex file | `rm -rIv` (interactive per item) |
| **Plex has alternate** | Title exists in Plex under a different edition/quality | `rm -rf` (direct delete) |
| **True orphan** | No version of this title in Plex | `mv`/`# rm` pair — comment out the one you don't want |

True orphan entries look like this in the script:

```bash
mv -v -- '/path/to/orphan' '/path/to/staging/'
# rm -rf -- '/path/to/orphan'
```

Comment out the `mv` line to delete instead, or comment out the `rm` line to move.

### Report retention

Each scan keeps the **5 most recent** report/script pairs per type and deletes older ones automatically. Change the `keep` value in `prune_old_reports()` in `plex_dupe_scan.py` if you want to retain more.
