/*
  Provides authentication token to LocalProviderWrapper once loaded
  in the browser.
  Returns 401 if no token cookie exists (user needs to log in).
*/
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

import { getServerBackendUrl } from '@/lib/apiClient';
import { getAuthProvider } from '@/lib/auth/config';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';
const OSS_USER_COOKIE = 'dograh_auth_user';

function expiredSessionResponse() {
  const response = NextResponse.json({ error: 'Not authenticated' }, { status: 401 });

  for (const name of [OSS_TOKEN_COOKIE, OSS_USER_COOKIE]) {
    response.cookies.set(name, '', {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 0,
      path: '/',
    });
  }

  return response;
}

export async function GET() {
  const authProvider = await getAuthProvider();

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.json({ error: 'Not in OSS mode' }, { status: 400 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(OSS_TOKEN_COOKIE)?.value;
  const user = cookieStore.get(OSS_USER_COOKIE)?.value;

  // If no token exists, return 401 (user needs to sign up or log in)
  if (!token) {
    return expiredSessionResponse();
  }

  // Middleware only checks for a cookie. Confirm that its JWT is still accepted
  // by the API before presenting the browser as authenticated. Without this,
  // an expired token opens authenticated UI and every subsequent save returns 401.
  const backendUrl = getServerBackendUrl().replace(/\/$/, '');
  const authHeaders = { Authorization: `Bearer ${token}` };
  const legacyValidationUrl = `${backendUrl}/api/v1/user/auth/user`;
  let validation = await fetch(legacyValidationUrl, {
    headers: authHeaders,
    cache: 'no-store',
  });

  // Newer API images expose the same authenticated-user check at /me. Keep the
  // session bridge compatible while the VPS rolls from the legacy route.
  if (validation.status === 404) {
    validation = await fetch(`${backendUrl}/api/v1/auth/me`, {
      headers: authHeaders,
      cache: 'no-store',
    });
  }

  if (!validation.ok) {
    return expiredSessionResponse();
  }

  // Return the auth info as JSON
  return NextResponse.json({
    token,
    user: user ? JSON.parse(user) : { id: token, name: 'Local User', provider: 'local' },
  });
}
