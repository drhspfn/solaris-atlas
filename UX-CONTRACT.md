# Interactive map UX contract

## Scope and evidence

Read-only game atlas at `/map`, using published map manifests, positioned entities and authored game marks. The API and `docs/game-data/maps.md` own coordinate and source semantics. Existing DESIGN.md owns visual tokens. No real-world location data, purchases or irreversible actions.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
| --- | --- | --- | --- | --- |
| Select/Listbox | Native HTML select | StoryMapPage and LocaleSwitcher | Platform-owned popup geometry is acceptable for world and grouped location selection | Keyboard selection, long floor names, narrow viewport |
| Search | Native search input with visible label | WorldMapPage | Immediate local filtering; committed query in URL | Empty results and clear input |
| Checkbox | Native checkbox | WorldMapPage | Hidden placements and unknown floor | Keyboard toggle |
| Object selection | Native button with aria-pressed | WorldMapPage | Two-column icon/name/count tiles; compact icon grid for other resources; select all and clear per group | Keyboard, individual types, group actions and counts |
| Range | Native range input | WorldMapPage | Surface opacity from 0 to 100 | Keyboard adjustment and output value |
| Navigation | React Router and App topbar | App.tsx | `/map` route and URL filter state | Back/forward and reload |
| Map viewport | Leaflet CRS.Simple | Game world coordinates and map manifest | Preview at overview, visible original tiles at close zoom | Region alignment, floors and zoom |
| Feedback | Inline page status and retry panel | API request lifecycle | Map remains readable during marker loading | Loading, empty dataset, errors and retry |

## Interaction and persistence

World, location, floor, surface opacity, category/subtype exclusions, search and hidden/unknown placement preferences live in URL query parameters. Pan and zoom are transient. Selecting a location fits its source-backed bounds; selecting a floor fits that layer. Floor images render above the surface, which defaults to 30% opacity while a floor is selected. Clusters open by click or keyboard; individual objects can always be opened from the list.

The sidebar owns its scroll; the map owns its viewport. On phones the sidebar is a scrollable top panel. Neither panel changes sibling routes. Map objects use category colors plus named filters; color is not the only identifier.

## Data and asynchronous behavior

All marker pages are loaded before the final object count is shown. Cancel obsolete requests on world change; ignore stale results. Refresh signed image URLs before expiry. Exact floor assignments are preserved; unknown assignments remain explicit and can be excluded. Hidden source placements are off by default. Game progress and respawn conditions are not inferred. Missing source names retain identifiable source types rather than invented item names.

## Language and accessibility

Interface copy follows the existing English application policy. Game names use the selected locale with English/source fallback, including Japanese. This is a global game archive, not a Japan-specific regulated workflow. Native keyboard controls, Leaflet arrow/zoom controls, object-list buttons, visible focus and reduced-motion behavior are required. No hover-only path is necessary to inspect an object.

Map filter groups are Featured, Battle, Activities & exploration, Shops & services, Unidentified collectibles, Ascension materials, Ore, and Other resources. Regions have a compact button rail; floors have buttons within source area groups. Resource groups come from item descriptions and entity CollectType, not a hardcoded item list. Types with the same item ID or authored name are grouped. Compact tiles reveal their full name on hover/focus, have accessible names and announce the selected type. Dense markers cluster with item icons and count badges; overlapping points at maximum zoom open a list.
