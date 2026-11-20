// Keep the published shell installer in step with the binary download links.
import { readFileSync, writeFileSync } from "node:fs";
import { version } from "../site.config.mjs";

const path = "dist/install.sh";
const script = readFileSync(path, "utf8");
const requirement = "PACKAGE='dj-sc-digger[gui,analyze]'";
if (!script.includes(requirement)) throw new Error("Missing Linux installer package requirement");
writeFileSync(path, script.replace(requirement, `PACKAGE='dj-sc-digger[gui,analyze]==${version}'`));
