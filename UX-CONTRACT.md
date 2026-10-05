# Interactive map UX contract

## Cited story explanations

V6 uses authored main-quest classification as the primary narrative role; secondary
functions remain separate. Unknown branch occurrence is disclosed once per analysis,
while real choices and conditions retain their per-claim labels. Visual descriptions
are explicitly AI interpretations of sampled frames, attached to the exact published
video variant. Their native disclosure in the cutscene player offers optional timed
chapters through the existing timeline seek behavior. Seeking across an unresolved
Rover choice still opens that choice. Missing or stale visual reports show no generated
description; dialogue and video remain available. Descriptions do not autoplay video.

V5 explanations disclose narrative role, loaded source versions and known/unknown/
cannot-conclude boundaries. Assertion certainty and branch occurrence are separate
labels. Absence claims are limited to the loaded corpus. Open threads show priority
and whether new imports should trigger review. New context appears in a closed
spoiler disclosure with its own citations; the original analysis is retained.
These sections reuse ExplanationSources and native details/summary keyboard and
touch behavior. The admin's import review list uses cursor pagination, the same
run status labels, explicit loading/empty/error/retry states and existing run links.

Quest pages show published AI interpretations in a separate native details section,
below continuity and above the source transcript. Interpretations never replace
authored dialogue. Source buttons are native disclosures: Enter/Space opens the
exact quote, and Read source opens the owning quest with version, locale and a
bounded passage focus. The cited line is highlighted, scrolled into view and
focused; conflicting transcript filters are cleared. Long transcripts load a
bounded window around the citation rather than silently omitting it.

Dialogue source pages reuse that passage navigation: each confirmed quest card
opens the exact line with the displayed locale and resolved snapshot version.
Forward transcripts and reverse quest lookup share explicit source ownership
paths (quest, quest node, or plot-handbook scene). Video dialogue also resolves
through an owned PlayMovie action when the complete multiset of subtitle
localization keys equals that of the dialogue state. Partial text matches and
similar asset names do not establish ownership. Multiple owners remain separate
links; an unlinked line shows an explanation and nearby dialogue, without guessing
ownership from a shared flow name or an inferred graph edge.

Generated events have their own read-only page, quotes and a return link to the
same quest/version/language. Related records and inferred connections use internal
source routes. AI interpretation labels are persistent. UI labels remain English;
explanation content follows the selected locale. Existing DESIGN.md tokens own
surfaces, spacing, typography, focus and responsive behavior.

Search has Source records and Story explanations modes. The existing SearchBox
owns the input, clear button, IME behavior and submit navigation; the form uses
noValidate. Committed question, mode, version and locale stay in the URL. Changing
the question cancels stale requests. Loading, missing analysis, no matches and
request failures have explicit text; failed explanation/search requests offer a
retry. Source disclosures work by click and keyboard. Neither reading a quest nor
searching explanations enqueues an analysis job. Public query embeddings are an
explicit backend option, guarded by Redis and the shared spending ledger.

## Dialogue voice playback

Available quest cutscenes appear in the transcript column with native video
controls. Playback follows a validated clip/choice graph: pauses for keyboard
accessible options inside the video, replaces sources at branch transitions,
and supports shared continuations. Restart returns to the entry. Original video
files retain their identity; analyzed variants use exported fragments with matching
audio, and the shared suffix is one join in the playback graph. Starting a cutscene pauses other
media. Subtitles and soundtrack coverage are stated below the video; asset
versions and technical paths stay behind a source disclosure. A failed video
offers an explicit page-refresh retry instruction. No autoplay is requested.

Quest dialogue uses one compact play/pause button beside the first text line. Text retains the same horizontal position when audio is unavailable; a muted unavailable-audio icon fills the control slot with an accessible tooltip. Starting a voice pauses other audio on the page. Changing voice language replaces the active audio source; unavailable languages do not fall back silently. English is the default, independent of text locale, and the existing narrative preferences provider persists the choice.

