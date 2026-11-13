export async function readWrapperAuthError(
  response: Response, fallback: string, codeLabel: string, networkHint: string,
): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
    const detail = body.detail;
    if (!detail || typeof detail !== "object" || !("error_code" in detail)) return fallback;
    const code = detail.error_code;
    if (typeof code !== "number" || !Number.isInteger(code) || code < 0 || code > 65535) return fallback;
    const message = `${fallback} (${codeLabel}: ${code})`;
    return code === 8 && "hint" in detail && detail.hint === "check_apple_network"
      ? `${message}. ${networkHint}` : message;
  } catch {
    return fallback;
  }
}
