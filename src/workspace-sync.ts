import { execFileSync } from "node:child_process";
import { mkdirSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { config } from "./config.js";

// Read the canonical project identity bd stores inside each database
// (metadata table, key '_project_id'). Returns null if unreadable.
function queryDbProjectId(dbName: string): string | null {
  try {
    const out = execFileSync(
      "mysql",
      [
        "-h", config.DOLT_HOST,
        "-P", String(config.DOLT_PORT),
        "-u", config.DOLT_USER,
        ...(config.DOLT_PASSWORD ? [`-p${config.DOLT_PASSWORD}`] : []),
        "--batch",
        "-N",
        "-e", `SELECT value FROM \`${dbName}\`.metadata WHERE \`key\` = '_project_id' LIMIT 1`,
      ],
      { encoding: "utf-8", timeout: 10000 }
    ).trim();
    return out || null;
  } catch {
    return null;
  }
}

export async function syncWorkspaces(): Promise<void> {
  console.log("Syncing beads workspaces from Dolt...");

  try {
    // Query Dolt for all beads databases
    const mysqlArgs = [
      "-h", config.DOLT_HOST,
      "-P", String(config.DOLT_PORT),
      "-u", config.DOLT_USER,
      ...(config.DOLT_PASSWORD ? [`-p${config.DOLT_PASSWORD}`] : []),
      "--batch",
      "-N",
      "-e", "SHOW DATABASES LIKE 'beads_%'",
    ];

    const result = execFileSync(
      "mysql",
      mysqlArgs,
      { encoding: "utf-8", timeout: 10000 }
    ).trim();

    if (!result) {
      console.log("No beads_ databases found in Dolt");
      return;
    }

    const databases = result.split("\n").filter(Boolean);
    console.log(`Found ${databases.length} beads database(s): ${databases.join(", ")}`);

    mkdirSync(config.BEADS_WORKING_DIR, { recursive: true });

    for (const dbName of databases) {
      // Extract project prefix: beads_elysium_proxmox -> elysium_proxmox
      const prefix = dbName.replace(/^beads_/, "");
      const workspaceDir = `${config.BEADS_WORKING_DIR}/${prefix}`;

      // Check if workspace is initialized. Server-mode stubs keep all data
      // on the Dolt server, so .beads/dolt stays empty forever — testing it
      // (as this code previously did) re-ran `bd init` on EVERY service
      // start, writing junk "bd init" commits to every canonical database.
      // metadata.json is written exactly once by a successful init and is
      // the correct marker.
      const beadsDir = `${workspaceDir}/.beads`;
      const needsInit = !existsSync(`${beadsDir}/metadata.json`);

      if (needsInit) {
        console.log(`Provisioning workspace: ${workspaceDir} -> ${dbName}`);
        mkdirSync(workspaceDir, { recursive: true });
        try {
          const bdEnv: NodeJS.ProcessEnv = {
            ...process.env,
            ...(config.DOLT_PASSWORD ? { BEADS_DOLT_PASSWORD: config.DOLT_PASSWORD } : {}),
          };
          execFileSync(
            "bd",
            [
              "init",
              "--database", dbName,
              "--server-host", config.DOLT_HOST,
              "--server-port", String(config.DOLT_PORT),
              "--server-user", config.DOLT_USER,
            ],
            { cwd: workspaceDir, encoding: "utf-8", timeout: 30000, env: bdEnv }
          );
          console.log(`  Provisioned ${prefix}`);
        } catch (err) {
          console.error(`  Failed to provision ${prefix}:`, err);
        }
      } else {
        // Workspace exists. Repair it so a third-party reader (e.g. the Hermes
        // agent) sees the canonical data without a local checkout. Full git
        // checkouts ship a committed metadata.json that (a) uses dolt_mode
        // "embedded" — making bd read an empty local store and return 0 beads —
        // and (b) may carry a project_id that does not match the canonical
        // database, tripping bd's PROJECT IDENTITY MISMATCH guard. Reconcile
        // both in place. Non-destructive: no `bd init`, no writes to the
        // canonical Dolt database.
        try {
          const metaPath = `${beadsDir}/metadata.json`;
          const meta = JSON.parse(readFileSync(metaPath, "utf-8"));
          let changed = false;

          if (meta.dolt_mode !== "server") {
            console.log(`  -> ${prefix}: repairing dolt_mode '${meta.dolt_mode}' -> 'server'`);
            meta.dolt_mode = "server";
            delete meta.dolt_server_port; // deprecated; bd uses .beads/dolt-server.port
            writeFileSync(`${beadsDir}/dolt-server.port`, String(config.DOLT_PORT));
            changed = true;
          }

          const dbProjectId = queryDbProjectId(dbName);
          if (dbProjectId && meta.project_id !== dbProjectId) {
            console.log(`  -> ${prefix}: aligning project_id '${meta.project_id}' -> '${dbProjectId}'`);
            meta.project_id = dbProjectId;
            changed = true;
          }

          if (changed) {
            writeFileSync(metaPath, JSON.stringify(meta, null, 2));
          } else {
            console.log(`  -> ${prefix}: workspace already initialized`);
          }
        } catch (err) {
          console.error(`  Failed to inspect/repair ${prefix}:`, err);
        }
      }
    }

    console.log("Workspace sync complete");
  } catch (err) {
    console.error("Workspace sync failed:", err);
    // Don't throw — server should still start even if sync fails
  }
}
