"""Check what crawlers receive, including pages missing from the route registry."""
import json
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

DIST = Path(__file__).resolve().parents[1] / "dist"


class Page(HTMLParser):
    def __init__(self, file):
        super().__init__()
        self.tags = defaultdict(list)
        self.content = defaultdict(str)
        self.capture = None
        self.feed(file.read_text())

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags[tag].append(values)
        if tag == "h1" or (tag == "title" and "body" not in self.tags):
            self.capture = tag
        if tag == "script" and values.get("type") == "application/ld+json":
            self.capture = "json"

    def handle_endtag(self, tag):
        if self.capture == tag or (self.capture == "json" and tag == "script"):
            self.capture = None

    def handle_data(self, data):
        if self.capture:
            self.content[self.capture] += data


def main():
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "h": "http://www.w3.org/1999/xhtml"}
    robots = (DIST / "robots.txt").read_text().splitlines()
    sitemap, = [line.removeprefix("Sitemap: ") for line in robots if line.startswith("Sitemap: ")]
    site = sitemap.removesuffix("/sitemap.xml")
    assert urlsplit(site).scheme in {"http", "https"} and "Allow: /" in robots
    assert "User-agent: *" in robots and not any(line.startswith("Disallow: /") for line in robots)
    listed = {}
    for entry in ElementTree.parse(DIST / "sitemap.xml").findall("s:url", ns):
        url = entry.findtext("s:loc", namespaces=ns)
        assert url not in listed, url
        links = entry.findall("h:link", ns)
        listed[url] = {link.get("hreflang"): link.get("href") for link in links}
        assert len(links) == len(listed[url]), url

    pages = {site + "/" + file.relative_to(DIST).as_posix().removesuffix("index.html"): Page(file)
             for file in DIST.rglob("index.html") if file != DIST / "graph/index.html"}
    assert set(pages) == set(listed), set(pages) ^ set(listed)
    titles, descriptions = set(), set()
    edges = defaultdict(set)
    for url, page in pages.items():
        try:
            meta = {m.get("name", m.get("property")): m.get("content") for m in page.tags["meta"]}
            links = page.tags["link"]
            assert [link.get("href") for link in links if link.get("rel") == "canonical"] == [url]
            assert "noindex" not in meta.get("robots", "").lower()
            alts = [link for link in links if "hreflang" in link]
            languages = {link["hreflang"]: link["href"] for link in alts}
            lang = page.tags["html"][0]["lang"]
            assert len(alts) == len(languages) and listed[url] == languages
            assert languages[lang] == url and languages["x-default"] == languages["en"]
            assert all(listed[target] == languages for target in languages.values())
            for code, target in languages.items():
                if code != "x-default":
                    assert pages[target].tags["html"][0]["lang"] == code, target
            title, desc = page.content["title"].strip(), meta["description"]
            assert title and desc and title not in titles and desc not in descriptions, (title, desc)
            titles.add(title)
            descriptions.add(desc)
            assert len(page.tags["h1"]) == 1 and page.content["h1"].strip()
            assert all("alt" in img for img in page.tags["img"])
            assert meta["og:title"] == title and meta["og:description"] == desc and meta["og:url"] == url
            assert meta["og:image"].startswith(site + "/") and meta["twitter:card"] == "summary_large_image"
            assert (DIST / urlsplit(meta["og:image"]).path.lstrip("/")).is_file()
            schema = json.loads(page.content["json"])
            graph = {node["@type"]: node for node in schema["@graph"]}
            assert schema["@context"] == "https://schema.org" and {"Person", "WebSite"} <= graph.keys()
            segments = urlsplit(url).path.strip("/").split("/")
            if lang != "en":
                segments = segments[1:]
            if not segments or segments[0] in {"", "app", "extension", "download"}:
                app = graph["SoftwareApplication"]
                assert app["author"]["@id"] == graph["Person"]["@id"]
                assert app["offers"]["price"] == "0" and urlsplit(app["downloadUrl"]).scheme in {"http", "https"}, app
            if "/guides/" in url:
                article = graph["Article"]
                assert article["mainEntityOfPage"] == url and article["headline"] == title
                assert article["inLanguage"] == lang and article["datePublished"] <= article["dateModified"]
                assert meta["og:type"] == "article"
            if "BreadcrumbList" in graph:
                assert graph["BreadcrumbList"]["itemListElement"][-1]["item"] == url
            for anchor in page.tags["a"]:
                target = urlsplit(urljoin(url, anchor.get("href", "")))
                if target.netloc != urlsplit(site).netloc:
                    continue
                assert target.scheme == urlsplit(site).scheme, target.geturl()
                dest = site + target.path
                if dest in pages:
                    edges[url].add(dest)
                    if target.fragment:
                        assert any(tag.get("id") == unquote(target.fragment)
                                   for tags in pages[dest].tags.values() for tag in tags), target.geturl()
                else:
                    asset = DIST / unquote(target.path.lstrip("/"))
                    assert asset.is_file() or (asset / "index.html").is_file(), target.geturl()
        except (AssertionError, KeyError, ValueError) as error:
            raise AssertionError(f"SEO check failed for {url}") from error

    visited = set()
    todo = [site + "/"]
    while todo:
        current = todo.pop()
        if current not in visited:
            visited.add(current)
            todo.extend(edges[current] - visited)
    assert visited == set(pages), set(pages) - visited
    missing = Page(DIST / "404.html")
    assert any(m.get("name") == "robots" and m["content"] == "noindex" for m in missing.tags["meta"])
    print(f"SEO check: {len(pages)} reachable canonical pages, localized alternates, links and schema verified.")


if __name__ == "__main__":
    main()
