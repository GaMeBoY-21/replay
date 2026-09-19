import { useId, useState } from "react";

/** A run's final answer: two lines at first, the whole of it on request. */
export function Answer({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const long = text.length > 160 || text.includes("\n");
  return (
    <blockquote className="answer">
      <p className="answer-label">Final answer</p>
      <p id={id} className={`answer-text${open || !long ? "" : " is-clamped"}`}>{text}</p>
      {long && (
        <button type="button" className="text-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show the whole answer"}
        </button>
      )}
    </blockquote>
  );
}
