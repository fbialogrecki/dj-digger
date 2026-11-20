import { readFileSync } from "node:fs";

export function GET() {
  const files = ["Archivo", "RobotoFlex", "JetBrainsMono"].map((name) => [name, `src/assets/fonts/${name}-OFL.txt`]);
  files.push(["Phosphor Icons", "node_modules/@phosphor-icons/core/LICENSE"]);
  return new Response(files.map(([name, path]) => name + "\n" + readFileSync(path, "utf8")).join("\n\n"),
    { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
