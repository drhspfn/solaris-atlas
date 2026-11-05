# Current quest null-choice audit

Read-only API15c audit of English current projections, game3.7.0/release21:

| Quest node | Null targets | Selection without explicit branch | Explicit FinishTalk | Side effects without jump | Authored sequence continuation |
|---|---:|---:|---:|---:|---:|
|4688653|22|21|1|0|18|
|5389571 (Denia)|43|41|1|1|28|

The continuation column overlaps selections; it is not an additional category.
None of the65 options has a nested JumpTalk or a matching
SequenceTransitions.OptionTextKey. No missing explicit branch was found.
PlotLineId identifies the option's authored plot line, not a JumpTalk destination;
it must not be treated as a TalkItem.Id. SelectInfo is absent in these options.

The retained client FbTalkOption interface exposes Actions and option presentation
fields; explicit JumpTalk carries TalkId, while FinishTalk has no destination.
Existing compiler behavior retains inline options, their side effects and authored
TalkSequence edges separately. The62 selections do not require invented
choice_branch edges. Two terminal options retain FinishTalk (one also retains
FinishDoInteract). The remaining side-effect option retains ChangeEntityState and
SetVar; its actions do not prove a dialogue jump or terminal state.

For46 options, TalkSequence explicitly supplies a continuation from the parent
TalkItem. This is normal authored dialogue continuation, not an option-specific
branch. For other selections, missing explicit navigation does not authorize
inferring execution from array adjacency. Full engine execution was not tested.

PlayerChoice.source_record_id initially pointed to27 retained records from
releases15/17. A separate bounded read of all27 corresponding release21 flow
records confirmed every selected option object, TalkSequence and
SequenceTransitions unchanged. Whole flow records are not asserted identical.
Localization resolution metadata is not branch-resolution metadata.

No runtime parser change was warranted. Three exact ShowTalk actions from current
release21 are retained in tests/fixtures/null_choice_release21.json, with original
canonical indices and source-record IDs. Tests verify option preservation, no
fabricated branch, nested terminal/side-effect actions, and explicit ordinary
continuation. Validation:6 compiler tests and20 server choice/browsing tests
passed; Ruff passed.

Machine-readable per-choice evidence is saved locally in ignored
scratch/null-choice-semantics-receipt.json, with source snapshots and current
release pin verification alongside it. No enqueue, graph repair, production
mutation, UI edit or changes to run26922/the13 publication targets occurred.
