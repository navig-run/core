- **`navig cabinet` — an encrypted cabinet for important files (new plugin `navig-cabinet`,
  extra `navig[cabinet]`).** ID and passport scans, medical records, contracts, photos, voice notes
  and videos: any type, any size. Items are chunked AES-256-GCM streams, so memory stays bounded
  and reordering, truncation or splicing is detected. Title, filename, tags, expiry, notes and OCR
  text are all encrypted, so the disk shows only counts and sizes. Text is read locally only, with
  no cloud OCR and no plaintext cache. `search` matches words inside scans, and `expiring` lists
  passports and cards that are due. The key is the machine key by default, with an optional
  passphrase; a wrong passphrase never falls back to the machine key. `backup` writes a portable
  `.ncab` that restores anywhere, and a standalone recovery script needs only `cryptography`.
  `import-paperwork` encrypts the ID and medical documents a `navig paperwork` scan set aside.
  The text-encoding guard no longer flags `tarfile.open(..., mode="w|")`: a tar has no text mode.
