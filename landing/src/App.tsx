import { demoVideoUrl, githubUrl, liveDemoUrl } from "./links";

const workflow = [
  ["Show once", "Perform the task on camera. TeachBack watches the objects, not a script."],
  ["Learn the order", "Each settled change becomes the next step, in the order you actually did it."],
  ["Coach the next person", "Practice checks the live table against that sequence."],
  ["Name the mistake", "A skip, the wrong object, or the right object in the wrong place stops the sequence."],
  ["Keep the procedure", "A name, the steps, and later checks stay available for the next session."],
];

const demoSteps = [
  ["Describe what is on the table", "Name the visible objects, such as a bottle, a wallet, a watch, and a phone."],
  ["Teach any short procedure", "Move them in whatever order the task needs. Nothing is hardcoded to one demo."],
  ["Make one deliberate mistake", "Skip a step, move the wrong object, or put the right one in the wrong place."],
  ["Read the correction", "TeachBack states what it expected, what it saw, and how to fix it."],
  ["Finish after the fix", "Correct the action and the rest of the learned procedure continues."],
];

const trust = [
  ["Locate Anything", "Described physical objects are identified on this machine. The model does not grade the step."],
  ["Deterministic checks", "A step passes only when the learned state change is actually there."],
  ["Gemini", "Names a procedure, writes clearer instructions, and answers from the saved steps."],
  ["Tiger Data", "Keeps named procedures, workspace setups, and readiness history in PostgreSQL."],
];

const matters = [
  "Fewer repeated shoulder-to-shoulder demonstrations.",
  "Hands-on know-how stays with the procedure, not only with the person who first showed it.",
  "A mistake comes with the correction, so practice can continue.",
  "The same engine fits a lab bench, a workshop, a class, a training tray, or a shelf at home.",
];

const tech = [
  "Local computer vision",
  "Gemini API",
  "Tiger Data / PostgreSQL",
  "FastAPI",
  "React",
  "iPhone and Android cameras",
];

function Mark() {
  return (
    <span className="mark" aria-hidden="true">
      <svg viewBox="0 0 32 32">
        <path d="M8 17l5 5 11-12" />
      </svg>
    </span>
  );
}

export function App() {
  return (
    <>
      <a className="skip" href="#content">Skip to content</a>
      <header className="top">
        <a className="brand" href="#content">
          <Mark />
          <span>TeachBack</span>
        </a>
        <nav aria-label="Page">
          <a href="#workflow">Workflow</a>
          <a href="#demo">Demonstration</a>
          <a href="#uses">Uses</a>
          <a href="#trust">How checks work</a>
        </nav>
      </header>

      <main id="content">
        <section className="hero">
          <p className="eyebrow">Teach once. Coach every time.</p>
          <h1>Teach any visible procedure by demonstrating it once.</h1>
          <p className="lede">
            TeachBack watches an object-based task, learns its ordered steps, and coaches the next person through it.
            Computer vision verifies each action, Gemini explains the procedure, and Tiger Data preserves it for future training.
          </p>
          {(demoVideoUrl || liveDemoUrl) && (
            <div className="actions">
              {demoVideoUrl && (
                <a className="btn primary" href={demoVideoUrl}>Watch Demo</a>
              )}
              {liveDemoUrl && (
                <a className="btn" href={liveDemoUrl}>Open Live Demo</a>
              )}
            </div>
          )}
          <p className="caption">Local-first computer vision · deterministic verification · reusable training</p>
        </section>

        <section id="workflow" aria-labelledby="workflow-title">
          <h2 id="workflow-title">From one demonstration to the next person</h2>
          <ol className="steps">
            {workflow.map(([title, body], index) => (
              <li key={title}>
                <span>{index + 1}</span>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="demo" aria-labelledby="demo-title">
          <h2 id="demo-title">What a session looks like</h2>
          <p className="section-lead">Any short tabletop procedure. The objects and the order come from the demonstration.</p>
          <ol className="cards">
            {demoSteps.map(([title, body], index) => (
              <li key={title}>
                <span>0{index + 1}</span>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="uses" aria-labelledby="uses-title">
          <h2 id="uses-title">One engine, many procedures</h2>
          <p className="section-lead">These are ways to use the same learned sequence. They are not separate products.</p>
          <div className="cases">
            <article>
              <h3>Toolbox Handoff</h3>
              <p>
                Teach a repeatable workstation shutdown or tool-return procedure. Catch skipped inspections,
                incorrect tools, and out-of-order actions.
              </p>
            </article>
            <article>
              <h3>Clinical Training Tray</h3>
              <p>
                Teach students a simulated tray-preparation sequence and coach them through the learned order.
                TeachBack assists with training; it does not provide medical judgment or certify sterility.
              </p>
            </article>
          </div>
        </section>

        <section id="trust" aria-labelledby="trust-title">
          <h2 id="trust-title">What decides a step, and what only explains it</h2>
          <ul className="trust">
            {trust.map(([title, body]) => (
              <li key={title}>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ul>
          <p className="rule">Gemini never decides whether a physical action passed.</p>
        </section>

        <section id="architecture" aria-labelledby="arch-title">
          <h2 id="arch-title">Where each piece sits</h2>
          <div className="diagram" aria-label="TeachBack architecture">
            <ol className="pipe">
              <li>Phone camera</li>
              <li>Stabilized mat</li>
              <li>Locate Anything</li>
              <li>Procedure engine</li>
              <li>Coaching</li>
            </ol>
            <div className="branches">
              <p><strong>Procedure Library</strong> keeps named procedures in <strong>Tiger Data</strong>.</p>
              <p><strong>Learned steps</strong> go to <strong>Gemini</strong> for names, instructions, and questions about that procedure.</p>
            </div>
          </div>
        </section>

        <section id="why" aria-labelledby="why-title">
          <h2 id="why-title">Why it matters</h2>
          <ul className="why">
            {matters.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </section>

        <section aria-label="Technology">
          <h2 className="visually-hidden">Technology</h2>
          <ul className="badges">
            {tech.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </section>
      </main>

      <footer>
        <p className="brandline"><Mark /> TeachBack</p>
        <p><a href="https://teachonce.study">teachonce.study</a></p>
        <ul>
          {githubUrl && <li><a href={githubUrl}>GitHub</a></li>}
          {demoVideoUrl && <li><a href={demoVideoUrl}>Demo video</a></li>}
          {liveDemoUrl && <li><a href={liveDemoUrl}>Live demo</a></li>}
        </ul>
      </footer>
    </>
  );
}
