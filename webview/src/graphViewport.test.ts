import { describe, expect, it } from "vitest";
import { anchorViewport } from "./graphViewport";

describe("anchorViewport", () => {
  it("preserves zoom and screen position when a file moves", () => {
    const before = [{ id: "file", position: { x: 80, y: 100 } }];
    const after = [{ id: "file", position: { x: 140, y: 75 } }];
    expect(anchorViewport({ x: -50, y: 80, zoom: 1.5 }, before, after, "file"))
      .toEqual({ x: -140, y: 117.5, zoom: 1.5 });
  });

  it("accounts for both the binding and its containing file moving", () => {
    const before = [
      { id: "file", position: { x: 90, y: 180 } },
      { id: "binding", parentNode: "file", position: { x: 15, y: 50 } },
    ];
    const after = [
      { id: "file", position: { x: 40, y: 210 } },
      { id: "binding", parentNode: "file", position: { x: 45, y: 80 } },
    ];
    const viewport = { x: 10, y: 20, zoom: 0.5 };
    expect(anchorViewport(viewport, before, after, "binding")).toEqual({ x: 20, y: -10, zoom: 0.5 });
    expect(anchorViewport(viewport, before, after, "missing")).toBe(viewport);
  });
});
