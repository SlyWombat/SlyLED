## Add Fixture — Step 2: Address

Pick the DMX universe and start address where this fixture sits on
your data run. The wizard does live conflict detection so you can spot
overlaps before committing.

### Fields

- **Name** — operator-facing label. Defaults to the profile's name;
  edit freely. The Layout tab, Dashboard, and timeline view all show
  this name.
- **Universe** — 1–4 by default (extendable in Settings → DMX). Each
  universe carries 512 channels and goes out over Art-Net (one
  universe per UDP packet to its bound network).
- **Start Address** — 1–512. The fixture occupies this address plus
  the next `channelCount − 1` slots, so a 13-channel mover at
  address 17 occupies 17–29.
- **Channels** — only shown for **Custom Fixture** (no library
  profile). Library profiles fix the channel count from their
  metadata.

### Live conflict detection

While you type, the wizard fetches the universe's current patch and
shows one of three states:

- **No conflicts at U1 @17-29** — green; safe to proceed.
- **Conflict: overlaps with Front Par 2** — red; pick a different
  start address or change the conflicting fixture first.
- **Error: channels extend past 512** — red; you ran off the end of
  the universe. Move to the next universe or pick an earlier start.

### Tips

- Address numbering is **1-based** on the wire. Some DMX consoles
  display 0-based addresses — match what's on the fixture's DIP
  switches or LCD, not the console.
- For a quick free-slot probe, click **Browse All** in step 1 → the
  next conflict-free slot for your chosen profile gets pre-filled
  here.

**More info →** chapter 4, *Fixture Setup*.
