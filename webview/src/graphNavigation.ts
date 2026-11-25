import type { GraphNodeVM } from "./types";

/** Resolve qualified paths to their canonical card after namespace compaction. */
export function findNavigationNode(nodes: readonly GraphNodeVM[], targetId: string): GraphNodeVM | undefined {
  return nodes.find((node) => node.id === targetId || node.pathAliases?.includes(targetId));
}
