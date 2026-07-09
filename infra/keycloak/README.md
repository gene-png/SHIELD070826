# Keycloak realm

`shield-realm.json` is imported automatically when the `keycloak` service starts (`docker-compose.yml` mounts this directory at `/opt/keycloak/data/import` and the service runs `start-dev --import-realm`).

## What the realm provides

- **Realm:** `shield`. SSO session idle = 30 min (`ssoSessionIdleTimeout`), max = 24 h (`ssoSessionMaxLifespan`) in `shield-realm.json`. These are Keycloak-native settings and only take effect once OIDC federation is switched on (v1.x). In v1 the API issues its own JWTs and does not consume Keycloak tokens, so these session limits are inert. NOTE: the app-side idle-timeout / forced-reauth knobs are NOT enforced by SHIELD's own JWT path — see DECISIONS.md D-017.
- **Realm roles:** `admin` (Kentro consultant), `reviewer` (read-only auditor), `client` (default).
- **Clients:**
  - `shield-web` — public OIDC client with PKCE (S256). Maps realm roles into the access token as `roles` and includes `shield-api` in the `aud` claim so the API can validate without an extra lookup.
  - `shield-api` — bearer-only client. The API uses this client ID to validate inbound tokens once OIDC federation switches on in v1.x.
- **Bootstrap user:** `dev-admin@shield.local` / `DevAdminPass2026!` (temporary password — must change on first login). For local development only.
- **Brute-force protection:** 10 failed attempts → lockout (matches Master Spec §4.5; both SHIELD's own login path and Keycloak's enforce the same counter).
- **Password policy:** 12+ chars, not equal to username or email (matches `apps/api/app/security/password.py`).

## v1 vs v1.x federation

For v1, the FastAPI API issues its own JWTs (see `apps/api/app/security/jwt.py`); Keycloak is deployed but the API does not consume Keycloak tokens yet. Flipping to Keycloak federation in v1.x requires no schema migration:

- The web app already uses NextAuth, which can switch its provider from Credentials to Keycloak by changing one config object.
- The API's audience (`KEYCLOAK_AUDIENCE=shield-api`) and issuer claims are stable across the switch — the same JWTs validate.

## Regenerating

If you change the realm in the Keycloak admin console, export it back with:

```bash
docker compose exec keycloak /opt/keycloak/bin/kc.sh export \
  --file /opt/keycloak/data/import/shield-realm.json \
  --realm shield
```

Then commit the diff.
