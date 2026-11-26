type CatalogServer = {
  id: number;
  address: string;
  protocol: "java" | "bedrock";
};

type Sample = {
  server_id: number;
  sampled_at: string;
  online: boolean;
  online_count: number;
  max_count: number;
  players: string[];
};

const supabaseUrl = Deno.env.get("SUPABASE_URL");
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
const batchSize = 25;
const concurrency = 5;

if (!supabaseUrl || !serviceRoleKey) {
  throw new Error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.");
}

function serviceHeaders(extra: HeadersInit = {}): Headers {
  return new Headers({
    apikey: serviceRoleKey!,
    Authorization: `Bearer ${serviceRoleKey}`,
    "Content-Type": "application/json",
    ...Object.fromEntries(new Headers(extra).entries()),
  });
}

async function fetchJson(url: string): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5_000);
  try {
    const response = await fetch(url, {
      headers: { "User-Agent": "MC-Monitor-History/1.0" },
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Status provider returned HTTP ${response.status}.`);
    }
    const value: unknown = await response.json();
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Status provider returned an invalid response.");
    }
    return value as Record<string, unknown>;
  } finally {
    clearTimeout(timeout);
  }
}

function text(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (Array.isArray(value)) {
    return value.map(text).filter(Boolean).join("\n");
  }
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return text(
      object.clean ?? object.name_clean ?? object.name_raw ?? object.name ??
        object.raw,
    );
  }
  return "";
}

function playerNames(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return [...new Set(value.map(text).filter((name) => name.length > 0))]
    .slice(0, 100);
}

function integer(value: unknown): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
}

function sampleFrom(
  server: CatalogServer,
  payload: Record<string, unknown>,
  sampledAt: string,
): Sample {
  const online = payload.online === true;
  const players = payload.players && typeof payload.players === "object"
    ? payload.players as Record<string, unknown>
    : {};
  const rawPlayers = players.list;
  const maxCount = integer(players.max);
  const onlineCount = online ? integer(players.online) : 0;

  return {
    server_id: server.id,
    sampled_at: sampledAt,
    online,
    online_count: onlineCount,
    max_count: Math.max(maxCount, onlineCount),
    players: online ? playerNames(rawPlayers) : [],
  };
}

async function probe(
  server: CatalogServer,
  sampledAt: string,
): Promise<Sample> {
  const address = encodeURIComponent(server.address);
  const endpoints = [
    `https://api.mcstatus.io/v2/status/${server.protocol}/${address}`,
    server.protocol === "bedrock"
      ? `https://api.mcsrvstat.us/bedrock/3/${address}`
      : `https://api.mcsrvstat.us/3/${address}`,
  ];
  let lastError: unknown;

  for (const endpoint of endpoints) {
    try {
      const payload = await fetchJson(endpoint);
      if (typeof payload.online !== "boolean") {
        throw new Error("Status provider omitted the online flag.");
      }
      return sampleFrom(server, payload, sampledAt);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError instanceof Error
    ? lastError
    : new Error("All Minecraft status providers failed.");
}

async function getBatch(): Promise<CatalogServer[]> {
  const response = await fetch(
    `${supabaseUrl}/rest/v1/rpc/claim_server_catalog_collection_batch`,
    {
      method: "POST",
      headers: serviceHeaders(),
      body: JSON.stringify({ result_limit: batchSize }),
    },
  );
  if (!response.ok) {
    throw new Error(
      `Unable to claim catalog servers: HTTP ${response.status}.`,
    );
  }
  const batch: unknown = await response.json();
  if (!Array.isArray(batch)) {
    throw new Error("Collector batch RPC returned an invalid response.");
  }
  return batch.map((item): CatalogServer => {
    if (!item || typeof item !== "object") {
      throw new Error("Collector batch contained an invalid server.");
    }
    const server = item as Record<string, unknown>;
    if (
      !Number.isSafeInteger(server.id) ||
      typeof server.address !== "string" ||
      !["java", "bedrock"].includes(String(server.protocol))
    ) {
      throw new Error("Collector batch contained invalid server fields.");
    }
    return {
      id: server.id as number,
      address: server.address,
      protocol: server.protocol as CatalogServer["protocol"],
    };
  });
}

async function collect(samples: Sample[]): Promise<void> {
  if (samples.length === 0) {
    return;
  }
  const response = await fetch(
    `${supabaseUrl}/rest/v1/server_catalog_collector_samples?on_conflict=server_id,sampled_at`,
    {
      method: "POST",
      headers: serviceHeaders({
        Prefer: "resolution=merge-duplicates,return=minimal",
      }),
      body: JSON.stringify(samples),
    },
  );
  if (!response.ok) {
    throw new Error(
      `Unable to store history samples: HTTP ${response.status}.`,
    );
  }
}

Deno.serve(async (request) => {
  if (request.method !== "POST") {
    return new Response("Method not allowed.", { status: 405 });
  }
  const authorization = request.headers.get("authorization");
  if (authorization !== `Bearer ${serviceRoleKey}`) {
    return new Response("Unauthorized.", { status: 401 });
  }

  try {
    const servers = await getBatch();
    const sampledAt = new Date(
      Math.floor(Date.now() / 60_000) * 60_000,
    ).toISOString();
    const samples: Sample[] = [];
    let failed = 0;
    let nextIndex = 0;

    const workers = Array.from(
      { length: Math.min(concurrency, servers.length) },
      async () => {
        while (nextIndex < servers.length) {
          const index = nextIndex++;
          try {
            samples.push(await probe(servers[index], sampledAt));
          } catch (error) {
            failed++;
            console.error(
              `Status collection failed for catalog server ${
                servers[index].id
              }:`,
              error,
            );
          }
        }
      },
    );
    await Promise.all(workers);
    await collect(samples);

    return Response.json({
      attempted: servers.length,
      stored: samples.length,
      failed,
    });
  } catch (error) {
    console.error("History collection failed:", error);
    return Response.json({ error: "History collection failed." }, {
      status: 500,
    });
  }
});
