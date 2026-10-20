---
version: alpha
name: Solaris Atlas
description: An editorial story atlas for exploring source-linked Wuthering Waves characters, places and narrative connections.
colors:
  primary: "#9BD9C0"
  background: "#090E0F"
  page: "#0B1112"
  surface: "#0F1617"
  surfaceRaised: "#121A1B"
  surfaceActive: "#162021"
  border: "#293738"
  text: "#E8ECEB"
  textSecondary: "#A0AAA8"
  textMuted: "#707C7A"
  mint: "#9BD9C0"
  mintStrong: "#B2E7D1"
  cyan: "#82CBCD"
  warm: "#D5C6A2"
typography:
  display:
    fontFamily: "Manrope, sans-serif"
  body:
    fontFamily: "DM Sans, sans-serif"
  utility:
    fontFamily: "Manrope, sans-serif"
rounded:
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "12px"
  xl: "14px"
spacing:
  content-max: "1200px"
  section-desktop: "96px"
  section-mobile: "64px"
components:
  button:
    backgroundColor: "#9BD9C0"
    textColor: "#09110E"
    rounded: "10px"
  search:
    backgroundColor: "#0F1617"
    textColor: "#E8ECEB"
    rounded: "12px"
  card:
    backgroundColor: "#0F1617"
    textColor: "#A0AAA8"
    rounded: "12px"
  featured-panel:
    backgroundColor: "#121A1B"
    textColor: "#A0AAA8"
    rounded: "14px"
---

# Solaris Atlas Design System

## Overview

### Creative North Star

Solaris Atlas should feel like an editorial field atlas assembled from quiet resonance traces: generous reading space, restrained orbital marks, and source evidence that remains easy to inspect. The connected graph is the signature; it should look like paths through an archive rather than a control dashboard.

### Product context and register

- **Audience and primary job:** Wuthering Waves players exploring characters, story transcripts, locations, items and source-backed links.
- **Target market(s) and evidence:** Global audience; the UI exposes English, Russian and Chinese locale choices in `packages/web/src/components/layout/LocaleSwitcher.tsx`.
- **Locale(s) and language policy:** UI copy is currently English; localized game text follows the selected archive locale.
- **Usage scene:** Desktop-first browsing with responsive mobile access; users move between searchable catalogs, entity profiles and quest transcripts.
- **Register:** Hybrid. The home page carries the editorial brand; catalogs and detail pages prioritize readable evidence and navigation.
- **Memorable signature:** Thin mint and cyan resonance paths connecting story entities.
- **Restraint:** Most surfaces stay near-monochrome. Semantic colors identify entity type in small marks only.
- **Anti-references:** Neon cyberpunk, terminal green, generic SaaS panels and glass effects.
- **Token ownership/runtime mapping:** The maintained Next.js frontend uses `packages/web_new/src/styles/index.css` for the canonical runtime palette. `site-shell.css` consumes these tokens for the header, footer and shared selectors. `packages/web` is the legacy frontend. No separate generated theme exists.

## Colors

Use the exact Solaris Atlas palette defined in `:root` of `packages/web/src/styles/index.css`. Root and page backgrounds are `--bg-root` and `--bg-page`; cards use `--surface-1`, hover uses `--surface-2`, and active panels use `--surface-3`. Mint marks primary actions and selected story elements; cyan marks graph paths and location states. Warm gold is reserved for story/lore emphasis. Semantic entity hues stay on small icons, dots and borders rather than large fills.

## Typography

Manrope carries display and utility labels; DM Sans carries body copy and controls. Headings use regular or medium weights. Technical labels use restrained tracking. Preserve script fallbacks and allow long localized names to wrap.

## Layout

Keep the existing route and component structure. Home sections use generous vertical spacing and a content width near 1200px. Catalogs and profiles retain their existing responsive grids and reading order. At narrow widths, navigation collapses, search stays visible, and the graph path becomes a vertical sequence.

## Elevation & Depth

Depth comes from three surface levels and thin borders. Static cards have no shadow. A faint mint focus ring is allowed on the active search field; do not add repeated card glows.

## Shapes

Use 6px graph nodes, 8px chips, 10px buttons, 12px inputs/cards and 14px featured panels. Avoid large radii and pill controls except for compact status indicators.

## Components

The existing CSS in `packages/web/src/styles/index.css` is the runtime owner. Global semantic variables map the palette and radii to existing components; the appended Solaris layer refines existing selectors instead of introducing a parallel component system. Search, navigation and entity cards retain their established React owners.

## Do's and Don'ts

- Do make Story a first-class route and keep source-backed links visually legible.
- Do label illustrative graph examples as illustrative.
- Do keep orbital background marks low contrast and respect reduced motion.
- Don't imply a character relationship from co-presence or style a source reference as a narrative fact.
- Don't use bright green, heavy glow, glass surfaces or decorative terminal labels.

## Map and shared controls

