const DEFAULT_TIMEOUT_MS = 10_000;

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        accept: "application/json",
        ...(options.body ? { "content-type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const message = typeof body === "object" ? body.error || body.message : body;
      throw new Error(message || `HTTP ${response.status}`);
    }
    return body;
  } finally {
    window.clearTimeout(timeout);
  }
}

export const httpApi = {
  get(path, options) {
    return request(path, { ...options, method: "GET" });
  },
  post(path, payload, options) {
    return request(path, {
      ...options,
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  put(path, payload, options) {
    return request(path, {
      ...options,
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  patch(path, payload, options) {
    return request(path, {
      ...options,
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  delete(path, options) {
    return request(path, { ...options, method: "DELETE" });
  },
  getConfig() {
    return request("/api/config");
  },
  getHealth() {
    return request("/api/health");
  },
};

export default httpApi;
