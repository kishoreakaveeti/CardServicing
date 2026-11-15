export type SessionData = { name: string; csrf: string };

const unavailable = 'The banking service is unavailable. Check the Experience API debug session and try again.';

export async function readSessionResponse(response: Response): Promise<SessionData> {
  // Proxies can return an empty or HTML error body. Never show raw server output
  // or a JSON parser exception, and never retry a credentials submission here.
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new Error(response.ok ? 'The banking service returned an invalid response. Please try again.' : unavailable);
  }
  if (!response.ok) {
    if (response.status >= 500) throw new Error(unavailable);
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
    throw new Error(typeof detail === 'string' && detail ? detail : 'Sign-in details were not accepted. Check the form and try again.');
  }
  if (!data || typeof data !== 'object' || !('name' in data) || !('csrf' in data) ||
      typeof data.name !== 'string' || !data.name || typeof data.csrf !== 'string' || !data.csrf) {
    throw new Error('The banking service returned an invalid session. Please sign in again.');
  }
  return { name: data.name, csrf: data.csrf };
}
