export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers =
    options.body instanceof FormData
      ? options.headers
      : { "Content-Type": "application/json", ...options.headers };
  const response = await fetch(path, {
    ...options,
    headers,
  });

  if (response.status === 204) return null as T;

  const responseText = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(responseText);
  } catch {
    if (response.ok) {
      const receivedHtml =
        response.headers.get("Content-Type")?.includes("text/html") ||
        responseText.trimStart().startsWith("<");
      const message = receivedHtml
        ? `Backend API returned HTML for ${path}. Deploy or restart the backend, then verify the API proxy.`
        : `Backend API returned invalid JSON for ${path}.`;
      throw new ApiError(message, response.status);
    }
    throw new ApiError(`Request failed (${response.status})`, response.status);
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    )
      message = body.detail;
    throw new ApiError(message, response.status);
  }

  return body as T;
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(path);
  if (!response.ok)
    throw new ApiError(
      `BPMN definition unavailable (${response.status})`,
      response.status,
    );
  return response.text();
}
