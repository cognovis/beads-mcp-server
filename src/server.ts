import express from "express";
import cors from "cors";
import { randomUUID } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { mcpAuthRouter } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { proxyServer } from "mcp-proxy";
import { config } from "./config.js";
import { oauthProvider, handleAuthorizeSubmit } from "./auth/provider.js";
import { syncWorkspaces } from "./workspace-sync.js";

const app = express();

// CORS for all origins (NetBird handles access control)
app.use(cors());

// Body parsing
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Health endpoint (no auth)
app.get("/health", (_req, res) => {
  res.json({
    status: "ok",
    service: "beads-mcp-server",
    port: config.PORT,
    sessions: sessions.size,
  });
});

// OAuth auth router (well-known, authorize, token, register)
const serverUrl = new URL(config.MCP_SERVER_URL);
app.use(
  mcpAuthRouter({
    provider: oauthProvider,
    issuerUrl: serverUrl,
    baseUrl: serverUrl,
    scopesSupported: ["read", "write"],
    resourceName: "beads MCP Server",
  })
);

// Handle login form submission
app.post("/authorize/submit", (req, res) => {
  handleAuthorizeSubmit(req.body, res);
});

// Bearer auth middleware for MCP endpoint
const bearerAuth = requireBearerAuth({
  verifier: oauthProvider,
});

type SessionRecord = {
  transport: StreamableHTTPServerTransport;
  stdioTransport: StdioClientTransport;
  client: Client;
  idleTimer: NodeJS.Timeout;
  createdAt: number;
  lastSeenAt: number;
  closing: boolean;
};

// Session management: each HTTP session owns one upstream beads-mcp process.
const sessions = new Map<string, SessionRecord>();

