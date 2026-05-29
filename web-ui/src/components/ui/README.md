# Design system primitives

Tokyo Minimal — restrained, Japanese editorial, hairline rules, whisper-quiet.
These primitives formalise the patterns that already exist across the gold-
standard pages (`SchedulesPage`, `ScheduleEditorDialog`, `StreamingLogViewer`)
so the rest of the app stops reinventing them.

If you're tempted to roll your own page header / filter bar / status pill —
**don't**. Use these. If you find a case they can't handle, extend the
primitive instead of forking it.

## Type scale

Defined in `src/index.css`. Use these classes — don't introduce inline
`text-[Npx]` overrides.

| Class          | Size  | Weight | Letter-spacing | Use for                                          |
|----------------|-------|--------|----------------|--------------------------------------------------|
| `text-display` | 16px  | 500    | 0.02em         | Page H1 (e.g. "Schedules", "Activity")           |
| `text-title`   | 14px  | 500    | 0.02em         | Section headings, prominent text                 |
| `text-body`    | 13px  | 300    | normal         | Body text, descriptions, input                   |
| `text-caption` | 11px  | 300    | 0.03em         | Labels, metadata, button text                    |
| `text-mono`    | 11px  | normal | -0.01em        | Monospaced data (timestamps, IDs, code)          |
| `text-micro`   | 10px  | 400    | 0.15em         | Uppercase chip labels, count badges, eyebrow ALL-CAPS |

20px (`offline-overlay__title`) is a one-off and intentionally excluded.
There is no `text-XL` / `text-huge` — if a heading wants to be larger, it
probably wants different IA instead.

## Colors

All colors live in `src/index.css` as CSS custom properties. Reach for the
tokens — never paste a `#hex` literal or an `rgba(199,62,58,…)` triplet.

| Token                    | Value      | Use                                      |
|--------------------------|------------|------------------------------------------|
| `--color-void`           | `#0c0c0c`  | Page background (dark mode)              |
| `--color-ink`            | `#171717`  | Card / surface background                |
| `--color-paper`          | `#fafaf9`  | Primary foreground                       |
| `--color-stone`          | `#b0b0b0`  | Secondary text, borders                  |
| `--color-mist`           | `#e5e5e5`  | Tertiary text (light mode override)      |
| `--color-vermillion`     | `#c73e3a`  | Errors, destructive actions              |
| `--color-vermillion-rgb` | `199,62,58`| For `rgb(var(--color-vermillion-rgb)/X)` |
| `--color-indigo`         | `#3d5a80`  | Active state, focused field              |
| `--color-orchid`         | `#a855f7`  | Review status                            |
| `--color-sky`            | `#38bdf8`  | Running status, info                     |
| `--color-harvest`        | `#f59e0b`  | Recovering status, warning               |
| `--color-jade`           | `#10b981`  | Completed status, success                |

For alpha compositing prefer `bg-[var(--color-X)]/[0.08]` (Tailwind v4
arbitrary opacity) over hand-rolled `rgba()`. The vermillion-rgb token
exists for the legacy sites that already used the RGB-triplet form.

---

## `<PageHeader>`

The top of every secondary page. Hairline `border-b`, `px-4 sm:px-6 py-3`.

```tsx
<PageHeader
  title="Schedules"
  icon={CalendarClock}
  count={sorted.length}
  countLabel="schedule"
  actions={<button className="…">New schedule</button>}
/>
```

**Don't:** roll a bespoke header `<div className="border-b …">…</div>` block.
**Do:** use this. If you need a subtitle, pass `subtitle="…"`. If you need
more structure, push back — pages have one H1.

## `<FilterBar>`

The row directly under `<PageHeader>` on list pages.

```tsx
<FilterBar
  filters={
    <select className="text-caption …">
      <option>All statuses</option>
      …
    </select>
  }
  search={{ value: q, onChange: setQ, placeholder: 'Search runs…' }}
  refresh={load}
  refreshing={loading}
  actions={<button>New</button>}
/>
```

The refresh button is `aria-label="Refresh"` — **not** `title="Refresh"`.
Screen readers don't read `title` reliably; we use ARIA for affordance,
hovered `title` only for redundant supplementary detail.

## `<StatusDot>`

Renders the `.mark mark-{state}` glyph from `index.css`.

```tsx
<StatusDot state="running" />
<StatusDot state="completed" label="Completed" />
<StatusDot state="recovering" size="lg" />
```

**Don't:** invent another `<span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />`.
**Do:** use this. If you need a new state, add `.mark-<state>` to `index.css`
first, then add it to the `StatusState` union — keep the system as the
source of truth.

## `<SaveableSetting>`

Card chrome around a single labelled control with optional inline Save +
state pill (Saving… / Saved, auto-dismiss 2s).

```tsx
<SaveableSetting
  label="API token"
  description="Used for outbound webhooks."
  dirty={value !== savedValue}
  saving={saving}
  saved={justSaved}
  onSave={save}
>
  <input value={value} onChange={(e) => setValue(e.target.value)} />
</SaveableSetting>
```

**Don't:** scatter four different save patterns across Settings, Workspace,
and Schedule editors. **Do:** use this one. The `onSave` slot is optional —
omit it for autosave-style controls (the `saving` / `saved` pill still works
for status feedback).

## `<DataPage>` / `<DataPage.Body>`

Composition shell for list-style pages. Just enforces rhythm — no logic.

```tsx
<DataPage>
  <PageHeader title="Schedules" icon={CalendarClock} count={n} countLabel="schedule" />
  <FilterBar filters={…} refresh={load} />
  <DataPage.Body>
    <table>…</table>
  </DataPage.Body>
</DataPage>
```

`DataPage` is a `flex-1 flex flex-col overflow-hidden min-h-0` container;
`DataPage.Body` is the scrolling region. This is what every list page should
look like; if yours doesn't, that's drift.

---

## Adding a new primitive

1. Find at least **two** places in the app that implement the pattern.
   If there's only one, you don't have a primitive — you have a component.
2. Lift the implementation, then narrow the API to what those callers
   actually need. Don't pre-emptively add props.
3. Token-only colors, named type-scale classes, hairline rules
   (`border-stone/10` or `rgba(163,163,163,0.1)`), `rounded-sm` corners.
4. Add a section to this README with: anatomy, example, "don't" footnote.
5. Migrate at least one caller as the canonical example so the next person
   sees the pattern in use.
