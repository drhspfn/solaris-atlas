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
