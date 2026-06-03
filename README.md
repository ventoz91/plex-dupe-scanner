# plex-dupe-scanner

> [!WARNING]
> **This tool generates scripts that permanently delete files from your server.**
> Always review every generated `.sh` file line by line before running it.
> Deleted files are **not** moved to trash — they are gone. The authors accept no
> responsibility for data loss. You have been warned.

Scans your Plex media library over SSH, groups duplicate files by title, compares your torrent download directories against the library, and optionally flags junk/extras files. Generates a human-readable Markdown report and a ready-to-run shell script for each task.

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
| `ssh.sudo` | | Set `true` if the SSH user needs `sudo` to delete files (e.g. owned by torrent client) (default: `false`) |
| `media_paths` | ✓ | Directories on the server to scan for media files |
| `extensions` | ✓ | File extensions to include in the scan |
| `output_dir` | | Local directory for generated reports (default: `./reports`) |
| `min_size_mb` | | Skip files below this size in MB when detecting duplicates (default: `0` — no filter) |
| `prefer.resolution_order` | | Ranked list of resolutions — first is most preferred |
| `prefer.codec_order` | | Ranked list of codecs — first is most preferred |
| `prefer.prefer_larger_file` | | Add file size to the score when comparing duplicates (default: `true`) |
| `torrent_paths` | | Torrent download directories on the server — omit to skip torrent cleanup |
| `torrent_orphan_move_dest` | | Destination path used in the `mv` line for orphan torrents |
| `scan_junk` | | Set `true` to scan for junk/extras files in `media_paths` (default: `false`) |
| `keep_local_artwork` | | Preserve Plex artwork files (`poster.jpg`, `fanart.jpg`, etc.) during junk scan (default: `true`) |
| `editor` | | Editor used by `--review` (default: `"vim"`) |

> **Note on `prefer_larger_file`:** The size bonus is log-scaled so it acts as a tiebreaker
> within a quality tier rather than overriding it. A 50 GB file adds at most ~780 pts vs a
> 1000 pt gap between resolution tiers, so a larger 720p file will never outscore a 1080p file.
> Set `prefer_larger_file: false` to rely solely on resolution/codec order.

> **Note on `ssh.sudo`:** When enabled you will be prompted for the sudo password locally before
> connecting. The password is sent to the remote `sudo -S bash -s` process over the existing SSH
> channel — it is never stored.

### Example config

```json
{
  "ssh": {
    "host": "192.168.1.100",
    "port": 22,
    "username": "admin",
    "sudo": false
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
  },
  "scan_junk": false,
  "keep_local_artwork": true,
  "editor": "vim"
}
```

## Commands

### Scan (default)

```bash
source .venv/bin/activate
python plex_dupe_scan.py
```

Connects to the server, scans media files, and writes reports and scripts to `output_dir`.

To use a different config file:

```bash
python plex_dupe_scan.py -c /path/to/my-config.json
```

SSH authentication tries your agent / keys first. If that fails you will be prompted for a password.

### Dry run

Scan and print a summary without writing any report files:

```bash
python plex_dupe_scan.py --dry-run
```

### Review generated scripts

Open the most recent purge and torrent cleanup scripts in your editor:

```bash
python plex_dupe_scan.py --review
```

Defaults to `vim`. Use `:n` / `:prev` to move between files. Set a different editor with `"editor": "nano"` (or `"code"`, `"hx"`, etc.) in `config.json`.

### Apply a script remotely

Once you have reviewed a generated script, run it on the server directly from your machine:

1. Open the generated `.sh` file (`--review` is the quickest way).
2. Remove or comment out any lines you do not want to execute.
3. Change `HasBeenChecked=false` to `HasBeenChecked=true`.
4. Run:

```bash
python plex_dupe_scan.py --apply reports/plex_purge_candidates_<timestamp>.sh
```

Or use a shorthand to pick the most recent script of a given type automatically:

```bash
python plex_dupe_scan.py --apply plex     # most recent duplicate purge script
python plex_dupe_scan.py --apply torrent  # most recent torrent cleanup script
python plex_dupe_scan.py --apply junk     # most recent junk file script
```

The script streams output live as it runs, then prints a summary:

