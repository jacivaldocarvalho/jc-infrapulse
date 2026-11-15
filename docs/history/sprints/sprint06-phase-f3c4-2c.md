# F3C.4.2C — Virtual OCI inventory

`scripts/oci_image_inventory.py` provides `inventory(layers)` for previously
identified, uncompressed USTAR layers. The caller must independently validate
the manifest, order, and descriptors; the reader checks bytes, hashes, limits,
and syntax before reconstruction. Each remaining explicit entry reports path,
type, size, mode, UID/GID, target, layer, and, for files and hardlinks, SHA-256
and the content source. Output is sorted and contains no file bytes.

Hardlinks use the size, mode, UID/GID, and hash of their source regular file,
even when the original path was subsequently removed or replaced. The hardlink's
layer and target describe the link entry itself. Implicit directories have no
TAR metadata of their own and are not listed. Whiteouts and removed content
are not inventoried. Filenames and targets may still contain sensitive information
and must be handled according to the environment.

Scope is limited to the reader's supported profile; unsupported formats,
ambiguous references, truncation, inconsistencies, and exceeded limits fail.
The caller allocates the bytes before validation. This inventory does not
independently validate the manifest, reconcile packages, or demonstrate
completeness or authenticity. Coverage remains `COVERAGE_PARTIAL`.
