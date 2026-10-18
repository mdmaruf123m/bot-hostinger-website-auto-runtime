function backendUrl(request, env) {
  const origin = (env.BACKEND_ORIGIN || "").trim().replace(/\/$/, "");
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(origin)) {
    throw new Error("BACKEND_ORIGIN must be a trusted HTTPS origin, for example https://your-botkeep-api.example.com");
  }
  const incoming = new URL(request.url);
  const suffix = incoming.pathname.replace(/^\/backend\/?/, "");
  return `${origin}/${suffix}${incoming.search}`;
}

export async function onRequest(context) {
  const { request, env } = context;
  let target;
  try {
    target = backendUrl(request, env);
  } catch (error) {
    return Response.json({ ok: false, error: error.message }, { status: 503 });
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
    return Response.json({ ok: false, error: "Bot hosting backend is unreachable. Check the HTTPS backend URL and that BotKeep is online." }, { status: 502 });
  }

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  const location = responseHeaders.get("location");
  if (location) {
    const backendOrigin = new URL(target).origin;
    if (location.startsWith(backendOrigin + "/")) {
      responseHeaders.set("location", location.slice(backendOrigin.length));
    }
  }
  responseHeaders.set("cache-control", "no-store");
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
}
