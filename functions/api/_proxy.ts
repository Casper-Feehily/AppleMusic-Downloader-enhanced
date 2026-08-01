// Cloudflare Pages Proxy — forwards /api/* requests to the backend server.
//
// Usage:
//   Frontend deployed on Cloudflare Pages, backend on a private server
//   exposed via Cloudflare Tunnel (cloudflared).
//
// Environment variable (set in Pages dashboard):
//   API_BACKEND_URL — backend base URL (default: https://amdl.api.cret.uk)

interface Env {
  API_BACKEND_URL?: string;
}

interface PagesContext {
  request: Request;
  env: Env;
}

export async function onRequest(context: PagesContext): Promise<Response> {
  const { request, env } = context;
  const url = new URL(request.url);

  const backendBase = env.API_BACKEND_URL || "https://amdl.api.cret.uk";
  const backendUrl = `${backendBase}${url.pathname}${url.search}`;

  return fetch(backendUrl, {
    method: request.method,
    headers: request.headers,
    body: request.body,
  });
}
// Cloudflare Pages Proxy — 将 /api/* 请求转发到后端服务器
// 
// 使用场景：
//   前端部署在 Cloudflare Pages，后端运行在私有服务器
//   通过 Cloudflare Tunnel (cloudflared) 暴露到公网
//
// 环境变量：
//   API_BACKEND_URL — 后端地址（在 Pages 控制台设置）
//                     默认 https://amdl.api.cret.uk

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  const backendBase = env.API_BACKEND_URL || "https://amdl.api.cret.uk";
  const backendUrl = `${backendBase}${url.pathname}${url.search}`;

  return fetch(backendUrl, {
    method: request.method,
    headers: request.headers,
    body: request.body,
  });
}
