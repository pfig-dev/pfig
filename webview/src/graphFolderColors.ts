import type { CSSProperties } from "react";

export type FolderAccent = readonly [stripe: string, fill: string];

const colors = {
  blue: "#4f86dd",
  teal: "#2eac86",
  amber: "#dd8b3b",
  violet: "#a77de0",
  olive: "#9ab343",
  pink: "#d968a4",
  cyan: "#39aabd",
  coral: "#d8665a",
} as const;
type Color = keyof typeof colors;

const solidAccents: FolderAccent[] = Object.values(colors).map((color) => [color, color]);
// Pair contrasting hues so the stripe and tinted fill read separately at graph zoom.
const contrastingPairs: readonly (readonly [Color, Color])[] = [
  ["olive", "violet"], ["pink", "teal"], ["amber", "blue"], ["coral", "cyan"],
  ["violet", "amber"], ["blue", "olive"], ["cyan", "pink"], ["teal", "coral"],
  ["amber", "teal"], ["pink", "olive"], ["blue", "coral"], ["cyan", "amber"],
];
const twoToneAccents: FolderAccent[] = contrastingPairs.map(([stripe, fill]) => [colors[stripe], colors[fill]]);
const accents = [...solidAccents, ...twoToneAccents];

export const folderAccentExamples = [solidAccents[0], twoToneAccents[0]];

export function folderAccentStyle([stripe, fill]: FolderAccent): CSSProperties {
  return { "--folder-stripe": stripe, "--folder-fill-color": fill } as CSSProperties;
}

function containingFolder(uri: string): string {
  return uri.slice(0, Math.max(0, uri.lastIndexOf("/")));
}

/** Assign solid accents to the largest folders first, then distinct two-tone pairs. */
export function folderAccentsByFile(uris: readonly string[]): Map<string, FolderAccent> {
  const files = [...new Set(uris)];
  const countByFolder = new Map<string, number>();
  for (const uri of files) {
    const folder = containingFolder(uri);
    countByFolder.set(folder, (countByFolder.get(folder) ?? 0) + 1);
  }
  const folders = [...countByFolder].sort(
    ([folderA, countA], [folderB, countB]) => countB - countA || folderA.localeCompare(folderB)
  );
  const byFolder = new Map(folders.map(([folder], i) => [folder, accents[i % accents.length]]));
  return new Map(files.map((uri) => [uri, byFolder.get(containingFolder(uri))!]));
}
