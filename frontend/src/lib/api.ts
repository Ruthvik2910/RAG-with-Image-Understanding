// Establish one server-issued session before concurrent browser requests.
export const API_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";
let sessionReady: Promise<void> | null = null;
export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  if (!sessionReady) {
    sessionReady = fetch(`${API_URL}/config`, { credentials: "include", cache: "no-store" })
      .then((response) => { if (!response.ok) throw new Error("Unable to initialize the backend session."); })
      .catch((error) => { sessionReady = null; throw error; });
  }
  await sessionReady;
  return fetch(input, { ...init, credentials: "include", cache: "no-store" });
}