function describeError(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> {
  let timeout: NodeJS.Timeout | undefined;
  const timeoutPromise = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => {
      reject(new Error(`${label} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
  });

  try {
    return await Promise.race([promise, timeoutPromise]);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

function sendServerError(res: express.Response, status: number, message: string): void {
  if (res.headersSent) {
    res.end();
    return;
  }
  res.status(status).json({
    error: "server_error",
    error_description: message,
  });
}

function scheduleIdleCleanup(sessionId: string, record: SessionRecord): void {
  record.idleTimer = setTimeout(() => {
    closeSession(sessionId, "idle timeout").catch((error) => {
      console.error(`Failed to close idle MCP session ${sessionId}: ${describeError(error)}`);
    });
  }, config.SESSION_IDLE_TIMEOUT_MS);
}

function touchSession(sessionId: string, record: SessionRecord): void {
  record.lastSeenAt = Date.now();
  clearTimeout(record.idleTimer);
  scheduleIdleCleanup(sessionId, record);
}

async function closeSession(sessionId: string, reason: string): Promise<void> {
  const record = sessions.get(sessionId);
  if (!record || record.closing) return;

  record.closing = true;
  sessions.delete(sessionId);
  clearTimeout(record.idleTimer);

  try {
    await record.client.close();
  } catch (error) {
    console.error(`Failed to close upstream MCP client for ${sessionId}: ${describeError(error)}`);
  }

  try {
    await record.stdioTransport.close();
  } catch (error) {
    console.error(`Failed to close upstream MCP transport for ${sessionId}: ${describeError(error)}`);
  }

  try {
    await record.transport.close();
  } catch (error) {
    console.error(`Failed to close HTTP MCP transport for ${sessionId}: ${describeError(error)}`);
  }

  console.log(`Closed MCP session ${sessionId}: ${reason}`);
}

async function closeUpstream(
  client: Client | undefined,
  stdioTransport: StdioClientTransport | undefined,
  reason: string
): Promise<void> {
  if (!client) return;
  try {
    await client.close();
  } catch (error) {
    console.error(`Failed to close upstream MCP client after ${reason}: ${describeError(error)}`);
  }

  try {
    await stdioTransport?.close();
  } catch (error) {
    console.error(`Failed to close upstream MCP transport after ${reason}: ${describeError(error)}`);
  }
}

// MCP handler - mounted on both /mcp and / (claude.ai POSTs to the server URL directly)
const mcpHandler: express.RequestHandler = async (req, res) => {
  const sessionId = req.headers["mcp-session-id"] as string | undefined;

  if (req.method === "GET" || req.method === "DELETE") {
    // GET = SSE stream, DELETE = close session
    if (!sessionId || !sessions.has(sessionId)) {
      res.status(400).json({ error: "Invalid or missing session ID" });
      return;
    }
    const record = sessions.get(sessionId)!;
    touchSession(sessionId, record);
    await record.transport.handleRequest(req, res);
    if (req.method === "DELETE") {
      await closeSession(sessionId, "client requested DELETE");
    }
    return;
  }

  // POST: existing session
  if (sessionId && sessions.has(sessionId)) {
    const record = sessions.get(sessionId)!;
    touchSession(sessionId, record);
    await record.transport.handleRequest(req, res, req.body);
    return;
  }

  // New session: spawn beads-mcp child process and proxy via mcp-proxy
  let mcpClient: Client | undefined;
  let httpTransport: StreamableHTTPServerTransport | undefined;

  const stdioTransport = new StdioClientTransport({
    command: config.BEADS_MCP_CMD,
    args: [],
    env: {
      ...process.env,
      BEADS_WORKSPACE_ROOT: config.BEADS_WORKING_DIR,
      ...(config.DOLT_PASSWORD ? { BEADS_DOLT_PASSWORD: config.DOLT_PASSWORD } : {}),
    } as Record<string, string>,
  });

  try {
    // Connect a Client to the stdio process
    mcpClient = new Client({ name: "beads-mcp-proxy", version: "1.0.0" });
    await withTimeout(
      mcpClient.connect(stdioTransport),
      config.UPSTREAM_CONNECT_TIMEOUT_MS,
      "upstream beads-mcp initialize"
    );

    // Discover capabilities of the upstream beads-mcp server
    const serverCapabilities = mcpClient.getServerCapabilities();

    // Create an MCP Server that will proxy to the Client
    const mcpServer = new Server(
      { name: "beads-mcp-server", version: "1.0.0" },
      { capabilities: serverCapabilities ?? {} }
    );

    // Wire the server's request handlers to delegate to the client
    await proxyServer({ server: mcpServer, client: mcpClient, serverCapabilities: serverCapabilities ?? {} });

    // Create HTTP transport for this session
    httpTransport = new StreamableHTTPServerTransport({
      sessionIdGenerator: () => randomUUID(),
    });

    httpTransport.onclose = () => {
      const sid = httpTransport?.sessionId;
      if (sid && sessions.has(sid)) {
        closeSession(sid, "transport closed").catch((error) => {
          console.error(`Failed to close MCP session ${sid}: ${describeError(error)}`);
        });
      } else if (!sid) {
        closeUpstream(mcpClient, stdioTransport, "transport closed before registration").catch((error) => {
          console.error(`Failed to close unregistered upstream MCP process: ${describeError(error)}`);
        });
      }
    };

    await mcpServer.connect(httpTransport);
    await httpTransport.handleRequest(req, res, req.body);

    // Store session after handleRequest so sessionId is set by the transport
    const sid = httpTransport.sessionId;
    if (sid) {
      const record: SessionRecord = {
        transport: httpTransport,
        stdioTransport,
        client: mcpClient,
        idleTimer: setTimeout(() => undefined, 0),
        createdAt: Date.now(),
        lastSeenAt: Date.now(),
        closing: false,
      };
      clearTimeout(record.idleTimer);
      scheduleIdleCleanup(sid, record);
      sessions.set(sid, record);
      return;
    }

    await closeUpstream(mcpClient, stdioTransport, "missing session ID after initialize");
    sendServerError(res, 500, "MCP initialize did not create a session");
  } catch (error) {
    const message = describeError(error);
    const isTimeout = message.includes("timed out");
    console.error(`Failed to initialize upstream beads-mcp session: ${message}`);
    await closeUpstream(mcpClient, stdioTransport, "initialize failure");
    sendServerError(
      res,
      isTimeout ? 504 : 502,
      isTimeout ? "Timed out initializing upstream beads-mcp session" : "Failed to initialize upstream beads-mcp session"
    );
  }
};

app.all("/mcp", bearerAuth, mcpHandler);
app.post("/", bearerAuth, mcpHandler);
app.get("/", bearerAuth, mcpHandler);
app.delete("/", bearerAuth, mcpHandler);

// Sync workspaces on startup
syncWorkspaces().catch(console.error);

app.listen(config.PORT, "0.0.0.0", () => {
  console.log(`beads MCP Server listening on port ${config.PORT}`);
  console.log(`OAuth issuer: ${config.MCP_SERVER_URL}`);
});

async function shutdown(): Promise<void> {
  const closeTasks = [...sessions.keys()].map((sessionId) => closeSession(sessionId, "process shutdown"));
  await Promise.allSettled(closeTasks);
  process.exit(0);
}

process.on("SIGINT", () => {
  shutdown().catch((error) => {
    console.error(`Shutdown failed: ${describeError(error)}`);
    process.exit(1);
  });
});

process.on("SIGTERM", () => {
  shutdown().catch((error) => {
    console.error(`Shutdown failed: ${describeError(error)}`);
    process.exit(1);
  });
});
