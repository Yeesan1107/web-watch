// Cloudflare Worker for Web Watch LINE group auto-subscription.
// Bind a KV namespace named GROUPS and add secret API_KEY.
// Set this Worker's URL as the LINE Messaging API webhook URL.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // GitHub Actions reads the current subscriber group IDs here.
    if (request.method === "GET" && url.pathname === "/groups") {
      if (request.headers.get("Authorization") !== `Bearer ${env.API_KEY}`) {
        return new Response("Unauthorized", { status: 401 });
      }
      const list = await env.GROUPS.list({ prefix: "group:" });
      return Response.json({ groups: list.keys.map(k => k.name.slice(6)) });
    }

    // LINE sends join/leave events here.
    if (request.method === "POST" && url.pathname === "/webhook") {
      const body = await request.json();
      for (const event of body.events || []) {
        const groupId = event.source?.groupId;
        if (!groupId) continue;
        if (event.type === "join") {
          await env.GROUPS.put(`group:${groupId}`, new Date().toISOString());
        } else if (event.type === "leave") {
          await env.GROUPS.delete(`group:${groupId}`);
        }
      }
      return new Response("OK");
    }

    return new Response("Web Watch group registry", { status: 200 });
  }
};
