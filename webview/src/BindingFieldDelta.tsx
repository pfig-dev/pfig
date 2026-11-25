import { useContext } from "react";
import { GraphDiffActions } from "./GraphDiffControls";
import type { FieldDelta, FieldSide, FieldStep } from "./types";

function Side({ side, old }: { side: FieldSide; old?: boolean }) {
  const actions = useContext(GraphDiffActions);
  if (!side.present) return null;
  const target = side.targetId;
  const style = old ? "pfig-diff-before" : "pfig-diff-after";
  if (old && target && actions?.removedIds.has(target)) {
    return (
      <button
        type="button"
        className={`${style} pfig-diff-action pfig-diff-link nodrag nopan`}
        title={side.title}
        aria-label={`Reveal removed binding ${target}`}
        onClick={(event) => { event.stopPropagation(); actions.locate(target); }}
        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") event.stopPropagation(); }}
      >
        {side.preview}
      </button>
    );
  }
  return <span className={style} title={side.title} data-graph-target={old ? undefined : target}>{side.preview}</span>;
}

function fieldPath(path: readonly FieldStep[]): string {
  return path.map(([kind, key]) => {
    if (kind === "arg") return `#${Number(key) + 1}`;
    if (kind === "item" || kind === "key") return `[${key}]`;
    if (kind === "kwarg") return key;
    return kind === "freshBase" ? "base provider" : kind;
  }).join(" › ");
}

export function BindingFieldDelta({ field }: { field: FieldDelta }) {
  const render = (delta: FieldDelta) => (
    <span className="pfig-diff-values">
      {delta.before.present && <Side side={delta.before} old />}
      {delta.before.present && delta.after.present
        ? <span aria-hidden="true">→</span>
        : !delta.before.present && delta.after.present
          ? <span className="pfig-diff-after" aria-label="Added">+</span>
          : null}
      {delta.after.present && <Side side={delta.after} />}
    </span>
  );
  return (
    <span className="pfig-field-delta">
      {render(field)}
      {field.nested?.map((delta) => (
        <span key={JSON.stringify(delta.path)} className="pfig-diff-nested">
          <span className="pfig-diff-field-name">{fieldPath(delta.path.slice(1))}</span>
          {render(delta)}
        </span>
      ))}
    </span>
  );
}
