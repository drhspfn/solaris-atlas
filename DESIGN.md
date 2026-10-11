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
- **Token ownership/runtime mapping:** This document mirrors the canonical runtime tokens in `packages/web/src/styles/index.css`. `:root` defines the named palette and legacy aliases; shared page components consume those variables and the design override layer. No separate generated theme exists.

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

Administration uses compact operation tables and separate activity/import/history
views. Task results and technical worker details are closed native disclosures;
status filters remain native selects. Queue cleanup uses a dark native dialog with
an explicit scope and a safe initial focus. Lore Assistant messages use existing
surface and text tokens; user messages differ through surface depth and a mint border.

Map controls use compact square icon buttons with accessible names and native title tooltips. Marker cards align category and title on one left edge; their header stays visible while the body scrolls. Search clear buttons use the shared `search-clear` treatment and a restrained focus indicator. The global scrollbar baseline in `index.css` uses thin tracks and shared border/text tokens, with a stable document gutter to keep the header fixed across route changes. The map menu uses 46px item rows and 25px item icons; dense icon-only rows remain 44px high.
