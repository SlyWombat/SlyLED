## DMX Monitor

The **DMX Monitor** is a live 512-channel grid for the selected
universe. Every cell is one channel; the value renders as both a
number (0–255) and a colour intensity. Auto-refresh keeps the view
within ~250 ms of the live bake output.

### Reading the grid

- **Rows** = address ranges of 32 channels each. The leftmost column
  is the start address for that row.
- **Columns** = the offset within the row (1–32). So `row 17, col 5`
  is address `17 + 5 - 1 = 21`.
- Cell colour scales with value — brighter = higher. Cells > 128 swap
  the foreground to dark for readability.
- The monitor reflects the **post-bake DMX universe buffer** — what
  Art-Net is about to broadcast. Master scaling, Auto Brightness,
  and per-fixture brightness are all already applied.

### Click-to-set

Click any cell to set its value manually. Useful when you want to
prove a channel mapping (toggle channel 5 to 255, watch the fixture
respond, confirm channel 5 = dimmer) without authoring a temporary
action.

> Manual sets are overwritten by the next bake tick, so this is a
> live test harness — not a way to drive a show. Use the Group
> Control modal or a saved action for sustained output.

### Universe selector

Switch between universes 1–4 with the dropdown. Each universe is its
own 512-channel buffer; they aren't aliased. If the universe you want
isn't listed, raise the universe count in **Settings → DMX**.

### Auto-refresh

The checkbox toggles a 250 ms polling loop. Turn it off when you're
stepping through values by hand and don't want them to flicker
underneath you.

**More info →** chapter 12, *DMX Fixture Profiles*; chapter 17,
*Troubleshooting* — for the "no channels light up" recipe.
