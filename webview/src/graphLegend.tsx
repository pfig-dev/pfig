import type { CSSProperties } from "react";
import { folderAccentExamples, folderAccentStyle } from "./graphFolderColors";
import { OVERVIEW_LEGEND } from "./graphOverview";
import { GRAPH_LEGEND } from "./graphNodeTheme";

const GRAPH_TEXT_PX = 12;

const panelStyle: CSSProperties = {
  margin: 6,
  padding: "5px 7px",
  maxWidth: 200,
  fontSize: GRAPH_TEXT_PX,
  lineHeight: 1.35,
  color: "var(--pfig-fg, #ccc)",
  background: "var(--pfig-bar)",
  border: "1px solid var(--pfig-border)",
  borderRadius: 4,
  userSelect: "none",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  marginTop: 4,
};

const firstRowStyle: CSSProperties = { ...rowStyle, marginTop: 0 };

const titleStyle: CSSProperties = {
  fontWeight: 600,
  fontSize: GRAPH_TEXT_PX,
  marginBottom: 2,
  opacity: 0.95,
};

function NodeSwatch(p: { palette: (typeof GRAPH_LEGEND.nodes)[number]["palette"] }) {
  const { border, background, borderStyle } = p.palette;
  const swatchBorder =
    borderStyle === "solid"
      ? `1px solid ${border}`
      : `2px ${borderStyle} ${border}`;
  return (
    <span
      style={{
        flexShrink: 0,
        width: 16,
        height: 10,
        borderRadius: 2,
        border: swatchBorder,
        background,
        boxSizing: "border-box",
      }}
      aria-hidden
    />
  );
}

function EdgeSample(p: { stroke: string; dashed: boolean }) {
  return (
    <svg width={22} height={11} aria-hidden style={{ flexShrink: 0 }}>
      <line
        x1={2}
        y1={5.5}
        x2={20}
        y2={5.5}
        stroke={p.stroke}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeDasharray={p.dashed ? "2 4" : undefined}
      />
    </svg>
  );
}

/** Shared legend rows (panel overlay + PNG export node). */
export function LegendContent() {
  return (
    <>
      <div style={titleStyle}>Legend</div>
      <div style={{ opacity: 0.75, fontSize: GRAPH_TEXT_PX, marginBottom: 2 }}>Nodes</div>
      {GRAPH_LEGEND.nodes.map((item, i) => (
        <div key={item.key} style={i === 0 ? firstRowStyle : rowStyle}>
          <NodeSwatch palette={item.palette} />
          <span>{item.label}</span>
        </div>
      ))}
      <div style={{ ...titleStyle, marginTop: 8 }}>Edges</div>
      {GRAPH_LEGEND.edges.map((item, i) => (
        <div key={item.key} style={i === 0 ? firstRowStyle : rowStyle}>
          <EdgeSample stroke={item.stroke} dashed={item.dashed} />
          <span>{item.label}</span>
        </div>
      ))}
    </>
  );
}

export function FileLegendContent() {
  return (
    <section className="pfig-file-legend" aria-label="pfig files">
      <div style={titleStyle}>pfig files</div>
      <div style={firstRowStyle}>
        {folderAccentExamples.map((accent) => (
          <span
            key={accent.join(":")}
            className="pfig-folder-accent pfig-folder-swatch"
            style={folderAccentStyle(accent)}
            aria-hidden
          />
        ))}
        <span>Stripe and fill mark the folder.</span>
      </div>
      <div className="pfig-overview-key">{OVERVIEW_LEGEND}</div>
    </section>
  );
}

export function GraphLegend({ overview }: { overview: boolean }) {
  return (
    <div style={panelStyle} className="pfig-graph-legend">
      <LegendContent />
      {overview ? <FileLegendContent /> : null}
    </div>
  );
}