The voice language selector deliberately uses the existing native select pattern: the operating system owns its popup and keyboard interaction. Source details are behind a small information icon at the right edge, with a native hover tooltip and a click/keyboard disclosure. Audio asset versions are recorded separately from transcript snapshot versions; a matching filename does not confirm that a current recording is identical to a historical release.

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
| Form | Native HTML form and shared auth-form styles | Browser constraints and server DTO validation | Admin forms use noValidate plus explicit reportValidity on submit | Required fields, bounded steps, retained input after API failure |

## Administration

`/admin` has one persistent sidebar: Content (Story agent) and Operations
(Alerts, Usage). The account menu exposes Admin panel only to administrators.
AdminLayout owns the client access gate; server require_admin and CSRF remain
authoritative for every request. Guests get a sign-in link retaining their route;
ordinary accounts see an access explanation without mounting admin data views.

Step details in the existing admin request history load on demand behind native
disclosures. They expose bounded model text, tool arguments/results, validation
errors, duration and exact-request repeat counts. Execution traces persist with
the checkpoint transaction outside compacted history; legacy requests explicitly
state that execution results were not retained. Provider reasoning is never shown.

Story agent shows cursor-paginated runs, saved research progress, safe request
history and today's shared budget. It refreshes every ten seconds while visible,
cancels obsolete requests and retains usable data on refresh failure. Selected
run and pagination stay in the URL. The creation form accepts only a quest ID;
analysis is always English and source reading remains multilingual. The server
pins the latest imported snapshot containing that quest. Creating the same request reuses
its run. Submissions disable duplicate actions and preserve form values on error.

Step pauses require a positive extension, up to 100 total steps. Responses context
pauses offer explicit compaction; budget/provider/cooldown pauses offer explicit
resume. Uncertain charges cannot resume through the panel. Continuing a checkpoint
preserves completed calls and never raises spending limits. Private model history
and credentials are not exposed.

Output-limit pauses offer Increase response limit and resume. It raises the
response allowance within 32,000 tokens and adds only the step needed to regenerate
the incomplete response. Earlier research is retained; partial tool calls are
discarded and the billed truncated response remains in usage history. The action
uses the existing daily allowance and never bypasses uncertain billing review.
Alerts may be marked resolved after investigation;
Usage is read-only. Tables scroll inside their panel on narrow screens; sidebar
navigation wraps above content. Loading, empty, error and permission states are
explicit, and action outcomes use live status text.

Daily spend is an estimate from reported standard/cache-read/cache-write usage
including the configured safety margin. The USD cap always applies. A separate
daily token guard is optional; a disabled guard displays usage without a zero cap.
Budget pauses mean the next request's reservation cannot fit, which can happen
before displayed settled usage reaches a cap. Tokens include cached input.

## Interaction and persistence

Story notes separate atomic assertions by confirmed, observed anomaly, inferred
and unresolved status, using text as well as color. Each assertion exposes its
authored quest encounter, independent world chronology and knowledge at that
point. Later explanations have their own citations and are hidden in a keyboard
operable spoiler disclosure. Related records explain why a passage matters;
Graph edges show readable endpoints and relationships with source disclosures.
Source disclosures identify their own patch version; source links preserve it.
The server selects the latest available quest transcript for admin submissions,
while research may use any imported patch. The resolved snapshot remains visible
in run details and source links. Earlier jobs retain their original
research scope; browsing does not enqueue a paid analysis.
Older analyses remain readable without fabricated chronology. Generated links
remain explicitly labeled agent interpretations.