The maintained Next.js header groups Maps and Archive, with Events directly
accessible. Account controls live on the right. Authenticated users find Settings
in their account menu; guests have a Settings icon beside Sign in. API, MCP,
GitHub and legal links belong in the footer. Menus close on navigation, outside
click and Escape. Header controls have a minimum 44px target. Mobile navigation
is a bounded panel rather than a full-height resource list. Decorative collection
sequence numbers are omitted.

`packages/web_new/src/components/ui/Select.tsx` owns authored single-select popups
for archive language, interactive-map fields and event filters. Native popovers provide top-layer
placement and dismissal, with listbox keyboard navigation and typeahead. Remaining
native selects retain native behavior and use the shared dark palette. This is a
deliberate change requested for the public shell and map; other controls migrate
incrementally. Chat scroll belongs to the message container, never the document.

Native checkboxes keep their semantic input and Space/label-click behavior, with
a shared dark surface, mint checked/indeterminate state, visible keyboard focus
and forced-colors fallback. The map sidebar uses an 18px horizontal gutter and
full-width location/search rows; the toolbar does not squeeze both into half-width cells.

On viewports up to 760px the map occupies the space below the header. A floating
Map filters button opens the existing controls in a native modal bottom sheet;
Locations contains region, location and floors, Objects contains search, marker
types and statistics. The active tab owns one scroll container with a fixed
header, tabs and Show map footer. Closing retains filters and returns focus to
the opener. Escape/backdrop dismissal, keyboard tabs, safe-area padding and
viewport resizing preserve the desktop sidebar and map state without duplicate inputs.

The desktop map sidebar also uses Locations/Objects tabs. Its height is constrained
to the map grid, with a fixed heading and tab bar; only the active panel scrolls.
Character voice lines separate text locale from audio tracks. Text selection sets
the default voice language; an individual player's explicit language choice wins.
Players use compact play/pause, seek, time and mute controls and show unavailable
or retry states. Switching tracks stops playback. Voice grids use shrinkable
columns and wrap text before falling back to a single mobile column.
`components/ui/AudioPlayer.tsx` owns playback of a single URL. `TrackPlayer.tsx`
adds only an optional name-to-URL map and track selection. Neither component
knows languages, characters or API paths; feature callers provide those labels
and URLs.

Administration uses compact operation tables and separate activity/import/history
views. Task results and technical worker details are closed native disclosures;
status filters remain native selects. Queue cleanup uses a dark native dialog with
an explicit scope and a safe initial focus. Lore Assistant messages use existing
surface and text tokens; user messages differ through surface depth and a mint border.

Map markers center their icons in a single bordered chip. Hover and keyboard focus expand that chip to reveal the name, opening left near the map's right edge. Region rail buttons use 44px targets and expand left to reveal full names. These controls use accessible labels without duplicate native title tooltips. Marker cards align category and title on one left edge; their header stays visible while the body scrolls. Search clear buttons use the shared `search-clear` treatment and a restrained focus indicator. The global scrollbar baseline in `index.css` uses thin tracks and shared border/text tokens, with a stable document gutter to keep the header fixed across route changes. The map menu uses 46px item rows and 25px item icons; dense icon-only rows remain 44px high.

## Game-client event catalog

The event page uses a scrollable daily calendar with horizontal occurrence bars.
Each event keeps its own color across reruns; dark purple, teal, rust, indigo,
forest and rose fills are a deliberate exception to the otherwise restrained
semantic marks. Artwork blends into the end of a bar and text stays readable
when the timeline scrolls. A bounded 42-day window, month navigation, searchable
multiple-event selection and a run index expose history without rendering years
of columns. Presence in a client build must not be presented as proof of current
availability; events without recorded schedules remain in a separate catalog.
Event details open in a native modal dialog with Escape, inert background and
focus restoration. Details expose the published image gallery. Selecting a gallery image updates
the preview, and animated convenes can use client-authored featured portraits or
weapon icons as static previews. Reward previews use localized names instead of
raw ID chips. Admin import notices confirm queued work, not completed publication.

## Public detail addresses

Events and Announcements share the event archive header, navigation, 1440px
container and mobile gutters. Calendar sections group character convenes, weapon
convenes, limited events and recurring modes. Chronological interval packing reuses
free lanes across different events without overlapping visible bars. A lane does
not imply a relationship between its events; IDs, selection and histories remain
independent. Permanent content without a closing date appears below the calendar.
Closing-time sorting applies to the event list; filters expose the same groups.
Full official Notice posters take precedence for matching visible runs; older
pinned Notice revisions remain authoritative. Ambiguous client rows may link to
a current announcement about the exact featured subject without asserting its
schedule or roster. Client UI textures remain in a separate disclosure, deduped
by image URL; small weapon variants are omitted when large art exists. When no
poster exists, a subject can be layered over its authored client background.

Use the API's stable slug address for entity, quest, event and notice links.
System keys remain the fallback for records without a resolved name. Canonical
redirects preserve locale, version and dialogue selections; a slug must never
replace an internal game/graph ID or become the displayed entity name. Social
metadata and details are returned in the first server response at the slug URL.
