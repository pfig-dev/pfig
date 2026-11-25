import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import type { Plugin } from "vite";

// Rollup exposes the package modules included in each output chunk. Copy the
// original notices, including copyright lines, instead of using SPDX labels alone.
export function thirdPartyNotices(): Plugin {
  return {
    name: "pfig-third-party-notices",
    generateBundle(_options, bundle) {
      const roots = new Set<string>();
      for (const chunk of Object.values(bundle)) {
        if (chunk.type !== "chunk") continue;
        for (const id of Object.keys(chunk.modules)) {
          const path = id.replace(/^\0/, "").replace(/\\/g, "/");
          const index = path.lastIndexOf("/node_modules/");
          if (index < 0) continue;
          const parts = path.slice(index + "/node_modules/".length).split("/");
          const name = parts.slice(0, parts[0].startsWith("@") ? 2 : 1).join("/");
          const root = path.slice(0, index) + "/node_modules/" + name;
          if (existsSync(join(root, "package.json"))) roots.add(root);
        }
      }

      const sections = [...roots].map((root) => {
        const { name, version, license } = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
        const files = readdirSync(root).filter((file) => /^(licen[cs]e|copying|notice)(\.|$)/i.test(file)).sort();
        if (!files.length) this.error(`No license or notice file found for ${name}@${version}`);
        const header = [`${name}@${version} (${license})`];
        if (name === "elkjs") {
          // ELK is compiled into elkjs, so it is not a separate Rollup module.
          if (version !== "0.9.3") this.error("Update ELK source references for the new elkjs release");
          header.push(
            "The elkjs and Eclipse Layout Kernel source code is available under EPL-2.0:",
            "https://github.com/kieler/elkjs/tree/a8304cf79fde75bc2ab1a89d28320f53f8637436",
            "https://github.com/eclipse-elk/elk/tree/7ca51784e42a24201f29bc13e458728b6fc61cdc",
            "The upstream release notes describe the ELK version and patch in this distribution:",
            "https://github.com/kieler/elkjs/releases/tag/0.9.3",
          );
        }
        return [
          header.join("\n"),
          ...files.map((file) => `${file}\n${readFileSync(join(root, file), "utf8").trimEnd()}`),
        ].join("\n\n");
      }).sort();

      this.emitFile({
        type: "asset",
        fileName: "THIRD_PARTY_NOTICES.txt",
        source: [
          "Third-party notices for the pfig graph viewer\n\nThe bundled components below retain their respective licenses.",
          ...sections,
        ].join("\n\n------------------------------------------------------------------------\n\n") + "\n",
      });
    },
  };
}
