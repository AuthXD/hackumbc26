# TeachBack demo

Four objects, one teach, one deliberate mistake, one setup check. Color mode is the recovery path if the model
does not reach Ready.

## Pre-demo checklist

Do this before judges are in front of the table.

1. Stabilize or mount the phone so the whole mat stays in frame.
2. Keep all four mat landmarks visible: purple creature, frog, potion bottle, SteelSeries logo.
3. From the repo: `npm run dev`. Open http://localhost:5173 on the laptop.
4. Select **Semantic Objects**. Wait until the label says **Model ready**. If it stays on **Loading model**, do
   not start the demo. If it says **Model error**, press **Retry model** once. If it still fails, use Color mode.
5. Start a trusted HTTPS tunnel to the frontend, for example `cloudflared tunnel --url http://localhost:5173`.
6. Paste that HTTPS address into **Connect phone**, or set `TEACHBACK_PHONE_URL` in `.env` and restart.
7. Open the QR link on the phone and approve the rear camera.
8. On the laptop, confirm the button says **Phone streaming**. **Phone connected — no video yet** means the page
   is open and no valid frame has arrived.
9. Press **Calibrate mat**. Click TL purple creature, TR frog, BR potion bottle, BL SteelSeries logo. Save.
10. Wait until the status says **Mat tracking** and the view is the top-down mat, not the raw camera.

Put only these objects on the mat, separated, none on the corner stickers:

- blue water bottle
- brown wallet
- green smartwatch
- blue smartphone

Descriptions field, already the default: `blue water bottle, brown wallet, green smartwatch, blue smartphone`.
Press **Apply objects** if you changed it. Press **Reset**. Voice on. Laptop volume up.

## Teach and practice

**Operator.** Press **Teach**. Arrange the start. Hands off. Press **Scan Objects**. Move one object to another
zone. Hands off. Press **Scan Objects**. Repeat until four steps are in, or press **Finish Teaching** earlier if
the judge only asked for a short sequence. Press **Practice**. Put the objects back to the start. Scan. Do the
first step correctly and scan.

**Say.** "It learns the procedure from the objects and the zones, not from a script."

## One mistake, then the fix

**Operator.** Skip the next step on purpose. Scan. Leave the red card up long enough to read Expected, Observed,
and Fix. Undo that move. Scan again. Finish the remaining steps.

**Say.** "That miss does not advance. Put it back, scan, and it continues."

## Setup Check

**Operator.** Press **Pause procedure** if a procedure is running, then **Setup Check**. Three objects is enough:
water bottle, wallet, smartwatch, one per zone. Scan. Name it `Lab bench`. Press **Capture Setup**. Remove the
wallet and move the watch. Scan with **Check Setup**. Restore them and check again. Press **Procedure** to leave.

**Say.** "Same scan, plain rules: missing, wrong zone, or complete. The model does not grade the setup."

## Readiness, in one sentence

**Say.** "Semantic mode loads the local model in the background. Scanning stays off until it reports ready."

Do not say it is scanning while the label says **Loading model**.

## Optional: tracking loss

Only if the phone is mounted and tracking has been steady for several seconds.

**Operator.** Cover one corner sticker, or tilt the phone until the card says the mat is lost. Uncover all four
corners and wait until **Mat tracking** returns. Do not scan during the loss.

**Say.** "If it cannot see the mat, it refuses a verdict instead of guessing."

Skip this if the view flickers. A flaky loss looks like a bug.

## If something breaks

| What happened | What to do |
|---------------|------------|
| A sticker moved or tracking says recalibrate | **Calibrate mat** again, same four clicks, then wait for **Mat tracking**. |
| **Model error** | **Retry model**. If it fails, **Color**, and demo with the red, yellow, green, and blue objects. |
| Phone video stops | The laptop should return to its own camera within a couple of seconds. Reopen the QR page. Wait for **Phone streaming**, then recalibrate if the card asks. |
| Scan says it cannot see an object | Separate the four objects, keep hands out, scan again. There is no confidence number to interpret. |
| Tunnel or QR is `http://` | The phone will not open the camera. Use the HTTPS tunnel URL. |
