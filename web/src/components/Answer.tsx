import { useId, useState } from "react";

/** The model writes Markdown bold; show it as bold, and change nothing else in its words. */
function Bold({ text }: { text: string }) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return <>{parts.map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : part))}</>;
}

/** A run's final answer: two lines at first, the whole of it on request. */
export function Answer({ text, label = "Final answer" }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const long = text.length > 160 || text.includes("\n");
  return (
    <blockquote className="answer">
      <p className="answer-label">{label}</p>
      <p id={id} className={`answer-text${open || !long ? "" : " is-clamped"}`}><Bold text={text} /></p>
      {long && (
        <button type="button" className="text-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show the whole answer"}
        </button>
      )}
    </blockquote>
  );
}