World, location, floor, surface opacity, category/subtype exclusions, search and hidden/unknown placement preferences are stored locally in localStorage, with an in-memory fallback. The URL accepts bounded numeric map and marker IDs for shared markers, or map/item/source IDs for acquisition groups. Existing URL filters are imported once and stripped using history replacement. All marker categories start disabled when no preferences have been saved. Preferences are stored as JSON without base64 encoding. Shared markers open their object card and center the map, clearing conflicting search, area and floor filters while preserving type selections. The shared marker remains visible even when its type is disabled. Pan and zoom are transient. Selecting a location fits its source-backed bounds; selecting a floor fits that layer. Floor images render above the surface, which defaults to 30% opacity while a floor is selected. Clusters open by click or keyboard; individual objects can always be opened from the list.

The sidebar owns its scroll; the map owns its viewport. On phones the sidebar is a scrollable top panel. Neither panel changes sibling routes. Map objects use category colors plus named filters; color is not the only identifier.

Collection progress is personal browser state. Chest placements and confirmed
Sonance Casket, Windchimer and Unclaimed Rafter Kite pickup types offer Mark as
found / Mark as not found in the object card. Permanent services, teleportation,
bosses, Tacet fields, plants, ore, unknown collectibles and collection hand-in
points do not offer this action. A generic Collectibles category does not prove
that a placement is collected only once.

Found markers are hidden by default, including from clusters and object lists.
Show found markers restores them at 40% opacity with a named Found state. Found
and remaining placements cluster separately. The selected card remains open
after marking, allowing immediate undo. A shared found-marker link still opens
and focuses its card; its map icon respects Show found markers. Share links do
not include personal progress. The compact sidebar count covers eligible
placements in the selected area/floor and placement visibility scope, independent
of category toggles and text search. Counts remain Loading until all pages arrive.

The versioned localStorage store keys progress by source game-map ID and entity
ID, preserving marks across newer asset jobs and database row IDs. Region/layer
views of the same source placement share progress. Native checkbox, progress and
button controls own these interactions; card status and changed counts are
announced accessibly. Other tabs refresh from storage events. Failed storage
writes keep session state and show an inline warning. This does not infer game
completion, change game data or synchronize accounts/devices.

The map sidebar switches between Markers and Statistics using native pressed
buttons. Switching preserves the region, location, floor, filters and selected
object. Statistics lists every eligible collection type in the same scope as the
overall count, with its existing icon, localized name, found/total count and a
native progress bar. Completed types also have a named completion icon. Search,
category visibility and Show found markers never change these totals. Counts
update immediately on mark/undo; loading, unavailable data and an empty collection
scope are explicit. Sidebar view selection is transient.

## Data and asynchronous behavior

The region selector names regions, not client versions. It shows the latest
available map dataset for each game-map/layer identity; inverted layers remain
distinct. Saved region preferences follow newer datasets. Explicit shared map
links retain their snapshot and marker IDs, with an older selected option labeled
as map data. Client version is disclosed as "Map data" inside Layers & display,
never presented as the region's introduction patch. Names and map IDs do not
establish when a region became playable.

All marker pages are loaded before the final object count is shown. Cancel obsolete requests on world change; ignore stale results. Refresh signed image URLs before expiry. Exact floor assignments are preserved; unknown assignments remain explicit and can be excluded. Hidden source placements are off by default. Game progress and respawn conditions are not inferred. Missing source names retain identifiable source types rather than invented item names.

## Language and accessibility

Interface copy follows the existing English application policy. Game names use the selected locale with English/source fallback, including Japanese. This is a global game archive, not a Japan-specific regulated workflow. Native keyboard controls, Leaflet arrow/zoom controls, object-list buttons, visible focus and reduced-motion behavior are required. No hover-only path is necessary to inspect an object.

Map filter groups are Featured, Battle, Activities & exploration, Shops & services, Unidentified collectibles, Ascension materials, Ore, and Other resources. Regions have a compact button rail; floors have buttons within source area groups. Resource groups come from item descriptions and entity CollectType, not a hardcoded item list. Types with the same item ID or authored name are grouped. Compact tiles reveal their full name on hover/focus, have accessible names and announce the selected type. Dense markers cluster with item icons and count badges; overlapping points at maximum zoom open a list.

