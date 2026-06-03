# TODO

## Backlog

- [ ] **Large item move efficiency**: `mv` across filesystems copies byte-for-byte
      before deleting, so moving hundreds of GBs to a staging directory on a
      different drive takes a very long time with no per-file progress output
      (`mv -v` only prints after the operation completes). Options to explore:
      - Warn in the report when `torrent_orphan_move_dest` appears to be on a
        different filesystem than the source paths
      - Show a note in `--apply` output when a `mv` is taking a long time
      - Consider using `rsync --remove-source-files` instead of `mv` for
        cross-filesystem moves (gives progress, resumable)

- [ ] `--review` should also open the junk report when `scan_junk: true`
      (currently only opens purge + torrent scripts)

- [ ] Consider a `--scan-only` flag that runs the scan and prints reports
      without writing scripts (different from dry-run which skips reports entirely)
