/**
 * Проксі до Gemini на Cloudflare Workers.
 *
 * Навіщо. Google відмовляє нашому серверу за геолокацією вихідного IP:
 * generateContent повертає 400 FAILED_PRECONDITION «User location is not
 * supported», хоча той самий ключ працює з домашнього комп'ютера. Запит,
 * пропущений через воркер, виходить з адрес Cloudflare, які не заблоковані.
 *
 * Ключ тут НЕ зберігається: він їде у заголовку x-goog-api-key від бота і
 * просто пересилається далі. Тому компрометація воркера не є компрометацією
 * ключа — але відкритий релей усе одно чужий, і секрет нижче захищає не ключ,
 * а нашу безкоштовну квоту запитів.
 *
 * Встановлення: Cloudflare -> Workers & Pages -> Create -> Worker, вставити
 * цей файл, задати змінну PROXY_SECRET (Settings -> Variables), Deploy.
 */

const UPSTREAM = "https://generativelanguage.googleapis.com";

export default {
  async fetch(request, env) {
    // 404, а не 403: відповідь «сюди потрібен пароль» підтверджує, що за
    // адресою щось є, і перетворює випадкового сканера на зацікавленого
    if (env.PROXY_SECRET && request.headers.get("x-proxy-secret") !== env.PROXY_SECRET) {
      return new Response("Not Found", { status: 404 });
    }

    const url = new URL(request.url);
    const headers = new Headers(request.headers);
    // секрет далі не їде, Host підставить сам fetch
    headers.delete("x-proxy-secret");
    headers.delete("host");

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    return fetch(UPSTREAM + url.pathname + url.search, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
    });
  },
};
