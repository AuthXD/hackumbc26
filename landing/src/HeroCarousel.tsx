import { useEffect, useRef, useState, type KeyboardEvent } from "react";

const slides = [
  {
    id: "everyday",
    tab: "01 Everyday",
    label: "Show Everyday Procedure",
    title: "Everyday Procedure",
    caption: "Teach an arbitrary procedure using the objects already around you.",
    alt: "A blue water bottle, phones, headphones, a green smartwatch, and a brown wallet laid out on a black mat.",
    src: "/images/hero-everyday.webp",
    notes: ["Objects identified", "Order recorded", "Step 02 captured"],
  },
  {
    id: "toolbox",
    tab: "02 Toolbox",
    label: "Show Toolbox Handoff",
    title: "Toolbox Handoff",
    caption: "Learn a shutdown, inspection, or tool-return sequence from one demonstration.",
    alt: "A tape measure, safety glasses, screwdriver, pliers, and wrench laid out on a black mat.",
    src: "/images/hero-toolbox.webp",
    notes: ["Tools identified", "Sequence learned", "Wrong step caught"],
  },
  {
    id: "clinical",
    tab: "03 Clinical",
    label: "Show Clinical Training Tray",
    title: "Clinical Training Tray",
    caption: "Coach students through a simulated preparation sequence in the learned order.",
    alt: "Gloves, gauze, a bandage roll, sanitizer, and a stethoscope laid out on a black mat for training.",
    src: "/images/hero-clinical.webp",
    notes: ["Supplies identified", "Order recorded", "Guidance ready"],
    disclaimer: "Training assistance only. TeachBack does not provide medical judgment or certify sterility.",
  },
] as const;

function useReducedMotion() {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduce(query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);
  return reduce;
}

export function HeroCarousel() {
  const [index, setIndex] = useState(0);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [hidden, setHidden] = useState(false);
  const reduce = useReducedMotion();
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const touchX = useRef<number | null>(null);
  const slide = slides[index];
  const paused = reduce || hover || focus || hidden;

  useEffect(() => {
    const onVisibility = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (paused) return;
    const id = window.setTimeout(() => setIndex((current) => (current + 1) % slides.length), 6000);
    return () => window.clearTimeout(id);
  }, [index, paused]);

  function select(next: number, moveFocus = false) {
    const wrapped = (next + slides.length) % slides.length;
    setIndex(wrapped);
    if (moveFocus) tabs.current[wrapped]?.focus();
  }

  function onTabKey(event: KeyboardEvent) {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      select(index + 1, true);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      select(index - 1, true);
    }
  }

  return (
    <div
      className="carousel"
      role="region"
      aria-roledescription="carousel"
      aria-label="Three procedures taught with the same TeachBack engine"
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => setHover(false)}
      onFocus={() => setFocus(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocus(false);
      }}
    >
      <div
        className="frame"
        id="use-case-slide"
        role="tabpanel"
        aria-labelledby={`use-tab-${slide.id}`}
        onTouchStart={(event) => {
          touchX.current = event.changedTouches[0]?.clientX ?? null;
        }}
        onTouchEnd={(event) => {
          if (touchX.current == null) return;
          const delta = (event.changedTouches[0]?.clientX ?? touchX.current) - touchX.current;
          touchX.current = null;
          if (delta > 48) select(index - 1);
          else if (delta < -48) select(index + 1);
        }}
      >
        {slides.map((item, itemIndex) => (
          <div key={item.id} className={itemIndex === index ? "shot is-on" : "shot"} aria-hidden={itemIndex !== index}>
            <img
              src={item.src}
              alt={item.alt}
              width={1672}
              height={941}
              loading={itemIndex === 0 ? "eager" : "lazy"}
              decoding="async"
            />
            <ul className="notes">
              {item.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          </div>
        ))}
      </div>
      <div className="slide-copy">
        <p className="slide-title">{slide.title}</p>
        <p className="slide-caption">{slide.caption}</p>
        {"disclaimer" in slide && <p className="disclaimer">{slide.disclaimer}</p>}
      </div>
      <div className="use-tabs" role="tablist" aria-label="Procedure photographs" onKeyDown={onTabKey}>
        {slides.map((item, itemIndex) => (
          <button
            key={item.id}
            ref={(node) => { tabs.current[itemIndex] = node; }}
            id={`use-tab-${item.id}`}
            type="button"
            role="tab"
            aria-selected={itemIndex === index}
            aria-controls="use-case-slide"
            aria-label={item.label}
            tabIndex={itemIndex === index ? 0 : -1}
            onClick={() => select(itemIndex)}
          >
            {item.tab}
          </button>
        ))}
      </div>
    </div>
  );
}
