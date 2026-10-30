"""Research provenance and a bounded public-HTML reader (no API credentials)."""

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urljoin, urlunsplit

from src.shared._workflow_store import WorkflowError, atomic_write, read_json
from src.shared.openrouter_client import response_text

MAX_BYTES = 2_000_000
MAX_TEXT = 20_000


class PageText(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "form", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipped = []
        self.parts = []
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipped.append(tag)
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in self.skipped:
            self.skipped = self.skipped[:self.skipped.index(tag)]
        if tag == "title":
            self.in_title = False

    def handle_data(self, text):
        if self.in_title:
            self.title.append(text)
        elif not self.skipped:
            self.parts.append(text)


def _public_target(url):
    try:
        if not isinstance(url, str) or len(url) > 4096:
            raise ValueError("Source URL is invalid or too long")
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Only public HTTP(S) URLs without credentials are allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in (80, 443):
            raise ValueError("Only standard web ports are allowed")
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global or ipaddress.ip_address(row[4][0]).is_multicast for row in addresses):
            raise ValueError("Private, loopback and reserved addresses are not allowed")
        return parsed, port, addresses[0][4][0]
    except (ValueError, OSError) as exc:
        raise WorkflowError("SOURCE_URL_REJECTED", str(exc)) from None


def fetch_html(url):
    original = url
    for _ in range(4):
        parsed, port, address = _public_target(url)
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, port, timeout=15)
        # Pin the already checked IP, keeping Host and TLS SNI on the hostname.
        connection._create_connection = lambda target, timeout, source_address=None, ip=address, p=port: socket.create_connection((ip, p), timeout, source_address)
        try:
            connection.request("GET", urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
                               headers={"User-Agent": "AI-Create-Video/Checkpoint4", "Accept": "text/html"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise WorkflowError("SOURCE_FETCH_FAILED", "Redirect has no location")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise WorkflowError("SOURCE_FETCH_FAILED", f"Source returned HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml")):
                raise WorkflowError("SOURCE_UNSUPPORTED", "Only public HTML is supported")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise WorkflowError("SOURCE_TOO_LARGE", "Source exceeds 2 MB")
            match = re.search(r"charset=([\w-]+)", content_type)
            encoding = match.group(1) if match else "utf-8"
            parser = PageText()
            parser.feed(raw.decode(encoding, errors="replace"))
            full_text = " ".join(" ".join(parser.parts).split())
            text = full_text[:MAX_TEXT]
            if len(text) < 200:
                raise WorkflowError("SOURCE_EMPTY", "Too little readable HTML text")
            return dict(url=original, final_url=url, title=" ".join(" ".join(parser.title).split()),
                        text=text, sha256=hashlib.sha256(text.encode()).hexdigest(),
                        raw_sha256=hashlib.sha256(raw).hexdigest(), truncated=len(full_text) > MAX_TEXT,
                        retrieved_at=datetime.now(timezone.utc).isoformat(), status="success")
        except (OSError, http.client.HTTPException, LookupError) as exc:
            raise WorkflowError("SOURCE_FETCH_FAILED", str(exc)) from None
        finally:
            connection.close()
    raise WorkflowError("SOURCE_FETCH_FAILED", "Too many redirects")


def citations(response):
    if response.get('usage',{}).get('billing_source') == 'codex_subscription':
        response_text(response)
        backend='codex'
        provider='Codex'
        trace=response.get(backend,{})
        if not trace.get('search_performed') or not isinstance(trace.get('search_count'),int) or trace['search_count']<1:
            raise WorkflowError('RESEARCH_UNVERIFIED',provider+' chưa có bằng chứng tìm kiếm trực tiếp.')
        found=[];seen=set()
        for source in trace.get('sources',[]):
            if not isinstance(source,dict):continue
            url=source.get('url');excerpt=source.get('excerpt')
            if isinstance(url,str) and url not in seen and isinstance(excerpt,str) and excerpt.strip():
                found.append(dict(url=url,title=source.get('title',''),search_excerpt=excerpt,discovery=backend+'_search_candidate'))
                seen.add(url)
        if not found:raise WorkflowError('RESEARCH_UNVERIFIED',provider+' không trả nguồn tìm kiếm để tải và xác minh độc lập.')
        return found[:10]
    # OpenRouter sources require transport-supplied citation metadata.
    response_text(response)
    found = []
    seen = set()
    for annotation in response["choices"][0]["message"].get("annotations", []):
        if annotation.get("type") != "url_citation":
            continue
        item = annotation.get("url_citation", {})
        url, excerpt = item.get("url"), item.get("content", annotation.get("content"))
        if isinstance(url, str) and isinstance(excerpt, str) and excerpt.strip() and url not in seen:
            found.append(dict(url=url, title=item.get("title", ""), search_excerpt=excerpt))
            seen.add(url)
    usage = response.get("usage", {})
    counts = [details["web_search_requests"]
              for name in ("server_tool_use_details", "server_tool_use")
              if isinstance(details := usage.get(name), dict) and "web_search_requests" in details]
    if (not counts or any(type(count) is not int or not 1 <= count <= 2 for count in counts)
        or len(set(counts)) != 1 or not found):
        raise WorkflowError("RESEARCH_UNVERIFIED", "Search requires citation annotations and a consistent web_search_requests count of 1–2 in usage.server_tool_use_details or usage.server_tool_use")
    return found[:10]


def collect_sources(job_dir, response, *, fetcher=None, previous=None, exclude_urls=()):
    fetcher = fetcher or fetch_html
    sources = []
    for source in previous or []:
        if source.get("status") == "success" and hashlib.sha256(source["text"].encode()).hexdigest() == source["sha256"]:
            sources.append(dict(source))
    attempts_dir = job_dir / "sources"
    attempts_dir.mkdir(exist_ok=True)
    attempts = []
    for index, candidate in enumerate(citations(response)):
        if len(sources) >= 4:
            break
        if candidate["url"] in {s["url"] for s in sources} or candidate["url"] in exclude_urls:
            continue
        cache = attempts_dir / f"fetch_{index:02}.json"
        cached_attempts = 0
        retry = False
        if cache.exists():
            item = read_json(cache)
            if item.get("url") != candidate["url"]:
                raise WorkflowError("CACHE_CORRUPT", "Cached source does not match its citation")
            if item.get("status") == "success" and hashlib.sha256(item["text"].encode()).hexdigest() != item["sha256"]:
                raise WorkflowError("CACHE_CORRUPT", "Cached source text checksum changed")
            cached_attempts = item.get("fetch_attempts", 1)
            retry = item.get("status") == "failed" and cached_attempts < 2
        if not cache.exists() or retry:
            try:
                if urlsplit(candidate['url']).path.lower().endswith('.pdf'):
                    raise WorkflowError('SOURCE_UNSUPPORTED', 'PDF source skipped; trying the remaining HTML citations')
                item = fetcher(candidate["url"])
                if item.get("status") != "success" or hashlib.sha256(item["text"].encode()).hexdigest() != item["sha256"]:
                    raise WorkflowError("SOURCE_INVALID", "Fetched source checksum does not match text")
                item = dict(item, search_excerpt=candidate["search_excerpt"], discovery=candidate.get("discovery","openrouter_citation"))
            except WorkflowError as exc:
                item = dict(url=candidate["url"], status="failed", error=str(exc))
            item["fetch_attempts"] = cached_attempts + 1
            atomic_write(cache, item)
        attempts.append(item)
        if item["status"] == "success":
            sources.append(item)
    for index, item in enumerate(sources):
        item["source_id"] = f"source_{index + 1:02}"
    atomic_write(job_dir / "source_attempts.json", attempts)
    if len(sources) < 2:
        raise WorkflowError("RESEARCH_INSUFFICIENT", "Need at least two successfully read HTML sources")
    return sources
