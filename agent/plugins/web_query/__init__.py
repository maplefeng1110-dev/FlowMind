import asyncio
from typing import Any, Dict, List
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from utils.plugin_result import error_result, success_result

DEFAULT_TIMEOUT = 15
USER_AGENT = "FlowMind-WebQuery/1.0"
SUPPORTED_MODES = {"search", "fetch_page", "extract_structured", "fetch_many", "selector_extract"}


def _request_page(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.text


def _parse_page_summary(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    paragraphs = [text for text in paragraphs if text]
    headings = [item.get_text(" ", strip=True) for item in soup.select("h1, h2, h3")]
    headings = [text for text in headings if text][:10]

    return {
        "mode": "fetch_page",
        "url": url,
        "title": title,
        "summary": " ".join(paragraphs[:3])[:500],
        "paragraph_count": len(paragraphs),
        "headings": headings,
        "preview": paragraphs[:5],
    }


def _fetch_search_results(query: str, site: str, top_k: int) -> Dict[str, Any]:
    scoped_query = f"site:{site} {query}" if site else query
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(scoped_query)}"
    html = _request_page(url)

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for result in soup.select(".result")[:top_k]:
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if not link:
            continue
        items.append(
            {
                "title": link.get_text(" ", strip=True),
                "url": link.get("href", ""),
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            }
        )

    return {
        "mode": "search",
        "query": query,
        "site": site or "",
        "top_k": top_k,
        "results": items,
        "summary": f"共获取到 {len(items)} 条搜索结果。",
    }


def _fetch_page(url: str) -> Dict[str, Any]:
    html = _request_page(url)
    return _parse_page_summary(url, html)


def _extract_structured(url: str, selectors: Dict[str, str] | None = None) -> Dict[str, Any]:
    html = _request_page(url)
    soup = BeautifulSoup(html, "html.parser")
    base = _parse_page_summary(url, html)
    links = []
    for link in soup.select("a[href]")[:20]:
        links.append({"text": link.get_text(" ", strip=True), "href": link.get("href", "")})

    structured = {
        "mode": "extract_structured",
        "url": url,
        "title": base["title"],
        "summary": base["summary"],
        "headings": base["headings"],
        "links": links,
    }

    if selectors:
        fields: Dict[str, List[str]] = {}
        for field, selector in selectors.items():
            selector_text = str(selector or "").strip()
            if not selector_text:
                continue
            fields[str(field)] = [
                node.get_text(" ", strip=True)
                for node in soup.select(selector_text)[:20]
                if node.get_text(" ", strip=True)
            ]
        structured["fields"] = fields

    return structured


def _fetch_many(urls: List[str], top_k: int) -> Dict[str, Any]:
    normalized_urls = [str(url).strip() for url in urls if str(url).strip()]
    pages = []
    for url in normalized_urls[:top_k]:
        try:
            pages.append(_fetch_page(url))
        except requests.RequestException as exc:
            pages.append({"url": url, "status": "error", "error": str(exc)})
    return {
        "mode": "fetch_many",
        "count": len(pages),
        "pages": pages,
    }


def _selector_extract(url: str, selector: str, attribute: str, limit: int) -> Dict[str, Any]:
    html = _request_page(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for node in soup.select(selector)[:limit]:
        if attribute:
            value = node.get(attribute)
            if value:
                items.append(str(value))
            continue
        text = node.get_text(" ", strip=True)
        if text:
            items.append(text)
    return {
        "mode": "selector_extract",
        "url": url,
        "selector": selector,
        "attribute": attribute,
        "count": len(items),
        "items": items,
    }


async def run(
    query: str = "",
    site: str = "",
    mode: str = "search",
    url: str = "",
    top_k: int = 3,
    urls: List[str] | None = None,
    selector: str = "",
    attribute: str = "",
    selectors: Dict[str, str] | None = None,
    limit: int = 5,
) -> dict:
    try:
        top_k = int(top_k)
        limit = int(limit)
    except (TypeError, ValueError):
        return error_result("top_k and limit must be integers")

    if mode not in SUPPORTED_MODES:
        return error_result("Unsupported web query mode", error=f"mode must be one of {sorted(SUPPORTED_MODES)}")

    if top_k <= 0 or top_k > 10:
        return error_result("top_k must be between 1 and 10")
    if limit <= 0 or limit > 50:
        return error_result("limit must be between 1 and 50")

    if mode == "search" and not query:
        return error_result("query is required when mode is search")
    if mode in {"fetch_page", "extract_structured", "selector_extract"} and not url:
        return error_result("url is required for the selected mode")
    if mode == "fetch_many" and not urls:
        return error_result("urls is required when mode is fetch_many")
    if mode == "selector_extract" and not selector:
        return error_result("selector is required when mode is selector_extract")

    try:
        if mode == "search":
            result = await asyncio.to_thread(_fetch_search_results, query, site, top_k)
        elif mode == "fetch_page":
            result = await asyncio.to_thread(_fetch_page, url)
        elif mode == "extract_structured":
            result = await asyncio.to_thread(_extract_structured, url, selectors)
        elif mode == "fetch_many":
            result = await asyncio.to_thread(_fetch_many, urls or [], top_k)
        else:
            result = await asyncio.to_thread(_selector_extract, url, selector, attribute, limit)
        return success_result(data=result)
    except requests.RequestException as exc:
        return error_result("Failed to query web content", error=str(exc))
    except Exception as exc:
        return error_result("Unexpected web query error", error=str(exc))
