import { describe, expect, it } from "vitest";

import { folderAccentsByFile } from "./graphFolderColors";

describe("folderAccentsByFile", () => {
  it("shares colors only for the same immediate folder and ignores input order and duplicates", () => {
    const first = "file:///config/data/inputs.pfig";
    const second = "file:///config/data/readers.pfig";
    const nested = "file:///config/data/lake/train.pfig";
    const sameName = "file:///config/utils/data/readers.pfig";
    const files = [second, nested, first, sameName, nested];
    const colors = folderAccentsByFile(files);
    expect(colors.get(first)).toEqual(colors.get(second));
    expect(colors.get(first)).not.toEqual(colors.get(nested));
    expect(colors.get(first)).not.toEqual(colors.get(sameName));
    expect(folderAccentsByFile([...files].reverse())).toEqual(colors);
  });

  it("cycles through solid colors and contrasting pairs before repeating", () => {
    const files = Array.from({ length: 21 }, (_, i) => `file:///config/${String(i).padStart(2, "0")}/train.pfig`);
    const colors = [...folderAccentsByFile(files).values()];
    const pairs = colors.slice(8, 20);
    expect(colors.slice(0, 8).every(([a, b]) => a === b)).toBe(true);
    expect(pairs.every(([a, b]) => a !== b)).toBe(true);
    expect(new Set(colors.slice(0, 20).map((accent) => [...accent].sort().join(":"))).size).toBe(20);
    expect(colors[20]).toEqual(colors[0]);
    expect(pairs.some((accent) => accent.includes("#2eac86") && accent.includes("#9ab343"))).toBe(false);
  });
});
