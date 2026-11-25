import { memo } from "react";

import {
  BINDING_NODE_WIDTH,
  EXPORT_LEGEND_FLOW_HEIGHT,
  OVERVIEW_LEGEND_FLOW_HEIGHT,
} from "./graphBindingConstants";
import { FileLegendContent, LegendContent } from "./graphLegend";
import { GRAPH_LEGEND } from "./graphNodeTheme";

const valuePalette = GRAPH_LEGEND.nodes.find((x) => x.key === "value")!.palette;

/**
 * PNG export only: same card width, padding, font size, and border treatment as binding nodes;
 * transparent fill so the graph shows through.
 */
export const LegendExportNode = memo(function LegendExportNode({ data }: { data: { overview?: boolean } }) {
  const cardBorder =
    valuePalette.borderStyle === "solid"
      ? `1px solid ${valuePalette.border}`
      : `2px ${valuePalette.borderStyle} ${valuePalette.border}`;

  return (
    <div
      className="binding-node binding-node--value"
      style={{
        width: BINDING_NODE_WIDTH,
        minWidth: BINDING_NODE_WIDTH,
        maxWidth: BINDING_NODE_WIDTH,
        boxSizing: "border-box",
        border: cardBorder,
        borderRadius: 6,
        background: "transparent",
        color: "var(--pfig-fg, #ccc)",
        fontSize: 12,
        padding: "6px 8px",
        minHeight: data.overview ? OVERVIEW_LEGEND_FLOW_HEIGHT : EXPORT_LEGEND_FLOW_HEIGHT,
      }}
    >
      {data.overview ? <FileLegendContent /> : <LegendContent />}
    </div>
  );
});
