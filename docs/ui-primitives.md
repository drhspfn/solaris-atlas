# Shared UI primitives

The maintained Next.js application owns these components in
`packages/web_new/src/components/ui`. They use the existing Solaris Atlas tokens
and native input semantics; no second theme or UI framework is introduced.

## Select

Existing `value`, `onChange`, `options`, `label`, `disabled` and `placeholder`
remain supported. `width="compact"`, `width="auto"` and `width="full"`
control the shared trigger geometry; `popupMaxWidth` bounds its menu independently.
These props apply to any screen, not just model animations. `searchable` enables search inside the popup. `categorized`
enables category/subcategory drilldown for options with `path` metadata;
`rememberPosition` preserves the current branch and scroll position while the
component stays mounted. Flat options remain valid. Nested categories are also
accepted through `categories` (label, options and recursive children).

```tsx
<Select label="Animation" value={animation} onChange={setAnimation}
  searchable categorized rememberPosition
  options={[
    { value: 'Stand1', label: 'Stand1', path: ['Idle & poses', 'Standing'] },
    { value: 'Run', label: 'Run', path: ['Movement', 'Running'] },
  ]} />
```

The compact popup header keeps Back, the current category and a search icon on
one row. Search opens only from that icon and covers the complete local catalog. Category navigation and typing never
fetch additional data. External controlled-value changes, including previous/next
animation buttons, reveal the selected option's branch. Escape dismisses the
popup; keyboard navigation and selection retain focus and listbox semantics.
Memory belongs to the mounted selector rather than global site preferences.

## Controls

`Button` supports native button props, icon placement through `icon` and
`iconPosition`, icon-only presentation, variants, sizes and `loading` state.
Icon-only buttons must receive an accessible label. Loading prevents repeated
activation and exposes `aria-busy` while retaining the control's footprint.

`Input`, `Checkbox` and `Slider` retain native props and ref access. Checkbox and
Slider accept an optional visible label. Callers own domain values, validation
and persistence. `Dropdown` reuses the existing ActionMenu dismissal/focus behavior
and allows a custom trigger rather than creating another popup implementation.

The character-model viewer is the first integrated consumer. Existing screens
can migrate incrementally without a repository-wide visual rewrite.


## Tabs

Shared `Tabs` provides `buttons`, `segmented` and `underline` appearances. The
selected value is controlled by the caller. Arrow keys and Home/End move through
enabled tabs; each tab exposes its selected state and can reference a named panel.
The model settings use this component, while other workflows can select the
appropriate appearance without duplicating tab layout or keyboard handling.

## Media viewer

MediaViewerProvider is mounted once by the site Providers. Any feature can call
useMediaViewer().open(items, index) with an image/video collection. The hook also
exposes close, next, previous and goTo; the provider owns the current snapshot.
Items carry id, src, type (image/video), title and optional poster/downloadName.

The native modal owns focus, Escape dismissal and scroll restoration. Image controls
provide bounded zoom and pointer panning; arrows navigate the collection. Video uses
native controls without autoplay. Failed media offers retry. Direct download links
retain the source URL; cross-origin browser download policy still applies.

## Pills and pagination

Pill renders informational text by default. kind="button" exposes a selectable
filter through selected/aria-pressed and supports native disabled state.
kind="link" uses the shared router Link with href and selected/aria-current.
Normal, selected, hover and disabled states share the application surface and
text tokens; only genuinely disabled controls use disabled styling.

Pagination uses the shared Button component in both modes. mode="pages" accepts
one-based page, pageSize, total and onPageChange. mode="cursor" accepts
hasPrevious, hasNext, onPrevious and onNext; callers retain their opaque tokens.
An optional label overrides the range summary. disabled suspends both actions.
Search forms use shared Input/Button controls, with an unframed clear icon.

## Game-authored text

Use `PlayerText` from `components/dialogue/PlayerText` for game dialogue, names,
descriptions and titles. The component has no preference or auth hooks. Resolve
`usePlayerDisplay()` once in the page owner and pass its memoized `display` to
children. `className`, `style` and span accessibility attributes are forwarded.
Use `gameTextPlainText(value, display, fallback)` for search and accessible labels;
it shares the same parser rather than stripping markup independently.

The parser selects `{Male=...;Female=...}` before interpreting supported colors,
annotations, emphasis and line breaks. An unset/ask gender uses the authored male
alternative until a Rover is chosen. Player names are substituted after parsing
and cannot inject markup. Unknown markup stays escaped. Numeric talent parameters
remain the talent component's responsibility before passing text to PlayerText.

Missing localized text may fall back to authored inline text. If both are absent,
show an unavailable-text indication; do not infer dialogue from speaker names.

## Skeletons

Use `SkeletonGroup` for one accessible pending region, `Skeleton` for individual
noninteractive bars and `TextSkeleton` for a paragraph. Named card skeletons are
exported by their feature module and reuse its card/grid classes. Loading labels
are visually hidden, while errors and empty-state copy remain visible.
