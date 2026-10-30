export function apiErrorMessage(status, body, hostname = "") {
  if (typeof body?.error === "string" && body.error.trim()) return body.error;
  if (status === 404) {
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(hostname);
    return local
      ? "This feature is missing from the running backend. Restart the GotTime server, then try again."
      : "This feature is missing from the running backend. Deploy the latest GotTime version, then try again.";
  }
  if (status === 401) return "Please sign in again.";
  if (status >= 500) return "The server could not load this information. Please try again shortly.";
  return "The request could not be completed. Please try again.";
}
