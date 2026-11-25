import type { GraphNodeVM } from "./types";

/** Status chips under the title. Collection nodes omit a redundant kind chip (title is List/Tuple/Dict). */
export function nodeBadgeLabels(kind: string, flags?: GraphNodeVM["flags"]): string[] {
  if (kind === "collection") {
    const b: string[] = [];
    if (flags?.isEntrypoint) b.push("entrypoint");
    if (flags?.isExternal) b.push("external");
    if (flags?.isReachable === false) b.push("unreachable");
    return b;
  }
  const b: string[] = [];
  if (kind === "fresh_instance") b.push("fresh instance");
  else if (kind === "inline_provider") b.push("inline");
  else b.push(kind);
  if (flags?.isEntrypoint) b.push("entrypoint");
  if (flags?.isExternal && kind !== "external") b.push("external");
  if (flags?.isReachable === false) b.push("unreachable");
  return b;
}
