# TODO

## Backlog

- [ ] **Broad video types not duplicate-scanned**: Files in `_BROAD_VIDEO_EXTS`
      (`.ts`, `.iso`, `.vob`, etc.) are correctly excluded from the junk report
      now, but they're still invisible to the duplicate scanner because duplicate
      detection only looks at `extensions` from config. If you have a `.ts`
      episode and a `.mkv` episode of the same show, the dupe scanner won't catch
      it. Fix options:
      - Recommend adding `.ts`, `.iso`, `.vob` to the user's `extensions` config
        (simplest — they'd then also show up in dupe detection)
      - Or merge `_BROAD_VIDEO_EXTS` into the media scan automatically

- [ ] **Symlink safety**: The media scan and junk scan follow symlinks without
      checking. If Plex is set up with symlinks (some Docker/NAS setups do this),
      the tool could report symlinked files as duplicates or flag them as junk.
      Add `os.path.islink()` checks in remote scans to skip symlinks or at least
      tag them differently in the report.

- [ ] **Scan progress feedback**: Remote scans give no output while running —
      the terminal just sits there for potentially minutes. Even a simple count
      printed after the scan completes ("scanned 4,821 files in 12s") would help.
      Harder: stream per-directory progress, but that would require restructuring
      the remote one-liner.

- [ ] **Large item move efficiency**: `mv` across filesystems copies byte-for-byte
      before deleting, so moving hundreds of GBs to a staging directory on a
      different drive takes a very long time with no per-file progress output
      (`mv -v` only prints after the operation completes). Options to explore:
      - Warn in the report when `torrent_orphan_move_dest` appears to be on a
        different filesystem than the source paths
      - Consider using `rsync --remove-source-files` instead of `mv` for
        cross-filesystem moves (gives progress, resumable)

- [ ] **`--review` should open junk script**: Currently only opens the purge and
      torrent cleanup scripts. Should also open the junk script when `scan_junk`
      is enabled.

- [ ] **Total recoverable summary**: Each scan type prints its own space figure
      separately. A single "total recoverable across all scans: X GB" line at the
      end of a full run would be a nice quality-of-life addition.
