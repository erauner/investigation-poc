#!/usr/bin/env node

import { realpathSync } from "node:fs";
import process from "node:process";
import { fileURLToPath } from "node:url";

const TOOLS = [
  {
    name: "investigate",
    description:
      "Investigate a Kubernetes issue through the controller-backed investigation path, with deterministic generic-vs-alert entrypoint routing.",
    inputSchema: {
      type: "object",
      properties: {
        task: {
          type: "string",
          description: "Natural-language investigation task for the agent."
        },
        mode: {
          type: "string",
          enum: ["auto", "generic", "alert"],
          description:
            "Optional routing mode. Defaults to auto, which only selects alert mode when explicit alert markers are present."
        },
        alertname: {
          type: "string",
          description:
            "Optional explicit alert name. When present, investigate routes to the alert-specific entrypoint."
        },
        labels: {
          type: "object",
          description:
            "Optional alert labels forwarded to the agent when alert mode is selected.",
          additionalProperties: {
            type: "string"
          }
        },
        annotations: {
          type: "object",
          description:
            "Optional alert annotations forwarded to the agent when alert mode is selected.",
          additionalProperties: {
            type: "string"
          }
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
  },
  {
    name: "list_investigation_agents",
    description: "List invokable kagent agents from the controller MCP endpoint.",
    inputSchema: {
      type: "object",
      properties: {},
      additionalProperties: false
    }
  }
];

let readBuffer = "";
let sessionId = null;
let requestId = 0;
let remoteInitialized = false;
const ENTRYPOINT_PREFIX = "[INVESTIGATION_ENTRYPOINT]=";
const OPERATOR_TARGET_PREFIXES = ["Backend/", "Frontend/", "Cluster/"];

function isMainModule() {
  if (!process.argv[1]) {
    return false;
  }
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
  } catch {
    return false;
  }
}

const IS_MAIN_MODULE = isMainModule();

function normalizeMode(rawMode) {
  const mode = typeof rawMode === "string" ? rawMode.trim().toLowerCase() : "auto";
  if (!mode) {
    return "auto";
  }
  if (mode === "auto" || mode === "generic" || mode === "alert") {
    return mode;
  }
  throw new Error(`Unsupported investigate mode: ${rawMode}`);
}

function extractAlertname(task) {
  const markers = [
    /\balertname\s*[:=]\s*([^\s,.;]+)/i,
    /^\s*Investigate\s+alert\s+([^\s,.;:]+)/i
  ];

  for (const pattern of markers) {
    const match = task.match(pattern);
    if (!match) {
      continue;
    }
    const candidate = match[1]?.trim() ?? "";
    if (!candidate) {
      continue;
    }
    if (candidate.includes("/")) {
      return null;
    }
    if (OPERATOR_TARGET_PREFIXES.some((prefix) => candidate.startsWith(prefix))) {
      return null;
    }
    return candidate;
  }

  return null;
}

function serializeKeyValueMap(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  const entries = Object.entries(value)
    .filter(([, item]) => typeof item === "string" && item.trim())
    .sort(([left], [right]) => left.localeCompare(right));

  if (!entries.length) {
    return null;
  }

  return JSON.stringify(Object.fromEntries(entries));
}

function commonRuntimeLines() {
  return [
    "Use the planner-led investigation flow.",
    "Prefer run_orchestrated_investigation as the default end-to-end runtime path once parsing and target resolution are complete.",
    "run_orchestrated_investigation keeps batch selection, external-step materialization, advancement, and final rendering in product code.",
    "Treat handoff_active_evidence_batch, get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams for debugging or explicit adapter choreography.",
    "Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.",
    "Use render_investigation_report only as a secondary low-level render seam when you are explicitly debugging the staged runtime path.",
    "Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step."
  ];
}

function genericParsingLines() {
  return [
    "If the target is vague or operator-backed, resolve it first with resolve_primary_target.",
    "If the request only says the unhealthy pod in a namespace, use Kubernetes MCP to identify the concrete unhealthy pod first, then continue with the planner-led control-plane path using that target."
  ];
}

function alertParsingLines(inferredAlertname, args) {
  const lines = [
    "Treat the pasted content below as alert text to extract, not as a workload target string.",
    "Extract alertname, labels, annotations, namespace, pod, service, instance, severity, and status from the pasted alert text before using the planner-led investigation path.",
    "If the pasted text includes Labels: or Annotations: sections, use those values as the authoritative alert fields.",
    "Treat only identity fields such as namespace, pod, service, deployment, node, and container as workload identity.",
    "Treat source or monitoring fields such as prometheus, alertmanager, rule_group, generatorURL, datasource, and runbook_url as metadata, not as workload identity.",
    "Never derive a workload namespace from source or monitoring metadata.",
    "If a service or pod label is present but namespace is missing, say the namespace is unknown instead of guessing.",
    "Do not investigate the first freeform words of the pasted message as the target unless they are explicitly a Kubernetes object reference such as pod/<name> or service/<name>.",
    "If live runtime evidence disagrees with the alert payload, call out the mismatch explicitly as possible stale alert metadata or drift between alert time and current state.",
    "Preserve the original alert name and the resolved operational target name explicitly in the final five-section answer when they are present in the request or report evidence.",
    "Also preserve the exact original alert-derived target string verbatim, such as pod/<name>, even if runtime resolution later points to a deployment or a specific replica pod.",
    "Do not rewrite the original alert-derived target string by removing the slash or changing its formatting. Keep forms such as pod/crashy exactly as written.",
    "Return exactly these five sections and no extra appendix sections: Diagnosis, Evidence, Related Data, Limitations, Recommended next step.",
    `alertname: ${inferredAlertname}`
  ];
  const labels = serializeKeyValueMap(args.labels);
  if (labels) {
    lines.push(`labels: ${labels}`);
  }
  const annotations = serializeKeyValueMap(args.annotations);
  if (annotations) {
    lines.push(`annotations: ${annotations}`);
  }
  return lines;
}

export function buildInvestigationTask(args) {
  const originalTask = typeof args.task === "string" ? args.task.trim() : "";
  if (!originalTask) {
    throw new Error("investigate task is required");
  }

  const explicitAlertname =
    typeof args.alertname === "string" && args.alertname.trim()
      ? args.alertname.trim()
      : null;
  const mode = normalizeMode(args.mode);
  const inferredAlertname = explicitAlertname ?? extractAlertname(originalTask);
  const selectedMode =
    mode === "auto" ? (inferredAlertname ? "alert" : "generic") : mode;

  if (selectedMode === "alert" && !inferredAlertname) {
    throw new Error(
      "Alert mode requires an explicit alertname or an alert-shaped task marker such as 'alertname=PodCrashLooping'."
    );
  }

  const lines = [`${ENTRYPOINT_PREFIX}${selectedMode}`];

  if (selectedMode === "generic") {
    lines.push(...genericParsingLines(), ...commonRuntimeLines());
  } else {
    lines.push(...alertParsingLines(inferredAlertname, args), ...commonRuntimeLines());
  }

  lines.push("", "Original user request:", originalTask);
  return lines.join("\n");
}

function controllerConfig() {
  const remoteUrl = (
    process.env.INVESTIGATION_REMOTE_MCP_URL ||
    process.env.REMOTE_MCP_URL ||
    ""
  ).trim();
  if (!remoteUrl) {
    throw new Error("INVESTIGATION_REMOTE_MCP_URL is required");
  }

  const bearerToken = (
    process.env.INVESTIGATION_REMOTE_MCP_TOKEN ||
    process.env.REMOTE_MCP_BEARER_TOKEN ||
    ""
  ).trim();
  const defaultAgentRef =
    (
      process.env.INVESTIGATION_DEFAULT_AGENT_REF ||
      process.env.DEFAULT_AGENT_REF ||
      ""
    ).trim() || "kagent/incident-triage";
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
      name: "investigation-remote",
      version: "0.1.6"
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
      task: buildInvestigationTask(args),
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
        name: "investigation-remote",
        version: "0.1.6"
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

    if (name === "investigate" || name === "investigate_with_agent") {
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

if (IS_MAIN_MODULE) {
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
}
