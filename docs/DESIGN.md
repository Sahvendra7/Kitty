# Kitty's design system

The tokens live in `app/ui/kitty.css`. Every page links that file, then adds only its own layout. This page says why they are what they are.

## The idea

A kitty is two things: the pot of money a group shares, and a cat. The product already had a strong structural idea, three rooms that differ by who can see them, and a chit, the slip of paper that travels between them. The design keeps those and adds the cat. Everything else stays quiet so that the figures read first.

**One thing gets to be loud: the cat.** Colour, type and layout are disciplined around her. If a screen has a choice between a decoration and a clearer figure, the figure wins.

## Colour means something

| Token | Hex | Means |
|---|---|---|
| `--monsoon` | `#0E3B43` | Just you. The private room and the rail around it |
| `--glass` | `#E4F3EF` | Your desk |
| `--marigold` | `#F5A81C` | The group, and the main action |
| `--paper` | `#F8F2EA` | The page |
| `--surface` | `#FFFCF8` | A chit |
| `--ink` | `#14282C` | Text |
| `--good` | `#1B7355` | Money someone has confirmed. Only then |
| `--wait` | `#8F5A00` | Not counted yet |
| `--bad` | `#B8124F` | Didn't arrive, or something's stuck |
| `--fur` | `#EC8428` | The cat, and nothing else |

The status colours never appear alone. Every status also says what it is in words, so a confirmed payment and a possible match can't be confused in greyscale. A possible match gets a dashed outline, and a confirmed payment a solid fill.

Dark mode comes from the same tokens, through `prefers-color-scheme`.

## Type

**Anek Latin** by Ek Type, an Indian foundry, under the SIL Open Font License. One family, doing two jobs through its width axis: wide and heavy for headings and figures, normal for reading. It has real tabular figures and a rupee sign. It's self-hosted in `app/ui/fonts/`, which also means a private app no longer tells a font CDN that it was opened.

Baloo 2, the previous display face, reads as children's at large sizes.

## Shape

- A **chit** has a torn top edge. Only chits get it, because only chits travel.
- The trip list is **one list on the page**, not a card per trip.
- Radii differ by role: 8px on fields, 12px on buttons, 20px on the panel, 999px on status pills.

## The cat

Original artwork, drawn for Kitty as inline SVG in `app/ui/cat.js`. She's a ginger desi street cat with a nick in her right ear, a monsoon-teal collar and a rupee coin for a tag. There's one drawing, and moods are CSS on the same shapes, so she can't drift off-model.

| Mood | When |
|---|---|
| `idle` | Nothing's needed. Blinks now and then |
| `greeting` | Once per screen per session |
| `curious` | Someone has a question about a payment, or a choice is open |
| `thinking` | Set-up is running, or an agent is working on what you said |
| `guiding` | Pointing out what to do next |
| `reviewing` | A treasurer has payments to check. Magnifying glass |
| `filing` | A member has a chit to release. Small receipt |
| `waiting` | A claim is with someone else to confirm |
| `warning` | Something's stuck or was refused. Ears back |
| `paid` | A confirmation just arrived. Holds up a coin |
| `reconciled` | The figures agree. Stamped chit |
| `celebrating` | A trip just became square. Marigold petals |
| `sleeping` | Two quiet minutes on a calm page |

**Rules she's held to.** A mood comes only from data the page already has. A moment only plays for a change seen while the page is open, never for what was already true when it loaded. She has no network access, and `tests/test_ui.py` checks that: she can explain and point, and she can't press anything. She doesn't speak for your agent, and she says so.

## Motion

CSS only, with no animation library. Most things hold still.

- Loops run only while their state is real: the thinking dots, the blink.
- Everything else plays a few times and stops.
- Animations pause when the tab is hidden.
- Under `prefers-reduced-motion` nothing moves, and she still shows the right mood. No petals.

## Where she goes

| Width | Where the cat sits | Where the panel opens |
|---|---|---|
| 1280px and up | In the page's margin. On the person app, in the rail | In its own column. The page moves over |
| 1024 to 1279px | In the header | In its own column. The page moves over |
| Under 1024px | In the header, on top of nothing | A sheet from the bottom |

She's never allowed to sit on a button. `scripts/ui_check.py` measures this.

## Checking the interface

```bash
uv run --with playwright==1.58.0 python scripts/ui_check.py OUT_DIR --axe path/to/axe.min.js
```

It drives 37 page states at 375, 768 and 1440px from fixtures, without the stack, and fails on a page wider than its screen, a console error, or a serious accessibility finding. It also checks keyboard use, focus, moods, reduced motion, and that asking the cat sends nothing. Those figures exist only in that script. The stills in `docs/assets/` come from real runs.

## Dependencies

No runtime packages were added, and there's no build step.

| Asset | Licence | How it's used |
|---|---|---|
| Anek Latin (Ek Type) | SIL OFL 1.1, `app/ui/fonts/OFL.txt` | Two font files, in the repo |
| Lucide icons | ISC | 17 icons copied into `app/ui/icons.js` |
| Playwright | Apache-2.0 | Dev only, through `uv run --with`. Not in `pyproject.toml` |
| axe-core | MPL-2.0 | Dev only. Passed as a path, not committed |
