import { createContext } from "react";
import type { DiffCounts, GraphComparison } from "./types";

export const GraphDiffActions = createContext<{
  locate: (id: string) => void;
  hide: (id: string) => void;
  removedIds: ReadonlySet<string>;
} | null>(null);

export function DiffTotals({ counts }: { counts: DiffCounts }) {
  return (
    <span className="pfig-diff-totals">
      <span data-diff-status="added" aria-label={`${counts.added} added`}>+{counts.added}</span>{" "}
      <span data-diff-status="modified" aria-label={`${counts.modified} modified`}>~{counts.modified}</span>{" "}
      <span data-diff-status="removed" aria-label={`${counts.removed} removed`}>−{counts.removed}</span>
    </span>
  );
}

export function GraphDiffControls({
  diff, visible, showingAll, show, hide, locate,
}: {
  diff: GraphComparison;
  visible: ReadonlySet<string>;
  showingAll: boolean;
  show: () => void;
  hide: () => void;
  locate: (id: string) => void;
}) {
  const label = diff.proposedLabel.split(/[/\\]/).pop() || diff.proposedLabel;
  const hidden = diff.removals.filter((r) => !r.aliases.every((id) => visible.has(id))).length;
  const anyChanges = Object.values(diff.counts).some(Boolean);
  const remainingWires = !showingAll && diff.baseOnlyEdges.length > 0;
  return (
    <div className="pfig-diff-controls" aria-label="Plan comparison">
      <strong title={`Proposed: ${diff.proposedLabel}\nBase: ${diff.baseLabel}`}>Proposed: {label}</strong>
      {diff.namespaceAlignment && <span>Aligned: {diff.namespaceAlignment}</span>}
      {!anyChanges ? <span>No plan changes</span> : (
        <>
          <span data-diff-status="added" aria-label={`${diff.counts.added} added`}>+{diff.counts.added}</span>
          <span data-diff-status="modified" aria-label={`${diff.counts.modified} modified`}>~{diff.counts.modified}</span>
          {diff.removals.length ? (
            <select
              aria-label={`Locate removal (${diff.counts.removed} removed)`}
              data-diff-status="removed"
              value=""
              onChange={(e) => locate(e.target.value)}
            >
              <option value="" disabled>−{diff.counts.removed}</option>
              {diff.removals.map(({ id, label, fileUri }) => (
                <option key={id} value={id} title={fileUri ?? undefined}>{label ?? id}</option>
              ))}
            </select>
          ) : <span aria-label="0 removed">−0</span>}
          {hidden > 0 ? <button onClick={show}>Show removed ({hidden})</button> :
            remainingWires ? <button onClick={show}>Show old wiring</button> : null}
          {visible.size > 0 || showingAll ? (
            <button onClick={hide}>{diff.removals.length ? "Hide removed" : "Hide old wiring"}</button>
          ) : null}
        </>
      )}
    </div>
  );
}
