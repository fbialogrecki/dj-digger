# Code cleanup and local music actions — 2026-09-06

Reviewed application Python modules using AST function/duplicate inventories,
Graphify navigation, static unused-code checks, and direct caller inspection.
Framework callbacks and public entry points were checked before treating an
unused-code warning as a deletion candidate.

## Changes

- Removed the unused database matching helper, application job helper, four
  unused loggers, and a duplicate audio-state declaration. The scanner test now
  rejects any database lookup instead of patching the removed helper.
- Shared local audio-entry filtering, output codec selection, and prepared-audio
  creation between their existing callers.
- Split profile import into owner resolution, legacy identity linking,
  pagination, hydration, and per-playlist import. The orchestrator decreased
  from 104 to 23 lines; hydration still preserves repetitions and playlist order.
- Split export planning into transformation decisions and portable path
  allocation (87 to 37 lines). Extracted verified copying from preparation
  (70 to 56 lines), and shared copy-report checkpoints.
- Calculate completed export sources once when constructing the missing-file
  report, removing a repeated set construction inside the per-item loop.
- Kept replacement commit/recovery ordering, DSP processing, authentication
  boundaries, and declarative UI/CLI construction intact. Function length alone
  is not evidence that splitting improves these sections.

## Local music usability

Local views now show conversion, analysis, playback, selection, and manual
analysis editing in the clickable footer. Less essential actions drop out as
the terminal narrows. Store actions and the store legend return for online
views. Stop appears while an operation runs.

Conversion settings explain selection scope, destination, and the default copy
behavior before the existing inspection and execution review. Analysis asks
the user to start the displayed number of files, enables BPM/Key columns, and
reports missing optional dependencies before entering the per-track loop.

## Verification

- Full offline suite: 872 passed, 81 live tests deselected.
- Additional focused checks cover the local footer at 80 columns and a missing
  analysis dependency without starting audio work.
- Ruff, specification section-map validation, and whitespace checks pass.
- Vulture at 100% confidence reports no unused-code findings; this is a static
  check, not a claim that every dynamically reachable path is necessary.
- Headless Textual previews inspected for the local view and export dialog.
- README and the affected specification sections describe the current UI.
