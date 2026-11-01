# Irrigation Programs

A simple editor for farm-style irrigation programs. You build **programs** out of **steps**
(each step opens one or more blocks for a set time) and choose when they run. The add-on writes
everything into [Irrigation Unlimited](https://github.com/rgc99/irrigation_unlimited), which does
the actual valve switching inside Home Assistant.

## Before you start

1. Install **Irrigation Unlimited** from HACS (Integrations). You don't need to configure it.
2. Your valves (and pump or main valve) must exist in Home Assistant as `switch`, `valve`,
   `light` or `input_boolean` entities, for example from an ESPHome relay board.

## First-time setup

1. Open **Irrigation** in the sidebar.
2. **Mainlines**: add a mainline for each water supply and pick its pump or main valve switch.
3. **Blocks**: add your blocks, give each one a number, name and valve switch, and set a max
   runtime (a safety limit: the valve is forced closed if it runs longer).
4. **Programs**: create a program, add steps, tick the blocks for each step and set durations.
5. Press **Save & apply**, then follow the **Setup** tab: *Connect* adds one line to
   `configuration.yaml`, then restart Home Assistant once.

After that, every **Save & apply** goes live straight away, without a restart.

## Run modes

- **Once at set times**: runs all steps once at each start time on the ticked days.
- **Loop until a set time**: starts at the start time and repeats the steps until the stop time.
  At the stop time it either finishes the running step or stops immediately.
- **Loop constantly**: repeats the steps from when you press Start on the Live page until you press
  Stop. It restarts by itself after a Home Assistant restart.

## Overlap

Each step can open the next step's blocks a few seconds before closing its own, so the line never
sees all valves closed (no pressure spike). Set the default per mainline (in 5-second steps) and
override it per step. In loop modes the last step also overlaps into the next loop.

## Good to know

- Only one program should run on a mainline at a time. The editor warns when schedules overlap,
  and the Live page asks before starting a program on a busy mainline.
- Applying changes reloads Irrigation Unlimited, which stops any watering in progress. The editor
  asks first. Programs set to loop constantly start again by themselves.
- The add-on writes `irrigation_programs.yaml` next to `configuration.yaml`. Don't edit it by
  hand: it is overwritten on every save. A `.bak` copy of the previous version is kept.
- Your programs are stored in the add-on's data and included in Home Assistant backups.