The marker toolbar can show or hide all types across every group, regardless of search and location. Show all clears type exclusions; Hide all excludes every marker category and closes object details. Group disclosure buttons preserve filter selections. Featured starts open; other groups start collapsed. Disclosure state is transient and independent of local filters.

The region rail keeps a fixed width on hover and focus. Full names are available through native title tooltips and accessible button labels; disclosure never changes the panel geometry.

Map controls share one top offset. Return to map restores the selected location bounds, or the current region when no location is selected, without changing filters, region, floor or selected object. Zooming out stops one level beyond the region overview. View recovery is transient and is not written to the shared URL.

Frontend behavior defaults (map zoom, tile coordinate contract, paging limits, storage keys and narrative preferences) are owned by `packages/web/src/config/settings.ts`. CSS owns visual layout tokens; server settings remain in the existing server configuration.

Settings offers a preferred Rover for cutscenes: ask each time (default), male,
or female. The existing narrative preferences provider saves it locally, with
an in-memory fallback. Only an explicitly tagged, complete male/female variant
pair skips its prompt. Text labels never establish variant identity. A shared
intro still plays first, followed automatically by the preferred branch and
shared continuation. Narrative and untagged choices remain interactive; page
load never starts playback automatically.

Catalog cards and entity portraits use published client images when available.
Image geometry stays fixed while loading; missing artwork keeps the established
placeholder. Character artwork disclosures link to available full images, and
skill icons use their exact source assets. Character voice playback resolves
only the selected language. Imported asset versions remain independent of the
story snapshot; a matching source path does not establish historical byte identity.

The map return action is an icon button below the zoom controls with a persistent accessible name and title. Marker details have a left-aligned category and title, a copy-link icon, and a square close button in a fixed header; only the detail body scrolls. Duplicate category/title text is omitted. Shared marker links focus at zoom 2 or closer. Copy success is announced briefly, and clipboard errors remain actionable.

All search fields offer a named clear control when populated and restore focus to the input. Clearing dialogue results cancels the pending request. The document reserves scrollbar space across routes; scrollbars use the shared thin theme. Compact map filters and responsive navigation must not overflow or shift sibling content.


Item acquisition combines published gathering placements, possible reward sources,
quest reward previews, confirmed regions and snapshot-scoped shop offers in one
section. Sources are grouped in native region disclosures; hand-in requirements
and general quest references remain in the usage section. Acquisition links
select only placements with an exact item or reward-preview reference, fit the
view to their bounds, clear conflicting filters and offer Clear source filter.
The menu count reflects the acquisition scope. Missing sources do not invent a
merchant position, loot probability or a guaranteed quest reward.


## Cutscene playback

QuestCutscenes owns cutscene controls; voice language reuses NarrativePreferences
and native Select/Listbox semantics, like the transcript voice selector. The page
locale selects caption text independently of voice timing. Music can be switched
only for an explicitly separated music stem. A mixed source has no misleading
music switch. Custom play, seek, subtitle, volume, restart and fullscreen controls share
one overlay inside the stage. The segmented timeline follows the selected path;
unresolved choices show a provisional duration and stop seeking until selected.
Explicit Rover preferences still bypass only verified Rover choices. Master
volume scales every track; music level multiplies the separated music stem. Missing captions and languages are
explicit. Video buffering pauses its audio; failed audio pauses playback and
exposes Retry audio. Changing the language preserves position and playback intent.

Cutscenes sit beside their confirmed transcript states/actions, preserving the
existing branch-aware transcript order. Filtered text/choice views omit players;
focused transcript windows do not append unrelated movies. Choices remain
keyboard buttons. Captions stay inside the fullscreen stage. New controls wrap
at narrow widths and reuse the site's surface, border, focus and mint tokens.
