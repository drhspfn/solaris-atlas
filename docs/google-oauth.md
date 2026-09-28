# Google sign-in setup

Google OAuth is optional until credentials are configured. The application uses Google's OpenID Connect authorization-code flow with `openid email profile`, one-time server-side state, nonce, and PKCE. Only the Google subject, verified email, and small profile metadata are retained; access and refresh tokens are discarded.

## Development

In Google Cloud Console, create an OAuth client of type **Web application** and add this authorized redirect URI:

```text
http://localhost:8000/auth/google/callback
```

The current Vite app proxies `/api/*` to the API on port 8000, so no JavaScript origin is needed for this server-side flow. Set these values in `.env`:

```dotenv
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
FRONTEND_URL=http://localhost:5173
CORS_ALLOWED_ORIGINS=http://localhost:5173
AUTH_COOKIE_SECURE=false
AUTH_CSRF_SECRET=<long random secret>
```

The default compose/Vite port may be 3000 or 5173 depending on how the frontend is launched. `FRONTEND_URL` must match the actual browser origin. Add that exact origin to `CORS_ALLOWED_ORIGINS` when the browser calls the API cross-origin; the built compose frontend proxies API calls same-origin.

## Production

For the deployment with the API exposed at `api.solarisatlas.fun`, register:

```text
https://api.solarisatlas.fun/auth/google/callback
```

Configure:

```dotenv
FRONTEND_URL=https://solarisatlas.fun
GOOGLE_REDIRECT_URI=https://api.solarisatlas.fun/auth/google/callback
CORS_ALLOWED_ORIGINS=https://solarisatlas.fun
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
AUTH_COOKIE_DOMAIN=.solarisatlas.fun
AUTH_CSRF_SECRET=<long random secret>
```

The current web container proxies `/api` on `solarisatlas.fun` to the API, while Google returns to `api.solarisatlas.fun`. The parent-domain cookie is needed so the session created by that callback is sent through the frontend's same-origin API proxy. If the frontend instead calls the API subdomain directly, configure `VITE_API_BASE` and exact CORS origins for that arrangement and use the narrowest cookie domain that works. Never commit the client secret or CSRF secret. Restart API containers after changing environment values.

Once those credentials and URLs are set, Google sign-in and linking are available without additional frontend configuration.
