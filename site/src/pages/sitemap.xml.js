// Five pages do not need a sitemap integration.
const paths = ["/", "/app/", "/extension/", "/download/", "/liner-notes/"];

export const GET = ({ site }) =>
  new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
      paths.map((path) => `  <url><loc>${new URL(path, site)}</loc></url>\n`).join("") +
      `</urlset>\n`,
    { headers: { "Content-Type": "application/xml; charset=utf-8" } },
  );
