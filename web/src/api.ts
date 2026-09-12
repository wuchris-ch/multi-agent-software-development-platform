let token = "";
export function connect() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const incoming = fragment.get("token");
  if (incoming) {
    sessionStorage.setItem("development-session", incoming);
    history.replaceState(null, "", window.location.pathname);
  }
  token = incoming || sessionStorage.getItem("development-session") || "";
}
export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(value.detail || `Request failed (${response.status})`);
  return value as T;
}
export function externalUrl(url: string | null | undefined) {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

export async function downloadBundle(identifier: string) {
  const response = await fetch(`/api/workflows/${identifier}/bundle`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok)
    throw new Error((await response.json()).detail || "Evidence export failed");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `development-evidence-${identifier.slice(0, 12)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
