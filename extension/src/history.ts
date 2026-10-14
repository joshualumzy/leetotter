import { validateImportUrls } from "./protocol";

export function readHistoryCache(raw: string | null): string[] {
  if (raw === null) throw new Error("synced-progress-cache is missing");
  let cache: unknown;
  try { cache = JSON.parse(raw); }
  catch { throw new Error("synced-progress-cache is not valid JSON"); }
  const completed = (cache && typeof cache === "object" && !Array.isArray(cache)) ? (cache as Record<string, unknown>).completed : undefined;
  if (!completed || typeof completed !== "object" || Array.isArray(completed) || !Object.values(completed).every(value => Array.isArray(value) && value.every(url => typeof url === "string"))) {
    throw new Error("synced-progress-cache completed must map categories to URL lists");
  }
  return validateImportUrls(Object.values(completed).flat() as string[]);
}
