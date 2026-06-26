import { z } from "zod";
import "dotenv/config";

const ConfigSchema = z.object({
  PORT: z.coerce.number().default(8092),
  MCP_SERVER_URL: z.string().url(),
  AUTH_USER: z.string().min(1),
  AUTH_PASSWORD: z.string().min(8),
  JWT_SECRET: z.string().min(32),
  CLIENTS_FILE: z.string().default("/opt/beads-mcp-server/clients.json"),
  BEADS_MCP_CMD: z.string().default("beads-mcp"),
  BEADS_WORKING_DIR: z.string().default("/opt/beads-workspaces"),
  UPSTREAM_CONNECT_TIMEOUT_MS: z.coerce.number().int().positive().default(30000),
  SESSION_IDLE_TIMEOUT_MS: z.coerce.number().int().positive().default(900000),
  DOLT_HOST: z.string().default("127.0.0.1"),
  DOLT_PORT: z.coerce.number().default(3307),
  DOLT_USER: z.string().default("root"),
  DOLT_PASSWORD: z.string().default(""),
});

export const config = ConfigSchema.parse(process.env);
