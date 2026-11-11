// env.mjs (cloud copy) — secrets come ONLY from process.env, which app.py fills from Streamlit secrets. No files, no fallbacks.
import os from "node:os";
import path from "node:path";
export const env = new Proxy({}, { get: (_, k) => (typeof k === "string" ? (process.env[k] ?? "") : undefined) });
export const RUNS_DIR = process.env.JOBENGINE_RUNS_DIR || path.join(os.tmpdir(), "scout-runs");
export const isServer = true;
