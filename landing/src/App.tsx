import { demoVideoUrl, githubUrl, liveDemoUrl } from "./links";

const steps = [
  ["01", "Describe the visible objects", "Name what is on the table. The procedure is not chosen in advance."],
  ["02", "Demonstrate the procedure", "Do the work once, in the order it should be learned."],
  ["03", "TeachBack records settled changes", "A step is the change that remains after the hands leave."],
  ["04", "Practice the learned order", "The next person is checked against that sequence."],
  ["05", "Correct mistakes and continue", "A skip, the wrong object, or the wrong place stops the sequence until it is fixed."],
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
  const demoHref = demoVideoUrl ?? "#how";

  return (
    <>
      <a className="skip" href="#content">Skip to content</a>
      <header className="mast">
        <div className="mast-inner">
          <a className="brand" href="#content">
            <Mark />
            <span className="brand-name">TeachBack</span>
            <span className="brand-rule">Teach once / Coach every time</span>
          </a>
          <nav aria-label="Page">
            <a href="#how">How it works</a>
            <a href="#uses">Use cases</a>
            <a href="#architecture">Architecture</a>
            {githubUrl && <a href={githubUrl}>GitHub</a>}
          </nav>
        </div>
      </header>

      <main id="content">
        <section className="hero">
          <div className="hero-copy">
            <h1><span>Show the work.</span><span>Keep the</span><span>know-how.</span></h1>
            <p className="lede">
              TeachBack turns one physical demonstration into step-by-step coaching—without programming the workflow.
            </p>
            <a className="cta" href={demoHref}>
              Watch the demo <span aria-hidden="true">↗</span>
            </a>
            <a className="text-link" href="#architecture">Explore the system <span aria-hidden="true">↓</span></a>
          </div>
          <figure className="hero-figure">
            <div className="photo">
              <img
                src="/images/hero.webp"
                alt="A real tabletop: blue water bottle, phones, headphones, a green smartwatch, and a brown wallet on a black mat."
                width={1800}
                height={917}
              />
              <ul className="annos">
                <li className="paper">Objects identified</li>
                <li className="paper">Order recorded</li>
                <li className="lime">Step 02 captured</li>
              </ul>
            </div>
            <figcaption>
              Labels on the photograph describe the product. They are not a live scan.
            </figcaption>
          </figure>
        </section>

        <section className="strip" aria-labelledby="strip-title">
          <h2 id="strip-title">One demonstration<br />becomes a reusable<br />procedure.</h2>
          <ol>
            <li>
              <span>01</span>
              <h3>Show</h3>
              <p>Perform the procedure once, naturally.</p>
            </li>
            <li>
              <span>02</span>
              <h3>Learn</h3>
              <p>TeachBack watches and structures the ordered changes.</p>
            </li>
            <li>
              <span>03</span>
              <h3>Practice</h3>
              <p>The next person receives real-time, step-by-step coaching.</p>
            </li>
          </ol>
        </section>

        <section className="how" id="how" aria-labelledby="how-title">
          <h2 id="how-title">How it works</h2>
          <ol>
            {steps.map(([num, title, body]) => (
              <li key={num}>
                <span>{num}</span>
                <div>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="uses" id="uses" aria-labelledby="uses-title">
          <h2 id="uses-title">One engine. Different procedures.</h2>
          <p className="uses-note">Both are the same learned sequence. Neither is a separate mode.</p>
          <article>
            <p className="kicker">Toolbox handoff / 01</p>
            <p>
              Teach a repeatable shutdown, inspection, or tool-return procedure. Catch skipped steps,
              incorrect tools, and actions performed out of order.
            </p>
          </article>
          <article>
            <p className="kicker">Clinical training tray / 02</p>
            <p>Teach students a simulated tray-preparation sequence and coach them through the learned order.</p>
            <p className="disclaimer">TeachBack supports training. It does not provide medical judgment or certify sterility.</p>
          </article>
        </section>

        <section className="trust" aria-labelledby="trust-title">
          <div className="trust-inner">
            <h2 id="trust-title">AI explains.<br />Deterministic vision decides.</h2>
            <dl>
              <div>
                <dt>Locate Anything</dt>
                <dd>Identifies the physical objects described by the user.</dd>
              </div>
              <div>
                <dt>Procedure engine</dt>
                <dd>Compares observed state changes against the learned order.</dd>
              </div>
              <div>
                <dt>Gemini</dt>
                <dd>Names procedures, improves instructions, and answers from stored steps.</dd>
              </div>
              <div>
                <dt>Tiger Data</dt>
                <dd>Stores procedures, setup checks, and readiness history.</dd>
              </div>
            </dl>
            <p className="rule">Gemini never decides whether a physical action passed.</p>
          </div>
        </section>

        <section className="arch" id="architecture" aria-labelledby="arch-title">
          <h2 id="arch-title">Architecture</h2>
          <div className="pipe" aria-label="Frame path">
            <ol>
              <li>Phone camera</li>
              <li>Stabilized mat</li>
              <li>Locate Anything</li>
              <li>Procedure engine</li>
              <li>Coaching</li>
            </ol>
          </div>
          <div className="branches">
            <p><span>Procedure library</span> → Tiger Data</p>
            <p><span>Learned steps</span> → Gemini</p>
          </div>
        </section>
      </main>

      <footer>
        <div className="foot-inner">
          <p className="foot-brand"><Mark /> TeachBack</p>
          <p>Teach once. Coach every time.</p>
          <p><a href="https://teachonce.study">teachonce.study</a></p>
          <ul>
            {githubUrl && <li><a href={githubUrl}>GitHub</a></li>}
            {demoVideoUrl && <li><a href={demoVideoUrl}>Demo video</a></li>}
            {liveDemoUrl && <li><a href={liveDemoUrl}>Live demo</a></li>}
          </ul>
        </div>
      </footer>
    </>
  );
}
