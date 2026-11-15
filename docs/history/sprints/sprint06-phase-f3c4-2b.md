# F3C.4.2B — Virtual OCI reconstruction

`reconstruct_rootfs` applies validated, uncompressed USTAR layers in memory,
without extracting or resolving symlinks in the operating system. The caller must
independently validate the manifest, descriptors, and layer order; the reader's
limits and digest checks remain mandatory.

Ordinary whiteouts and opaque directories remove only entries from lower layers
before entries in the current layer are processed, regardless of TAR order.
Implicit directories are allowed; traversing a file or symlink as a directory fails.
Hardlinks target only regular files already available, including earlier hardlinks,
and preserve their identity after replacements. Forward references, cycles,
escapes, and unsupported types are rejected. Output is sorted by path and records
the layer and source path of the content; it neither exposes contents nor
constitutes a file inventory.

This stage does not prove authenticity of the manifest or builder, or completeness
of the SBOM. Coverage remains `COVERAGE_PARTIAL`. Inventory, reconciliation, and
operational integration remain for later stages.
