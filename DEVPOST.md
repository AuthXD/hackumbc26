# TeachBack

**Show a procedure once. TeachBack coaches the next person through it.**

## Inspiration

Most hands-on skills still get passed on the same way: someone experienced stands next to you and says "no, the
other one first." Lab protocols, kitchen prep, assembly steps, and equipment setup all depend on doing things in the
right order, and the expert's time is the bottleneck. Written checklists don't notice when you skip a line. Video
tutorials can't see what you did. We wanted something you teach the way you teach a person, by doing it once, and
that then watches the next person like a patient expert would.

## What it does

TeachBack watches a tabletop through a webcam.

- **Teach**: an expert performs a short procedure once, for example "red block to Zone B, stack yellow on blue,
  green to Zone C, red back to A." TeachBack learns each step as a *state change* (which object moved, where it
  ended up, what it's stacked on) and shows a four-step timeline with before/after snapshots.
- **Practice**: the next person performs it. Each correct step turns green and is announced. On a mistake, TeachBack
  stops and shows and speaks **what it expected versus what it observed**, plus a concrete fix ("Put the yellow
  object back in Zone B. Then do step 2."). It catches **skipped steps**, **out-of-order steps**, **the wrong
  object**, and **the right object in the wrong place**. Hands moving or covering an object never count as mistakes.
  Undo the error and it coaches you through to the finish.

The procedure isn't hardcoded. A judge can invent any four-step sequence on the spot.

## How we built it

- **Frontend**: React + TypeScript + Vite. The webcam is captured with `getUserMedia` and streamed as JPEG over a
  WebSocket at about 5 fps with one frame in flight, so there is never a backlog. A canvas overlay draws the zones
  and labeled detections. The UI has a large status card (Expected / Observed / Fix) readable from across a room.
- **Backend**: Python, FastAPI, OpenCV, and NumPy.
  - HSV color segmentation finds one object per color. Its center maps to a zone. Stacking is inferred from
    bounding-box overlap (overhead camera) or edge contact (angled camera).
  - A stability filter commits a state only when every object is visible, the frame is still (pixel-change motion
    gate), and the arrangement has held for 700 ms.
  - A deterministic sequence engine compares each committed state with the learned step's postconditions and
    classifies errors by matching unexpected changes against later steps.
- **Gemini**: after a step is learned, Gemini gets the structured StepDelta JSON plus the before and after keyframes
  and returns strict JSON (`{title, instruction}`) to name the step naturally. We validate the response, cache it
  per step, and fall back to deterministic wording.
- **ElevenLabs**: spoken coaching for step completions and actionable errors, through a small server-side proxy that
  keeps the key off the client. It falls back to browser speech synthesis.
- **Testing**: 68 pytest tests, including 25 randomized "judge-invented" procedures and full end-to-end runs through
  real JPEG frames with a simulated hand. A live `demo:check` script replays the judging demo three times against the
  running server with three different mistakes. A built-in **simulator** renders a draggable virtual tabletop through
  the identical vision pipeline, for testing and as a camera-failure fallback.

## Challenges we ran into

- **Hands are the enemy.** A hand holding an object mid-air looks like a new state. We added a motion gate
  (percentage of changed pixels, since a mean difference was too diluted by a small hand), an occlusion state, and a
  700 ms settle rule.
- **2D stacking is ambiguous.** "Resting on top of" and "touching from behind" look identical in one frame. Our fix
  is causal: an object that hasn't moved since the last settled state can't have *become* stacked.
- **Randomized tests caught a design bug.** We had inferred "teacher undo" whenever a layout returned to its previous
  state. But "move it back" is a legitimate step in real procedures, so undo became an explicit button.
- **Browser performance surprise.** `canvas.toBlob` sometimes took 500–1000 ms in Chromium, which starved the frame
  stream. Switching to synchronous encoding restored a steady 5 fps.
- **Keeping AI out of the verdict.** It's tempting to ask a vision-language model "did they do it right?" We made
  that impossible by design: pass/fail is a placement comparison, and the model only writes nicer words.

## Accomplishments that we're proud of

- A judge can invent a procedure and see it learned and enforced live. Nothing is scripted.
- Every error message is actionable: expected, observed, and exactly how to fix it.
- It degrades gracefully. No API keys, no camera (use the simulator), or a backend restart (the procedure is saved)
  all still leave a working demo.
- A test suite that checks behavior, not just code: randomized procedures, simulated hands, and three live demo runs.

## What we learned

- For demos that must work every time, simple deterministic vision (HSV plus rules) with good state filtering beats
  a more impressive model that's right most of the time.
- Represent actions as **state transitions with postconditions**. That made skip, out-of-order, and wrong-object
  detection fall out naturally.
- Use AI where fuzziness is a feature (wording, voice), not where correctness is the product.

## What's next

- Learned object appearance instead of fixed colors: any tools or ingredients, via few-shot embeddings.
- Richer steps: rotations, open/close states, pouring, timed holds.
- Multiple procedures, branching steps, and per-learner analytics for trainers.
- Phone and smart-glasses cameras over HTTPS, and hands-free voice commands.

## Track notes

**Entrepreneurial idea.** Training for hands-on work is expensive, expert-bound, and hard to verify. The initial
market is places where procedure order matters and mistakes are costly: lab onboarding, commercial kitchens, light
assembly, and healthcare supply prep. The pitch is "record once, coach forever": an expert's single demonstration
becomes a reusable, self-checking trainer. The wedge is a laptop and a webcam with no custom model training, so a
team can set it up in minutes. Revenue would come from a per-station subscription for trainers, with audit logs of
who completed which procedure correctly as the upsell for compliance-heavy settings.

**Engaging demo.** The judge chooses the procedure, so it's obviously not canned. Then someone deliberately makes a
mistake and TeachBack immediately says out loud what it expected and what it saw. The recovery ("Back on track",
then "Procedure complete") closes the loop in under a minute. The UI is built to be read from several feet away.

**Gemini.** Gemini gets structured context, not just pictures: the exact StepDelta (which objects changed, before
and after placements) plus before and after keyframes. It returns schema-constrained JSON (`responseMimeType:
application/json` with a `responseSchema`) naming the step for a newcomer. We validate the response, reject answers
that omit a handled object, cache per step, and never let Gemini decide correctness. It's grounded,
structured-output use of a multimodal model in a safety-conscious role.

**ElevenLabs.** Coaching is hands-busy, eyes-on-the-table work, so voice is the natural channel. ElevenLabs speaks
only meaningful moments: a step completed, an actionable error with its fix, or the procedure finished. It stays
quiet during ordinary waiting. Calls go through a cached server-side proxy (low-latency flash model), and
`speechSynthesis` takes over automatically if the service or key is unavailable.