```
Script:  plex_purge_candidates_2026-06-03_14-00-00.sh
Targets: 12 entries (47.30 GB)

Connecting to Plex machine...
Running script on server...
── Output ───────────────────────────────────────────
  removed '/home/data/plex/media/movies/Movie.720p.mkv'
  removed '/home/data/plex/media/tv/Show/S01E01.480p.mkv'
  ...
── Summary ──────────────────────────────────────────
  Targets processed:           12 / 12
  Files removed:               12
  Directories removed:         0
  Items moved:                 0
  Space freed (estimated):     47.30 GB

  Directories affected (3):
    /home/data/plex/media/movies
    /home/data/plex/media/tv/Breaking Bad
    /home/data/plex/media/tv/The Office
```

> The `-I` interactive confirmation flag is stripped automatically since you already reviewed the file.
> Space freed is computed from targets that were actually confirmed removed — if any operations failed,
> they will appear in an Errors section and are excluded from the total.

If your SSH user doesn't own the files (e.g. owned by the torrent client), set `"ssh.sudo": true` in config. You'll be prompted for the sudo password before connecting.

### Run scripts manually

As an alternative to `--apply`, copy the script to your server and run it there:

```bash
scp reports/plex_purge_candidates_<timestamp>.sh admin@192.168.1.100:~/
ssh admin@192.168.1.100
bash ~/plex_purge_candidates_<timestamp>.sh
```

## Output

All output files are written to `output_dir` (default: `./reports`). Each scan keeps the **5 most recent** report/script pairs per type and prunes older ones automatically.

### Duplicate scan

| File | Description |
|---|---|
| `plex_duplicate_report_<timestamp>.md` | Grouped table of duplicates with KEEP / PURGE? labels and sizes |
| `plex_purge_candidates_<timestamp>.sh` | Shell script using `rm -Iv` (interactive) |

Files within each duplicate group are ranked by your `prefer` settings. The highest-scoring file is marked KEEP; the rest are marked PURGE?. Review the report before running the script.

### Torrent cleanup (when `torrent_paths` is configured)

| File | Description |
|---|---|
| `plex_torrent_cleanup_<timestamp>.md` | Report listing matched, alternate, and orphan torrent entries |
| `plex_torrent_cleanup_<timestamp>.sh` | Shell script for cleanup |

Torrents are classified into three tiers:

| Tier | Meaning | Script action |
|---|---|---|
| **Exact match** | Torrent name closely matches a Plex file | `rm -rIv` (interactive per item) |
| **Plex has alternate** | Title exists in Plex under a different edition/quality | `rm -rfv` (direct delete, verbose) |
| **True orphan** | No version of this title found in Plex | `mv -v` / `# rm -rfv` pair — keep the action you want, comment out the other |

True orphan entries look like this in the script:

```bash
mv -v -- '/path/to/orphan' '/path/to/staging/'
# rm -rfv -- '/path/to/orphan'
```

Comment out the `mv` line to delete instead, or comment out the `rm` line to move to staging.

> **Note on large moves:** If `torrent_orphan_move_dest` is on a different filesystem than your
> torrent directories, `mv` has to copy every byte before deleting the original. Moving hundreds
> of GBs will be slow. Point the destination to the same filesystem (or the same drive) for
> instant renames.

### Junk file scan (when `scan_junk: true`)

| File | Description |
|---|---|
| `plex_junk_report_<timestamp>.md` | Report listing junk, sample, and extras files |
| `plex_junk_candidates_<timestamp>.sh` | Shell script for cleanup |

Files are classified into three categories:

| Category | What it catches | Notes |
|---|---|---|
| **Junk files** | `.nfo`, `.sfv`, `.txt`, stray images, etc. | Release detritus from torrent downloads |
| **Sample files** | Video files with `sample` in the name | Preview clips bundled with releases |
| **Extras / bonus content** | Files inside `Featurettes/`, `Behind the Scenes/`, `Trailers/`, etc. | Plex Pass users can browse these — only delete if you don't need them |

`keep_local_artwork: true` (the default) preserves `poster.jpg`, `fanart.jpg`, and other Plex local media assets so they aren't flagged as junk.
