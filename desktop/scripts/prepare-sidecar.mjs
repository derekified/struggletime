import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, chmodSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const args = process.argv.slice(2);
const option = (name) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
};
const profile = option("--profile") ?? "debug";
if (!["debug", "release"].includes(profile)) {
  throw new Error("--profile must be debug or release");
}

const host = execFileSync("rustc", ["-vV"], { encoding: "utf8" })
  .match(/^host:\s*(.+)$/m)?.[1]
  ?.trim();
if (!host) throw new Error("Could not determine the Rust host target");
const target = option("--target") ?? process.env.TAURI_TARGET ?? process.env.CARGO_BUILD_TARGET ?? host;
const windows = target.includes("windows");
const executable = `struggletime${windows ? ".exe" : ""}`;
const cargoArgs = [
  "build",
  "--manifest-path",
  path.join(root, "rust", "Cargo.toml"),
  "-p",
  "struggletime",
  "--target",
  target,
];
if (profile === "release") cargoArgs.push("--release");
execFileSync("cargo", cargoArgs, { cwd: root, stdio: "inherit" });

const source = path.join(root, "rust", "target", target, profile, executable);
const destinationDir = path.join(root, "desktop", "src-tauri", "binaries");
const destination = path.join(destinationDir, `struggletime-${target}${windows ? ".exe" : ""}`);
mkdirSync(destinationDir, { recursive: true });
copyFileSync(source, destination);
if (!windows) chmodSync(destination, 0o755);
console.log(`Prepared CLI sidecar: ${path.relative(root, destination)}`);
