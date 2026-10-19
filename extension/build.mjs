import { build } from "esbuild";
import { cp, mkdir } from "node:fs/promises";

const port = Number(process.env.LEETOTTER_PORT ?? 8765);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("LEETOTTER_PORT must be a TCP port");
await mkdir("dist", { recursive: true });
await build({
  entryPoints: { worker: "src/worker.ts", panel: "src/panel.ts", "page-reader": "src/page-reader.ts", "page-bridge": "src/page-bridge.ts" },
  outdir: "dist",
  bundle: true,
  format: "iife",
  target: "chrome120",
  define: { __LEETOTTER_PORT__: String(port) },
});
await Promise.all([cp("manifest.json", "dist/manifest.json"), cp("panel.html", "dist/panel.html"), cp("panel.css", "dist/panel.css"),
  ...[16, 32, 48, 128].map(async (size) => { await mkdir("dist/icons", { recursive: true }); await cp(`icons/icon-${size}.png`, `dist/icons/icon-${size}.png`); })]);
