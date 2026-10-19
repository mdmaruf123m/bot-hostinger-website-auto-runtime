const FIREBASE_PROJECT_ID = "bot-36914";
const FIREBASE_API_KEY = "AIzaSyCaMi91Dp6s7ydrKMO4DwfWcJXMaViG_d4";
const CONFIG_DOCUMENT = `https://firestore.googleapis.com/v1/projects/${FIREBASE_PROJECT_ID}/databases/(default)/documents/users/panel_config?key=${FIREBASE_API_KEY}`;

function validateOrigin(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) return null;
    return url.origin;
  } catch (_) {
    return null;
  }
}

async function firestoreOrigin(env, request) {
  let cache;
  try { cache = caches.default; } catch (_) { cache = null; }
  const cacheKey = new Request(new URL("/__internal/backend-origin-cache/v1", request.url));
  if (cache) {
    try {
      const cached = await cache.match(cacheKey);
      if (cached) {
        const body = await cached.json();
        const origin = validateOrigin(body.origin);
        if (origin) return origin;
      }
    } catch (_) {}
  }

  const response = await fetch(CONFIG_DOCUMENT, { headers: { accept: "application/json" } });
  if (!response.ok) {
    if (env.BACKEND_ORIGIN) return validateOrigin(env.BACKEND_ORIGIN);
    throw new Error(`Could not read Firestore document users/panel_config (HTTP ${response.status}). Check the existing users collection read rule.`);
  }
  const doc = await response.json();
  const value = doc.fields?.backendOrigin?.stringValue;
  const origin = validateOrigin(value);
  if (!origin) throw new Error("Firestore users/panel_config must contain an HTTPS backendOrigin field, such as https://your-app.onrender.com");
  if (cache) {
    try {
      await cache.put(cacheKey, Response.json({ origin }, { headers: { "cache-control": "public, max-age=20" } }));
    } catch (_) {}
  }
  return origin;
}

async function backendUrl(request, env) {
  const origin = await firestoreOrigin(env, request);
  const incoming = new URL(request.url);
  const suffix = incoming.pathname.replace(/^\/backend\/?/, "");
  return `${origin}/${suffix}${incoming.search}`;
}

export async function onRequest(context) {
  const { request, env } = context;
  let target;
  try {
    target = await backendUrl(request, env);
  } catch (error) {
    return Response.json({ ok: false, error: error.message }, { status: 503, headers: { "cache-control": "no-store" } });
  }

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("cf-connecting-ip");
  headers.set("x-forwarded-proto", "https");
  headers.set("x-forwarded-host", new URL(request.url).host);

  let upstream;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual"
    });
  } catch (_error) {
    return Response.json({ ok: false, error: "The selected bot backend is unreachable. Check the HTTPS URL saved in the admin panel and confirm that its service is online." }, { status: 502 });
  }

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  const location = responseHeaders.get("location");
  if (location) {
    const backendOrigin = new URL(target).origin;
    if (location.startsWith(backendOrigin + "/")) responseHeaders.set("location", location.slice(backendOrigin.length));
  }
  responseHeaders.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
}
