#!/usr/bin/env node

import process from "node:process";

const TOOLS = [
  {
    name: "list_investigation_agents",
    description: "List invokable kagent agents from the controller MCP endpoint.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false
    }
  },
  {
    name: "investigate_with_agent",
    description: "Invoke the investigation agent through the kagent controller MCP endpoint.",
    inputSchema: {
      type: "object",
      properties: {
        task: {
          type: "string",
          description: "Natural-language investigation task for the agent."
        },
        agent: {
          type: "string",
          description: "Optional agent ref in format namespace/name."
        },
        context_id: {
          type: "string",
          description: "Optional conversation context id for follow-up turns."
        }
      },
      required: ["task"],
      additionalProperties: false
    }
  }
];

let readBuffer = "";
let sessionId = null;
let requestId = 0;
let remoteInitialized = false;

function controllerConfig() {
  const remoteUrl = (process.env.REMOTE_MCP_URL || "").trim();
  if (!remoteUrl) {
    throw new Error("REMOTE_MCP_URL is required");
  }

  const bearerToken = (process.env.REMOTE_MCP_BEARER_TOKEN || "").trim();
  const defaultAgentRef =
    (process.env.DEFAULT_AGENT_REF || "").trim() || "kagent/homelab-k8s-custom-agent";
  const allowInsecureTls =
    (process.env.ALLOW_INSECURE_TLS || "").trim().toLowerCase() === "true";

  if (allowInsecureTls) {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
  }

  return { remoteUrl, bearerToken, defaultAgentRef };
}

function writeMessage(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function writeResult(id, result) {
  writeMessage({ jsonrpc: "2.0", id, result });
}

function writeError(id, code, message) {
  writeMessage({
    jsonrpc: "2.0",
    id,
    error: { code, message }
  });
}

function parseMessages() {
  while (true) {
    const lineEnd = readBuffer.indexOf("\n");
    if (lineEnd === -1) {
      return;
    }

    const raw = readBuffer.slice(0, lineEnd).replace(/\r$/, "").trim();
    readBuffer = readBuffer.slice(lineEnd + 1);
    if (!raw) {
      continue;
    }

    handleMessage(JSON.parse(raw)).catch((error) => {
      console.error("[handleMessage]", error);
    });
  }
}

async function controllerRequest(method, params) {
  const { remoteUrl, bearerToken } = controllerConfig();
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream"
  };

  if (sessionId) {
    headers["mcp-session-id"] = sessionId;
  }

  if (bearerToken) {
    headers.authorization = `Bearer ${bearerToken}`;
  }

  const response = await fetch(remoteUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: ++requestId,
      method,
      params
    })
  });

  const newSessionId = response.headers.get("mcp-session-id");
  if (newSessionId) {
    sessionId = newSessionId;
  }

  const text = await response.text();
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const jsonLine =
    [...lines]
      .reverse()
      .find((line) => line.startsWith("{")) ??
    [...lines]
      .reverse()
      .find((line) => line.startsWith("data: "))
      ?.slice(6);
  if (!jsonLine) {
    throw new Error(`Unexpected controller response: ${text}`);
  }

  const payload = JSON.parse(jsonLine);
  if (payload.error) {
    throw new Error(payload.error.message || JSON.stringify(payload.error));
  }

  return payload.result;
}

async function controllerNotify(method, params) {
  const { remoteUrl, bearerToken } = controllerConfig();
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream"
  };

  if (sessionId) {
    headers["mcp-session-id"] = sessionId;
  }

  if (bearerToken) {
    headers.authorization = `Bearer ${bearerToken}`;
  }

  const response = await fetch(remoteUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      method,
      params
    })
  });

  const newSessionId = response.headers.get("mcp-session-id");
  if (newSessionId) {
    sessionId = newSessionId;
  }

  await response.text();
}

async function ensureRemoteInitialized() {
  if (remoteInitialized) {
    return;
  }

  await controllerRequest("initialize", {
    protocolVersion: "2025-11-25",
    capabilities: {},
    clientInfo: {
      name: "homelab-investigation-remote",
      version: "0.1.4"
    }
  });

  await controllerNotify("notifications/initialized", {});
  remoteInitialized = true;
}

async function listAgentsResult() {
  await ensureRemoteInitialized();
  const result = await controllerRequest("tools/call", {
    name: "list_agents",
    arguments: {}
  });

  const structured = result.structuredContent ?? {};
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(structured, null, 2)
      }
    ],
    structuredContent: structured
  };
}

async function investigateResult(args) {
  const { defaultAgentRef } = controllerConfig();
  await ensureRemoteInitialized();
  const result = await controllerRequest("tools/call", {
    name: "invoke_agent",
    arguments: {
      agent: args.agent || defaultAgentRef,
      task: args.task,
      ...(args.context_id ? { context_id: args.context_id } : {})
    }
  });

  const structured = result.structuredContent ?? {};
  const text =
    typeof structured.text === "string" && structured.text.trim()
      ? structured.text
      : JSON.stringify(structured, null, 2);

  return {
    content: [
      {
        type: "text",
        text
      }
    ],
    structuredContent: structured
  };
}

async function handleMessage(message) {
  if (message.method === "initialize") {
    writeResult(message.id, {
      protocolVersion: "2025-11-25",
      capabilities: {
        tools: {}
      },
      serverInfo: {
        name: "homelab-investigation-remote",
        version: "0.1.4"
      }
    });
    return;
  }

  if (message.method === "notifications/initialized") {
    return;
  }

  if (message.method === "tools/list") {
    writeResult(message.id, { tools: TOOLS });
    return;
  }

  if (message.method === "tools/call") {
    const { name, arguments: args = {} } = message.params ?? {};

    if (name === "list_investigation_agents") {
      writeResult(message.id, await listAgentsResult());
      return;
    }

    if (name === "investigate_with_agent") {
      writeResult(message.id, await investigateResult(args));
      return;
    }

    writeError(message.id, -32602, `Unknown tool: ${name}`);
    return;
  }

  if (message.id !== undefined) {
    writeError(message.id, -32601, `Unsupported method: ${message.method}`);
  }
}

process.on("uncaughtException", (error) => {
  console.error("[uncaughtException]", error);
});

process.on("unhandledRejection", (error) => {
  console.error("[unhandledRejection]", error);
});

process.stdin.on("data", (chunk) => {
  readBuffer += chunk.toString("utf8");
  parseMessages();
});

process.stdin.resume();
