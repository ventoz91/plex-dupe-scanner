# plex-dupe-scanner

Scans your Plex media library over SSH, groups duplicate files by title, and optionally cleans up your torrent download directories. Generates a human-readable Markdown report and a ready-to-run shell script for each task.

## Requirements

- Python 3.10+
- SSH access to your Plex server (key-based or password)
- `python3` available on the Plex server (standard on most Linux installs)

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
| `media_paths` | ✓ | List of directories on the server to scan for media files |
| `extensions` | ✓ | File extensions to include in the scan |
| `output_dir` | | Local directory for generated reports (default: `./reports`) |
| `prefer.resolution_order` | | Ranked list of resolutions — first is most preferred |
| `prefer.codec_order` | | Ranked list of codecs — first is most preferred |
| `prefer.prefer_larger_file` | | Add file size to the score when comparing duplicates (default: `true`) |
| `torrent_paths` | | List of torrent download directories on the server — omit to skip torrent cleanup |
| `torrent_orphan_move_dest` | | Destination path used in the `mv` line for orphan torrents |

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

## Output

All output files are written to `output_dir` (default: `./reports`).

### Duplicate scan

| File | Description |
|---|---|
| `plex_duplicate_report_<timestamp>.md` | Grouped table of duplicates with KEEP / PURGE? labels and file sizes |
| `plex_purge_candidates_<timestamp>.sh` | Shell script using `rm -Iv` (interactive) — run on the Plex server |

Files within each duplicate group are ranked by your `prefer` settings. The highest-scoring file is marked KEEP; the rest are marked PURGE?. Review the report before running the script.

### Torrent cleanup (when `torrent_paths` is configured)

| File | Description |
|---|---|
| `plex_torrent_cleanup_<timestamp>.md` | Report listing matched and orphan torrent entries |
| `plex_torrent_cleanup_<timestamp>.sh` | Shell script for cleanup — run on the Plex server |

**Matched** — torrent has a corresponding entry in your Plex library. Uses `rm -rIv` (interactive per item).

**Orphans** — no Plex match found. Each entry has two lines; comment out the one you don't want before running:

```bash
mv -v -- '/path/to/orphan' '/path/to/staging/'
# rm -rf -- '/path/to/orphan'
```

### Running the scripts

Copy the `.sh` file to your Plex server and run it there, or paste its contents into an active SSH session:

```bash
scp reports/plex_purge_candidates_<timestamp>.sh admin@192.168.1.100:~/
ssh admin@192.168.1.100
bash ~/plex_purge_candidates_<timestamp>.sh
```
