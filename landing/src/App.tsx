import { useEffect, useState } from "react";
import { demoVideoUrl, githubUrl, liveDemoUrl } from "./links";

const steps = [
  ["01", "Describe the visible objects", "Name what is on the table. The procedure is not chosen in advance."],
  ["02", "Demonstrate the procedure", "Do the work once, in the order it should be learned."],
  ["03", "TeachBack records settled changes", "A step is the change that remains after the hands leave."],
  ["04", "Practice the learned order", "The next person is checked against that sequence."],
  ["05", "Correct mistakes and continue", "A skip, the wrong object, or the wrong place stops the sequence until it is fixed."],
];

const slides = [
  {
    id: "everyday",
    label: "Everyday handoff",
    title: "Teach any visible routine",
    image: "/images/everyday.jpg",
    alt: "Water bottle, headphones, phones, smartwatch, wallet, and keys arranged on a black mat.",
    annotations: ["Objects identified", "Order recorded", "Step 02 captured"],
  },
  {
    id: "toolbox",
    label: "Toolbox workflow",
    title: "Return every tool in order",
    image: "/images/toolbox.jpg",
    alt: "Tape measure, safety glasses, screwdriver, pliers, and adjustable wrench arranged on a black mat.",
    annotations: ["Five tools tracked", "Sequence learned", "Missing step caught"],
  },
  {
    id: "clinical",
    label: "Clinical training",
    title: "Practice a tray setup safely",
    image: "/images/clinical.jpg",
    alt: "Gloves, sterile pad, bandage roll, sanitizer, and stethoscope arranged on a black mat.",
    annotations: ["Training objects found", "Setup preserved", "Order coached"],
  },
] as const;

type SlideIndex = 0 | 1 | 2;

const slideIndices = [0, 1, 2] as const;
const nextSlide: Record<SlideIndex, SlideIndex> = { 0: 1, 1: 2, 2: 0 };
const previousSlide: Record<SlideIndex, SlideIndex> = { 0: 2, 1: 0, 2: 1 };

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
  const localAppUrl = "http://localhost:5173";
  const [activeSlide, setActiveSlide] = useState<SlideIndex>(0);
  const [interactionPaused, setInteractionPaused] = useState(false);
  const [rotationPaused, setRotationPaused] = useState(false);
  const [pageVisible, setPageVisible] = useState(!document.hidden);

  useEffect(() => {
    const onVisibilityChange = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (interactionPaused || rotationPaused || !pageVisible || reducedMotion) return;

    const timer = window.setInterval(() => {
      setActiveSlide((current) => nextSlide[current]);
    }, 5200);
    return () => window.clearInterval(timer);
  }, [interactionPaused, pageVisible, rotationPaused]);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) return;

    const root = document.documentElement;
    const sections = [...document.querySelectorAll<HTMLElement>("[data-reveal]")];
    root.classList.add("has-reveal");

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -12%", threshold: 0.12 },
    );

    for (const section of sections) observer.observe(section);
    return () => {
      observer.disconnect();
      root.classList.remove("has-reveal");
    };
  }, []);

  const slide = slides[activeSlide];

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
            {githubUrl && <a href={githubUrl}>GitHub</a>}
            <a className="open-app" href={localAppUrl} target="_blank" rel="noreferrer">Open local app <span aria-hidden="true">↗</span></a>
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
            <a className="text-link" href="#how">See how it works <span aria-hidden="true">↓</span></a>
          </div>
          <figure
            className="hero-figure"
            aria-roledescription="carousel"
            aria-label="TeachBack use cases"
            onPointerEnter={() => setInteractionPaused(true)}
            onPointerLeave={() => setInteractionPaused(false)}
            onFocusCapture={() => setInteractionPaused(true)}
            onBlurCapture={(event) => {
              if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
                setInteractionPaused(false);
              }
            }}
          >
            <div className="photo">
              <img
                key={slide.id}
                src={slide.image}
                alt={slide.alt}
                width={1792}
                height={1024}
              />
              <ul className="annos">
                {slide.annotations.map((annotation, index) => (
                  <li className={index === 2 ? "lime" : "paper"} key={annotation}>{annotation}</li>
                ))}
              </ul>
            </div>
            <div className="carousel-meta">
              <figcaption><span>{slide.label}</span> — {slide.title}</figcaption>
              <div className="carousel-controls" aria-label="Choose a use case">
                <button type="button" onClick={() => setActiveSlide(previousSlide[activeSlide])} aria-label="Previous use case">←</button>
                {slideIndices.map((index) => {
                  const item = slides[index];
                  return (
                  <button
                    type="button"
                    className={index === activeSlide ? "active" : ""}
                    aria-label={`Show ${item.label}`}
                    aria-pressed={index === activeSlide}
                    onClick={() => setActiveSlide(index)}
                    key={item.id}
                  >
                    {String(index + 1).padStart(2, "0")}
                  </button>
                  );
                })}
                <button type="button" onClick={() => setActiveSlide(nextSlide[activeSlide])} aria-label="Next use case">→</button>
                <button
                  type="button"
                  className="pause-control"
                  aria-pressed={rotationPaused}
                  onClick={() => setRotationPaused((paused) => !paused)}
                >
                  {rotationPaused ? "Play" : "Pause"}
                </button>
              </div>
            </div>
          </figure>
        </section>

        <section className="strip" aria-labelledby="strip-title" data-reveal>
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

        <section className="how" id="how" aria-labelledby="how-title" data-reveal>
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

        <section className="uses" id="uses" aria-labelledby="uses-title" data-reveal>
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

        <section className="trust" aria-labelledby="trust-title" data-reveal>
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
            <p className="rule"><span>Verification rule</span> Gemini explains the procedure; it never decides whether a physical action passed.</p>
          </div>
        </section>
      </main>

      <footer>
        <div className="foot-inner">
          <p className="foot-brand"><Mark /> TeachBack</p>
          <p>Teach once. Coach every time.</p>
          <p><a href="https://teachonce.study">teachonce.study</a></p>
          <ul>
            <li><a href={localAppUrl} target="_blank" rel="noreferrer">Open local app</a></li>
            {githubUrl && <li><a href={githubUrl}>GitHub</a></li>}
            {demoVideoUrl && <li><a href={demoVideoUrl}>Demo video</a></li>}
            {liveDemoUrl && <li><a href={liveDemoUrl}>Live demo</a></li>}
          </ul>
        </div>
      </footer>
    </>
  );
}
