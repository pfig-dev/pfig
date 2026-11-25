import type { GraphNodeVM } from "./types";

export function BindingValuePreview({ binding }: { binding: NonNullable<GraphNodeVM["binding"]> }) {
  const sequence = binding.valueSequence;
  if (!sequence) {
    return (
      <div className="pfig-value-preview">
        <div style={{ fontFamily: "monospace", wordBreak: "break-all" }}>{binding.valuePreview}</div>
      </div>
    );
  }

  const remaining = sequence.count - sequence.items.length;
  const label = `${sequence.kind === "list" ? "List" : "Tuple"} · ${sequence.count} ${sequence.count === 1 ? "item" : "items"}`;
  return (
    <div className="pfig-value-preview">
      <strong>{label}</strong>
      {sequence.items.length > 0 ? (
        <ol className="pfig-value-items" aria-label={label}>
          {sequence.items.map((item, index) => (
            <li key={index}>
              {"callee" in item ? (
                <>
                  <code className="pfig-value-reference" data-graph-target={item.targetId} title={`@${item.targetId}`}>
                    {item.callee}({item.kwargs.length ? "…" : ""})
                  </code>
                  {item.kwargs.map(({ name, text }) => (
                    <div key={name} className="pfig-value-call-argument">
                      <code><span>{name}</span> = {text}</code>
                    </div>
                  ))}
                </>
              ) : <code>{item.text}</code>}
            </li>
          ))}
        </ol>
      ) : null}
      {remaining > 0 ? <div className="pfig-value-more">{remaining} more</div> : null}
    </div>
  );
}
