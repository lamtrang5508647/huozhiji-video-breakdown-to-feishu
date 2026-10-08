import path from "node:path";
import { TEMP_DIR_NAME } from "./policies.js";

// Curated from upstream: only this cache-path helper is used by downloader.js.
// Resume/state scanning and recursive cache deletion are excluded.
export function getTempDir(outputDir) {
  return path.join(outputDir, TEMP_DIR_NAME);
}