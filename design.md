# Design system — Intramural Scheduler

The record of *why* the interface looks the way it does. Read this before
changing anything visual; most of these are constraints with a reason behind
them, not preferences.

Palette, type scale and motion curve are **unchanged** from the previous
revision — the restructure is layout and material, not colour.

---

## 1. Colour — Onyx + Candy Blue

Locked. Do not introduce a new hue without a reason recorded here.

### Onyx ramp (surfaces, darkest → lightest)

| Token | Hex | Used for |
|---|---|---|
| `--onyx-900` | `#0b0d10` | page ground |
| `--onyx-800` | `#101317` | panels |
| `--onyx-700` | `#161a20` | raised rows, inputs' resting state |
| `--onyx-600` | `#1c212a` | inputs, hover fills |
| `--onyx-500` | `#232936` | pressed / selected fills |
| `--line` | `#2b3240` | borders |
| `--line-soft` | `#212734` | internal dividers |

### Candy Blue (the single accent)

| Token | Hex |
|---|---|
| `--candy-400` | `#63c9ff` |
| `--candy-500` | `#2bb3ff` |
| `--candy-600` | `#0d98e8` |
| `--candy-700` | `#0a7ab9` |

### The one rule that matters

**Candy Blue is spent on one thing per visual field.** The primary action, the
current nav item, today's date, the selected match, a pending role handover.
The interface is otherwise monochrome onyx and grey.

This is the Von Restorff effect doing real work: an accent that appears
everywhere stops meaning anything. Add a second blue element beside the first
and you have taken meaning away from both.

**Amended after building the Admin view.** The rule was originally written as
"one per view", which is right for a view with a single job — Book has exactly
one primary action, and so does Roster. Admin is different: it carries four
independent tools (master sheet, terms, role handover, history), and forcing a
single accent across all of them would either leave three panels with no
emphasis at all, or promote one arbitrary action over equally important ones.

So the unit is the **panel**, not the view:

- At most one `.btn-primary` per panel. Never two in the same panel.
- A view with one job has one panel that matters, so this still reads as one
  accent per screen.
- A multi-tool view may show one per panel, because panels are separated
  enough to be their own visual fields.

Audit before shipping a view: count `.btn-primary` inside each panel. More than
one is a bug.

### Status colours (reserved)

These are **never** reused as decoration or as "another series colour". Each
one is a specific answer to "what do I do about this?", and each ships with a
label, never colour alone.

| Token | Hex | Means | The fix it implies |
|---|---|---|---|
| `--ok` | `#3ddc97` | confirmed, healthy | nothing |
| `--venue` | `#ff6b6b` | venue double-booked | move the room or the time |
| `--player` | `#ffb454` | player double-booked | pick another player |
| `--blackout` | `#b79cff` | not enough rest | push the match later |

The blackout violet is deliberately *not* red or amber. A blackout is not a
clash — nobody is double-booked, the player simply has not recovered yet. It
demands a different fix, so it gets a different colour.

### Text tokens

`--text` `#e9edf4` · `--text-dim` `#9aa6b8` · `--text-faint` `#6b7687`

Values, labels and legends always wear text tokens. A coloured mark next to
them carries the identity; the text itself never takes the status colour.

---

## 2. Material — liquid glass

The nav bar and primary buttons are glass: the backdrop is blurred **and
refracted**, the way real glass bends what is behind it.

```
feTurbulence (fractalNoise)  →  a noise field
      ↓
feGaussianBlur               →  soften it, so the distortion rolls instead of fizzing
      ↓
feDisplacementMap            →  shove each backdrop pixel sideways by that noise
      ↓
backdrop-filter: url(#…) blur(…)
```

Two variants, because the scale of the ripple has to match the size of the
element:

| Filter | `baseFrequency` | `scale` | On |
|---|---|---|---|
| `#glass-nav` | `0.006 0.010` | 16 | the nav bar — long, shallow ripple |
| `#glass-btn` | `0.014 0.020` | 9 | buttons — tighter, smaller ripple |

**The lit edge is not optional.** Glass reads as glass because its top edge
catches light:

```css
box-shadow: inset 0 1px 0 rgba(255,255,255,0.38);
```

One pixel, white, inset, top only. Without it the effect looks like fog.

### Verified, not assumed

`backdrop-filter: url(#filter)` is **not** universally supported — for years
Chrome parsed it and silently dropped it. It was confirmed rendering in
Chrome 154 before this was built, by putting two panels over a striped
background and checking the stripes actually bend.

