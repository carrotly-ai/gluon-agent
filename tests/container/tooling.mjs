// Offline checks for the developer tools shipped in the production image.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "gluon-tooling-home-"));
const env = {
	...process.env,
	HOME: home,
	XDG_CONFIG_HOME: path.join(home, "config"),
	CI: "1",
	VERCEL_TELEMETRY_DISABLED: "1",
	NO_UPDATE_NOTIFIER: "1",
	// The Node CLI is the supported default; the native CLI is upstream opt-in.
	VERCEL_CLI_USE_NATIVE_BINARY: "0",
};
const versions = {};

function run(command, args, options = {}) {
	const result = spawnSync(command, args, {
		env,
		encoding: "utf8",
		timeout: 60_000,
		...options,
	});
	assert.equal(
		result.status,
		0,
		`${command} ${args.join(" ")}: ${result.error ?? result.stderr}`,
	);
	return result.stdout.trim() || result.stderr.trim();
}

for (const command of [
	"node",
	"bun",
	"npm",
	"uv",
	"git",
	"gh",
	"biome",
	"agent-browser",
	"vercel",
	"vc",
]) {
	versions[command] = run(command, ["--version"]);
}
assert.equal(
	fs.realpathSync("/usr/bin/vercel"),
	fs.realpathSync("/usr/bin/vc"),
);
for (const command of ["build", "deploy", "dev"]) {
	run("vercel", [command, "--help"]);
}

// Exercise the browser and its shared libraries without a remote page or server.
const html = run("chromium", [
	"--headless",
	"--no-sandbox",
	"--disable-gpu",
	"--disable-background-networking",
	"--dump-dom",
	"data:text/html,<title>fictional</title><p>offline-browser-check</p>",
]);
assert.ok(html.includes("offline-browser-check"), html);

const require = createRequire(fs.realpathSync("/usr/bin/vercel"));
for (const kind of ["static", "node"]) {
	const directory = fs.mkdtempSync(
		path.join(os.tmpdir(), `gluon-tooling-${kind}-`),
	);
	fs.mkdirSync(path.join(directory, ".vercel"));
	fs.writeFileSync(
		path.join(directory, ".vercel/project.json"),
		JSON.stringify({
			projectId: "prj_fictional_offline",
			orgId: "team_fictional_offline",
			projectName: "fictional-offline",
			settings: {
				framework: null,
				buildCommand: kind === "static" ? "node build.cjs" : null,
				installCommand: "node -e \"console.log('fictional offline install')\"",
				outputDirectory: kind === "static" ? "public" : null,
				nodeVersion: "24.x",
				createdAt: 1,
				updatedAt: 1,
			},
		}),
	);
	fs.writeFileSync(
		path.join(directory, "package.json"),
		JSON.stringify({
			name: `fictional-offline-${kind}`,
			version: "1.0.0",
			private: true,
		}),
	);
	if (kind === "static") {
		fs.writeFileSync(
			path.join(directory, "build.cjs"),
			"const fs = require('node:fs'); fs.mkdirSync('public'); fs.writeFileSync('public/index.html', '<title>fictional</title>offline-build');",
		);
		fs.writeFileSync(
			path.join(directory, "vercel.json"),
			JSON.stringify({
				version: 2,
				framework: null,
				buildCommand: "node build.cjs",
				outputDirectory: "public",
			}),
		);
	} else {
		fs.mkdirSync(path.join(directory, "api"));
		fs.writeFileSync(
			path.join(directory, "api/hello.js"),
			"module.exports = (request, response) => response.json({ fixture: 'fictional', method: request.method });",
		);
		fs.writeFileSync(
			path.join(directory, "vercel.json"),
			JSON.stringify({
				version: 2,
				builds: [{ src: "api/hello.js", use: "@vercel/node" }],
				routes: [{ src: "/api/hello", dest: "/api/hello.js" }],
			}),
		);
	}
	run("vercel", ["build", "--standalone", "--cwd", directory]);
	const output = path.join(directory, ".vercel/output");
	assert.equal(
		JSON.parse(fs.readFileSync(path.join(output, "config.json"), "utf8"))
			.version,
		3,
	);
	if (kind === "static") {
		assert.ok(
			fs
				.readFileSync(path.join(output, "static/index.html"), "utf8")
				.includes("offline-build"),
		);
	} else {
		const handler = require(
			path.join(output, "functions/api/hello.js.func/api/hello.js"),
		);
		let response;
		handler(
			{ method: "GET" },
			{
				json: (value) => {
					response = value;
				},
			},
		);
		assert.deepEqual(response, { fixture: "fictional", method: "GET" });
	}
}
console.log(
	JSON.stringify({ versions, browser: "ok", vercelBuilds: ["static", "node"] }),
);
