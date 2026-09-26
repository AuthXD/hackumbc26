# TeachBack — 60-second demo

**Before judges arrive (2 min)**
- `npm run dev`, open http://localhost:5173 in Chrome, allow the camera, and press F11 for full screen.
- Put the red, yellow, green, and blue objects on the table. Check that the overlay labels all four at 90% or more.
  If not, use **Calibrate colors**.
- Press **Reset** (`R`). Voice on. Laptop volume up.
- Backup: if the camera misbehaves, click **Simulator** and run the same script by dragging blocks.

---

**0:00 — Hook (10 s)**
> "Training someone on a hands-on procedure usually means an expert standing over their shoulder. TeachBack
> watches the expert do it once, then coaches the next person and catches their mistakes."

**0:10 — Judge invents the procedure (5 s)**
> "Give me any four steps with these blocks: move one to another zone, stack one, anything."
(For example: red to B, stack yellow on blue, green to C, red back to A.)

**0:15 — Teach (15 s)** Press **Teach** (`T`). Arrange the start, then hands off.
Do the four steps, pausing hands-off for about a second after each.
> "It isn't recording video. It's recording state changes: which object ended up where."
Point at the timeline filling in with before/after snapshots.

**0:30 — Practice (20 s)** Press **Practice** (`P`). The judge (or a teammate) restores the start and does step 1
correctly, and it goes green. Then **skip step 2** on purpose.
TeachBack turns red and says it out loud: *"Skipped step 2. Expected: … Observed: … Put … back."*
> "Deterministic, instant, and it tells you how to fix it."

**0:50 — Recover (10 s)** Undo the mistake. The card reads "Back on track". Finish the remaining steps, and it shows
"Procedure complete".
> "Any procedure, taught once, no code. Next: kitchens, labs, assembly lines."

---

**If asked**
- *Is the AI deciding?* No. Pass/fail is a deterministic comparison of object placements. Gemini only names the
  steps; ElevenLabs only speaks.
- *Hands in the way?* Hands moving or covering an object is a "waiting" state, never an error.
- *Other mistakes?* Wrong object, right object in the wrong zone, and steps done too early are all caught (show one).
- *Tests?* `npm test` runs 68 tests. `npm run demo:check` replays three full demos with different mistakes.
