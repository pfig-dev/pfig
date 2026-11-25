import type { BindingEdgeDetails } from "./graphEdgeBundles";

export function EdgeReferenceDetails({ details, selected }: {
  details: BindingEdgeDetails[];
  selected: boolean;
}) {
  return (
    <aside
      className={`pfig-edge-reference-details${selected ? " is-selected nowheel nopan" : ""}`}
      role={selected ? "region" : "tooltip"}
      aria-label={selected ? "Edge references" : undefined}
    >
      {details.map(({ wiring, summary }) => (
        <div key={wiring}>
          <strong><code>{wiring}</code></strong>
          <div className="pfig-edge-reference-summary">{summary}</div>
        </div>
      ))}
    </aside>
  );
}
