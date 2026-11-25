import { Handle, Position } from "reactflow";

import { folderAccentStyle } from "./graphFolderColors";
import type { OverviewGroup } from "./graphOverview";
import { NodeToolbar } from "./NodeToolbar";
import { useNodeSingleClick } from "./useNodeSingleClick";
import type { FocusDirection } from "./graphFocus";
import { DiffTotals } from "./GraphDiffControls";

export type OverviewCardData = OverviewGroup & {
  estimatedWidth: number;
  estimatedHeight: number;
  expand: (groupId: string) => void;
  collapse: (groupId: string) => void;
  soloDirection: FocusDirection | null;
  toggleSolo: (groupId: string, direction: FocusDirection, expanded: boolean) => void;
};

export function OverviewGroupNode({ data }: { data: OverviewCardData }) {
  const {
    id, label, kind, entryFile, expanded, cardCount, overrideCount, expand, collapse, soloDirection, toggleSolo,
  } = data;
  const singleClick = useNodeSingleClick();
  const split = label.lastIndexOf("/");
  const directory = split < 0 ? "" : label.slice(0, split);
  const name = label.slice(split + 1);
  const toolbar = (
    <NodeToolbar
      label={label}
      active={soloDirection}
      onToggle={(direction) => toggleSolo(id, direction, expanded)}
      collapse={expanded ? (
        <button
          className="pfig-overview-icon"
          type="button"
          aria-label={`Collapse ${label}`}
          title="Collapse file"
          onClick={(event) => {
            event.stopPropagation();
            collapse(id);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") event.stopPropagation();
          }}
        >
          <span aria-hidden="true">−</span>
        </button>
      ) : null}
    />
  );
  const appearance = {
    width: data.estimatedWidth,
    height: data.estimatedHeight,
    ...(data.folderAccent ? folderAccentStyle(data.folderAccent) : {}),
  };
  return (
    <div
      className={`pfig-overview-card${data.folderAccent ? " pfig-folder-accent" : ""}${expanded ? " pfig-overview-card--expanded" : ""}`}
      data-kind={kind}
      data-entry-file={entryFile || undefined}
      style={appearance}
    >
      {!expanded && <Handle type="target" position={Position.Left} id="kw-__binding__" />}
      <div
        className={expanded ? "pfig-overview-header" : "pfig-overview-summary nodrag nopan"}
        title={label}
        onClick={expanded ? undefined : (event) => singleClick(event, () => expand(id))}
      >
        {directory && <strong className="pfig-overview-directory" title={`${directory}/`}>{directory}/</strong>}
        <div className="pfig-node-title">
          {expanded ? (
            <strong className="pfig-overview-name" title={label}>{name}</strong>
          ) : (
            <button
              className="pfig-overview-name pfig-overview-expand"
              type="button"
              title={label}
              aria-label={`Expand ${label}`}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") event.stopPropagation();
              }}
            >
              {name}
            </button>
          )}
          {toolbar}
        </div>
        <span className="pfig-overview-count">
          {entryFile ? "Entry file · " : ""}{cardCount} {cardCount === 1 ? "card" : "cards"}
          {overrideCount > 0 ? ` · ${overrideCount} overridden` : ""}
          {data.diffCounts && <> · <DiffTotals counts={data.diffCounts} /></>}
        </span>
      </div>
      {!expanded && <Handle type="source" position={Position.Right} id="out" />}
    </div>
  );
}
