## Community Profiles

The **Community** sub-panel lets you search the user-contributed
fixture-profile catalogue, preview a profile's channel layout, and
import it into your local library with one click.

### Searching

- Type at least two characters; the search hits both name and
  manufacturer fields, case-insensitive.
- Results show **Manufacturer · Name · channel count · short
  description** for each hit, plus a **Community** badge so you can
  tell them apart from Local and OFL hits on the unified search panel.
- The catalogue is fetched on demand — first search after launching
  the orchestrator may take a few seconds while the cache warms.

### Sharing

Click **Share My Profiles** to upload your local custom profiles to
the community catalogue. The upload step strips identifying metadata
(orchestrator host, file path, user name) and assigns a slug like
`my-custom-mover-3ch`. The slug is the only handle other operators
need to import your profile.

### Deduplication

When you import a profile that already exists locally (same `id`),
the importer:

1. Compares the channel definitions byte-for-byte.
2. If identical, no-ops and reports "already imported".
3. If different, prompts: **Keep local**, **Overwrite with
   community**, or **Save side-by-side** (appends `-community` to
   the slug).

Side-by-side is safe by default — your local edits aren't clobbered.

### Pitfalls

- Community profiles are crowd-sourced and untested by your rig. For
  movers, run the calibration wizard after import; for hybrid
  RGB+wheel fixtures, double-check the wheel slot map matches your
  hardware's gel sequence.
- A failed download (network, GitHub rate limit) leaves no local
  state — re-trying is safe.

**More info →** chapter 12, *DMX Fixture Profiles*.
