import type { ReactNode } from "react";
import type { RunStatus } from "../api/types";

export function Loading({ what }: { what: string }) {
  return (
    <div className="state state-loading" role="status" aria-live="polite">
      <span className="state-bar" aria-hidden="true" />
      Loading {what}…
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children && <div className="state-body">{children}</div>}
    </div>
  );
}

export function Failure({ title, message, children }: { title: string; message: string; children?: ReactNode }) {
  return (
    <div className="state state-error" role="alert">
      <p className="state-title">{title}</p>
      <p className="state-body mono">{message}</p>
      {children}
    </div>
  );
}

const STATUS_TEXT: Record<RunStatus, string> = {
  completed: "completed",
  running: "running",
  failed: "failed",
  tripped: "halted by a breaker",
};

export function StatusChip({ status }: { status: RunStatus }) {
  return <span className={`chip chip-${status}`}>{STATUS_TEXT[status] ?? status}</span>;
}
