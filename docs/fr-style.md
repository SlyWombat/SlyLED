# SlyLED — French translation style guide

This file normalises translator output (human or VLM-driven via
`tools/docs/translate.py`) so `docs/src/fr/` reads like a single
voice, not fourteen drive-by contributions.

## Voice

- **Formal "vous" throughout.** SlyLED docs address an operator, not
  a peer. Reserve "tu" for the eventual Discord / community channel,
  not the manual.
- **Active voice.** "Le moteur calibre le projecteur" beats "Le
  projecteur est calibré par le moteur" every time.
- **Direct imperatives for procedures.** "Cliquez sur Calibrer",
  not "Vous pouvez cliquer sur Calibrer si vous le souhaitez".
- **Match the EN register.** The SlyLED manual is not academic French;
  it is documentation French. Short declarative sentences, no
  rhetorical flourishes, no *néanmoins* where *mais* will do.

## Technical vocabulary

Borrowed English terms (used unchanged, lower-case unless they open a
sentence): **DMX**, **Art-Net**, **sACN**, **ArUco**, **YOLO**,
**ONNX**, **UDP**, **HTTP**, **JSON**, **YAML**, **WiFi**, **OTA**,
**BeamLight**, **gobo**, **strobe**, **firmware**, **open source**.

Native French terms (use these — do not fall back to English):

| EN | FR |
|----|-----|
| Moving head / mover | Projecteur motorisé |
| Fixture | Appareil |
| Scene | Scène |
| Stage (physical) | Scène (physique) |
| Stage (SlyLED 3D model) | Plateau |
| Bake | Précalculer (verb); précalcul (noun) |
| Cue | Déclencheur (cue list → liste de déclencheurs) |
| Runtime | Exécution |
| Orchestrator | Orchestrateur |
| Performer (LED node) | Nœud exécutant |
| Child (board role) | Nœud enfant |
| Parent (board role) | Nœud parent |
| Calibration | Étalonnage |
| Tracking | Suivi |
| Layout | Disposition |
| Timeline | Chronologie |
| Track (on timeline) | Piste |
| Preset | Préréglage |
| Show | Spectacle |
| Dashboard | Tableau de bord |
| Settings | Paramètres |
| Firmware | Micrologiciel (in body text); **firmware** (as a heading / term of art) |

## Numbers and units

- **Decimal separator: comma.** `3,14 mm`, not `3.14 mm`.
- **Thousands separator: narrow no-break space** (U+202F) or plain
  space. `10 000 octets`, not `10,000 bytes`.
- **Temperature, length, time.** Always metric. SlyLED ships no
  imperial units.
- **Percentages.** Keep the `%` sign attached: `80 %`, with a
  non-breaking space before it (U+00A0).

## Typography

- **Non-breaking space before `:` `;` `!` `?` `%` `»` «**.
- **Quotation marks.** Use French guillemets «  like this  » at the
  top level; switch to "fancy double quotes" when nested.
- **Em-dash.** Retain the EN author's rhythm: when the EN copy uses
  `—` the FR copy uses `—`. Do not soften to commas.
- **Code blocks / inline code.** Never translate the code content.
  Comments inside code blocks stay in English, even in FR sections,
  so diff tools pair up.
- **File paths, env vars, shell commands.** Never translate.

## Markdown structure

The translator **must preserve** every EN structural feature:

- Same heading levels (`##`, `###`), same anchor slugs (Starlight
  computes anchors from heading text — if you rename the heading,
  update the cross-ref in the same diff).
- Same number of code blocks, in the same positions.
- Same number of list items per list.
- Same image references (`![alt](path)`) — translate the alt text
  only; preserve the path verbatim.
- Same link URLs. Translate the link text; keep the href.

## Glossary terms

Terms defined in `schema/glossary.yml` must use the `<abbr>` pattern
so the SPA hover layer (#671) can wire tooltips. Example:

> Le flux <abbr data-term="dmx">DMX</abbr> passe par le pont.

Do not redefine glossary terms in-line; link readers to
`20-glossary.md` instead.

## Review workflow

Every FR section carries a sentinel at the top while it is awaiting
review:

```
<!-- review-status: pending -->
```

After a human reviewer confirms the translation, replace the sentinel
with:

```
<!-- review-status: reviewed YYYY-MM-DD reviewer-name -->
```

`tools/docs/drift_check.py --mode parity` fails the build if an EN
section changes after a matching FR file was marked reviewed. The FR
reviewer re-examines and re-stamps (or re-translates) to unblock.

## Common fixes

- **Don't borrow "performer" directly.** Say **nœud exécutant** — the
  engineering meaning (LED execution node) is not the theatre meaning
  (a human performer).
- **Don't machine-translate "show".** `spectacle` carries the right
  sense; `émission` and `exposition` do not.
- **Don't translate "Kinetic-Prism".** It is the product theme name.
- **Don't translate "SlyLED".** Product name.

## Scope

This guide governs the content under `docs/src/fr/` and
`docs/src/marketing/` when French versions land there. It does not
apply to:

- Code identifiers, variable names, commit messages (all English).
- Inline SPA labels rendered from `spa/js/` — those have their own
  i18n plumbing (see `_i18n()` in `app.js`).
- French community-provided fixture profile names.
