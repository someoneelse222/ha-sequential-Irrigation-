# Irrigation Programs

Farm-style irrigation programs for Home Assistant. You build **programs** out of **steps**
(each step opens one or more blocks for a set time) and choose when they run. The add-on
switches your valves and pumps itself, through Home Assistant.

## What you need

Your valves and pump/main valve must exist in Home Assistant as `switch`, `valve`, `light` or
`input_boolean` entities, for example from an ESPHome relay board. Nothing else is needed.

## Pages

- **Live**: every mainline with its pump state and the programs running on it. For each running
  program you see every step: done, running (with time left), next and later (with start times),
  plus when the program or loop ends. Skip step, Pause, Resume and Stop per program, per mainline
  or for everything. Several programs can run on the same mainline at once.
- **Programs**: the step editor and run mode (once at set times, loop until a set time, loop
  constantly), plus an optional season.
- **Blocks**: live state of every valve, which program has it open and how long is left.
  **Open** and **Close** always send the signal, even if the valve already looks open or closed,
  to fix valves that got out of sync. Open on an idle block runs it for its default runtime.
  Use ✎ to edit a block.
- **Mainlines**: pump / main valve, timings and default overlap.

## Pump and valve order

- Starting: blocks open first, the pump/main valve follows *Pump opens after blocks* seconds later.
- Finishing, pausing and stopping: the pump/main valve closes **first**, the blocks follow
  *Pump closes before blocks* seconds later (at least 2 s).
- Steps overlap: the next step opens *overlap* seconds before the current one closes, so the line
  never sees every valve closed.

## Restarts and crashes

The add-on saves where each running program is every few seconds. If the add-on or Home
Assistant restarts, or contact with Home Assistant is lost, the program clock stops and carries
on from the same point when things are back, and valves are re-sent their correct state.

## Out-of-sync valves

If a valve doesn't match what the add-on last sent it for 20 seconds, the command is sent again
and a note appears in Recent activity. The Blocks page shows any valve that is out of sync.

## Saving

Saving never interrupts watering. Programs already running finish with the settings they
started with; changes apply from their next start.
