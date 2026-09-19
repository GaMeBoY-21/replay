// Copy the two self-hosted faces DESIGN.md names into src/fonts, Latin subset only.
// The demo must run with the network unreachable, so nothing is fetched at runtime.
import { copyFileSync, mkdirSync } from "node:fs";
const faces = [
  ["archivo", ["400", "500", "600", "700"]],
  ["ibm-plex-mono", ["400", "500", "600"]],
];
mkdirSync("src/fonts", { recursive: true });
for (const [face, weights] of faces) {
  for (const w of weights) {
    const name = `${face}-latin-${w}-normal.woff2`;
    copyFileSync(`node_modules/@fontsource/${face}/files/${name}`, `src/fonts/${name}`);
    console.log(`src/fonts/${name}`);
  }
}
