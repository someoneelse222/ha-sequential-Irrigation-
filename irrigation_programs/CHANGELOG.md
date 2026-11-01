# Changelog

## 0.4.0
- Orange for anything about to close: steps in their overlap, programs stopping/pausing/ending, closing blocks.
- Closing a block that a program has open holds it closed only for that step; it opens again at the next step that needs it. The Live page shows held-closed blocks clearly.
- "Add block" adds one block through a dialog.

## 0.3.0
- Header line shows valve switching as it happens (e.g. "Opened block 3", "Waiting 5 s to close blocks on Mainline 1").
- Live page: each mainline is its own clearly separated section with its controls next to the title.
- Programs page is a list per mainline with enable tick, status and Edit; rows coloured running / paused / disabled.
- Blocks page grouped by mainline, rows coloured when open or disabled.
- A valve can only belong to one block (and not be a pump too).
- Setup page removed. Better phone layout.

## 0.2.0
- The add-on now switches valves and pumps itself (Irrigation Unlimited is no longer used).
- Pump/main valve opens after the blocks and always closes first (finish, pause, stop).
- Several programs can run on the same mainline at once.
- Running programs resume from where they were after a crash, restart or lost connection.
- Live page shows every step with status, time left and start times, plus recent activity.
- Blocks page shows live valve state with Open/Close buttons that always send the signal;
  blocks are edited in a dialog. Default runtime replaces max runtime.
- Problems and warnings are listed in full at the bottom of the page.
- Out-of-sync valves are detected and re-sent their state.

## 0.1.0
- First version (programs ran on Irrigation Unlimited).