`@supports` gates it. Where the SVG filter does not apply, the element falls
back to plain `backdrop-filter: blur()`, which every current browser has. The
UI is never *dependent* on refraction.

---

## 3. Navigation — the gliding pill

The nav indicator is a single pill that slides to whichever link is hovered,
with **no JavaScript**.

```css
nav a:hover      { anchor-name: --nav-pill; }   /* hovered link claims the name */
.nav-pill        { position-anchor: --nav-pill;
                   left:   anchor(left);         /* edges follow the anchor */
                   right:  anchor(right);
                   transition: left .38s var(--spring), right .38s var(--spring); }
```

Because the pill's edges are ordinary `left`/`right` values, the jump from one
anchor to the next is just a value change — and a plain CSS transition turns
that jump into a glide. Nothing measures anything.

**Do not use the `inset` shorthand here.** `anchor()` inside `inset` is not
supported in Chrome 154 (checked with `CSS.supports`); only the longhands
resolve. Writing `inset: anchor(...)` silently produces no positioning at all.

The *selected* item is marked separately by `aria-current`, because hover is
transient and selection is not. Hover moves the pill; selection colours the
label.

---

## 4. Layout

A top nav with views, not a sidebar of tabs. The schedule is what most people
come for, so the dashboard leads with it in summary and the calendar is one
click away.

| View | Who sees it | Job |
|---|---|---|
| Dashboard | everyone | what is the state of things right now |
| Schedule | everyone | the month calendar, public |
| Book | HEAD / REP | create one match |
| Roster | HEAD / REP | players and teams |
| Generate | HEAD / REP | batch fixtures |
| Admin | HEAD only | master sheet, terms, role handover |

Views are gated by role, and the nav only renders what the signed-in user can
actually reach — an item you cannot use is an item you should not have to read
(Hick's Law).

### Spacing is the only grouping signal

| Gap | Between |
|---|---|
| 6px | a label and its input |
| 14px | fields inside one group |
| 26px + rule | one group and the next |

Do not add boxes to show grouping. The spacing already says it (Law of
Proximity); a border on top of it is noise.

### Touch targets

`--tap: 44px` is the floor for anything interactive (Fitts's Law). Primary
buttons are full-width and sit directly beneath the last field they submit. A
draft's Publish and Cancel live inside that draft's own card so the pointer
barely travels.

---

## 5. Dashboard tiles

Stat tiles, not charts — a single number answering a single question is a
**hero number**, and wrapping it in a chart adds ink without adding meaning.

Rules they follow:

- The number wears `--text`; its label wears `--text-dim`. Neither takes a
  status colour.
- A status dot may sit beside the number, and always with a word next to it.
- Tiles never animate their value on load. A number that counts up is a number
  you cannot read yet.

The one plotted element is the **7-day load strip**: a single-hue bar per day
of the coming week, so a crowded day is visible before anyone books into it.
Single series, so no legend — the heading names it. Bars carry 4px rounded
ends anchored to the baseline, a 2px gap, and a per-bar tooltip.

---

## 6. Motion

One spring curve, everywhere, so every interaction feels like the same product.

```css
--spring: cubic-bezier(0.34, 1.56, 0.64, 1);   /* release: overshoots */
--ease:   cubic-bezier(0.4, 0, 0.2, 1);        /* everything else */
```

Press compresses **fast and linearly** — it must feel immediate. Release
springs back with an overshoot. Success pops once, then stops.

All of it collapses under `prefers-reduced-motion: reduce`. The motion is
decoration and never carries information, so removing it costs nothing.

---

## 7. Conventions to keep

- `[hidden] { display: none !important; }` stays. Any author rule that sets
  `display` on a class otherwise beats the UA `[hidden]` rule, and elements
  toggled with `el.hidden` silently keep rendering. This has already caused one
  bug (a stray empty chip in the app bar).
- Toasts must never cover the primary action.
- Times shown to a person are formatted in *their* locale and zone; the API
  speaks canonical UTC. Never print a raw ISO string at a user.
- **Anything sent to the API carries an explicit offset.** A `datetime-local`
  input returns a bare `2026-10-08T23:30` with no zone, and the API reads a
  naive timestamp as UTC. Since the page *displays* local time, an organizer in
  +05:30 who typed the time they saw on screen had it stored five and a half
  hours away — and it silently failed to collide with the match already in that
  slot. `toInstant()` stamps the browser's offset on every value before it
  leaves. A missed conflict that looks like a clean booking is the worst thing
  this system can produce; the frontend must not manufacture one.
- Conflict messages say what to **do**, not what happened.
