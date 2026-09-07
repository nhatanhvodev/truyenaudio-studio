from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional
import httpx
from lxml import html

from app.modules.sources.archive_guard import ImportCandidate


class WenkuService:
    BASE_URL = "https://wenku.read.qq.com"

    RANK_TYPES = {
        "hot": ("553428_1", "Bảng Đang Hot (热门榜)"),
        "monthly": ("553426_1", "Bảng Nguyệt Phiếu (月票榜)"),
        "soaring": ("553430_1", "Bảng Tăng Trưởng (飙升榜)"),
        "god": ("553433_1", "Bảng Phong Thần (封神榜)"),
        "free": ("553434", "Bảng Sách Miễn Phí (免费好书)"),
        "finish": ("553436", "Bảng Hoàn Thành (完本好书)"),
    }

    def __init__(self, timeout: float = 15.0, delay: float = 0.2):
        self.timeout = timeout
        self.delay = delay
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,vi;q=0.7",
            "Referer": "https://wenku.read.qq.com/",
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers=self.headers,
            follow_redirects=True,
            timeout=self.timeout,
        )

    def extract_book_id(self, input_str: str) -> str:
        input_str = input_str.strip()
        if input_str.isdigit():
            return input_str
        match = re.search(r"/(?:detail|chapter|read)/(\d+)", input_str)
        if match:
            return match.group(1)
        digits = re.findall(r"\d{7,15}", input_str)
        if digits:
            return digits[0]
        raise ValueError(f"INVALID_WENKU_BOOK_ID: {input_str}")

    def get_book_info(self, url_or_id: str) -> Dict[str, Any]:
        book_id = self.extract_book_id(url_or_id)
        url = f"{self.BASE_URL}/detail/{book_id}"
        with self._client() as client:
            resp = client.get(url)
            if resp.status_code != 200:
                raise ValueError(f"WENKU_BOOK_NOT_FOUND: {resp.status_code}")
            tree = html.fromstring(resp.text)

            title_el = (
                tree.xpath("//meta[@property='og:title']/@content")
                or tree.xpath("//h1[contains(@class, 'book-info__title')]/text()")
            )
            author_el = (
                tree.xpath("//meta[@property='og:novel:author']/@content")
                or tree.xpath("//a[contains(@class, 'book-info__author')]/text()")
            )
            category_el = (
                tree.xpath("//meta[@property='og:novel:category']/@content")
                or tree.xpath("//p[contains(@class, 'book-info__categories')]//a/text()")
            )
            status_el = tree.xpath("//meta[@property='og:novel:status']/@content")
            desc_el = (
                tree.xpath("//meta[@property='og:description']/@content")
                or tree.xpath("//div[contains(@class, 'intro-content')]/text()")
            )
            cover_el = tree.xpath("//meta[@property='og:image']/@content")
            latest_ch_el = tree.xpath("//meta[@property='og:novel:latest_chapter_name']/@content")
            words_el = tree.xpath("//p[contains(@class, 'book-info__categories')]//span/text()")

            title = title_el[0].strip() if title_el else f"book_{book_id}"
            author = author_el[0].strip() if author_el else "Unknown"
            category = category_el[0].strip() if category_el else "武侠"
            status = status_el[0].strip() if status_el else "连载"
            description = "\n".join(p.strip() for p in desc_el if p.strip()) if desc_el else ""
            cover_url = cover_el[0].strip() if cover_el else ""
            latest_chapter = latest_ch_el[0].strip() if latest_ch_el else ""
            word_count_text = words_el[0].strip() if words_el else ""

        catalog = self.get_catalog(book_id)

        return {
            "bookId": book_id,
            "title": title,
            "author": author,
            "category": category,
            "status": status,
            "description": description,
            "coverUrl": cover_url,
            "latestChapter": latest_chapter,
            "wordCountText": word_count_text,
            "totalChapters": len(catalog),
            "catalog": catalog[:50],  # preview 50 chapters
            "sourceUrl": url,
        }

    def get_catalog(self, book_id: str) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/chapter/{book_id}"
        with self._client() as client:
            resp = client.get(url)
            if resp.status_code != 200:
                raise ValueError(f"WENKU_CATALOG_FAILED: {resp.status_code}")

            tree = html.fromstring(resp.text)
            uls = tree.xpath(
                "//ul[contains(@class, 'book-dir') and not(contains(@style, 'display:none'))]"
            )
            if not uls:
                uls = tree.xpath("//ul[contains(@class, 'book-dir')]")

            chapters: List[Dict[str, Any]] = []
            seen_urls = set()

            if uls:
                for idx, li in enumerate(uls[0].xpath(".//li[contains(@class, 'list')]"), start=1):
                    a_tag = li.xpath(".//a[contains(@class, 'catalogueItem')]")
                    if not a_tag:
                        continue
                    a = a_tag[0]
                    href = a.get("href", "")
                    if not href or href in seen_urls:
                        continue
                    seen_urls.add(href)
                    if href.startswith("/"):
                        href = f"{self.BASE_URL}{href}"

                    title_nodes = a.xpath(
                        ".//p[contains(@class, 'item') and not(contains(@class, 'time'))]/text()"
                    )
                    ch_title = (
                        title_nodes[0].strip()
                        if title_nodes
                        else a.get("title", f"Chương {idx}")
                    )
                    is_app_free = bool(a.xpath(".//span[contains(@class, 'app-free')]"))

                    match = re.search(r"/read/\d+/(\d+)", href)
                    order_num = int(match.group(1)) if match else idx

                    chapters.append({
                        "ordinal": order_num,
                        "title": ch_title,
                        "url": href,
                        "isAppFree": is_app_free,
                    })

            chapters.sort(key=lambda x: x["ordinal"])
            return chapters

    def fetch_chapter_content(
        self,
        book_id: str,
        chapter_ordinal: int,
        chapter_url: Optional[str] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        if not chapter_url:
            chapter_url = f"{self.BASE_URL}/read/{book_id}/{chapter_ordinal}"

        with self._client() as client:
            for attempt in range(1, max_retries + 1):
                try:
                    resp = client.get(chapter_url)
                    if resp.status_code == 200:
                        tree = html.fromstring(resp.text)
                        title_el = tree.xpath("//h1[contains(@class, 'chapter-title')]/text()")
                        ch_title = (
                            title_el[0].strip() if title_el else f"第{chapter_ordinal}章"
                        )
                        paragraphs = tree.xpath("//div[@id='article']//p/text()")
                        cleaned = [p.strip() for p in paragraphs if p.strip()]

                        is_locked = (
                            bool(tree.xpath("//div[contains(@class, 'purchase')]"))
                            or any("上QQ阅读APP免费读" in p for p in paragraphs)
                            or any("登录订阅本章" in p for p in paragraphs)
                        )

                        return {
                            "ordinal": chapter_ordinal,
                            "title": ch_title,
                            "url": chapter_url,
                            "text": "\n\n".join(cleaned),
                            "is_locked": is_locked,
                            "success": True,
                        }
                    elif resp.status_code in (429, 503):
                        time.sleep(1.0 * attempt)
                    else:
                        break
                except Exception:
                    if attempt == max_retries:
                        break
                    time.sleep(0.5 * attempt)

        return {
            "ordinal": chapter_ordinal,
            "title": f"Chương {chapter_ordinal}",
            "url": chapter_url,
            "text": "",
            "is_locked": False,
            "success": False,
        }

    def crawl_candidates(
        self,
        url_or_id: str,
        start_chapter: int = 1,
        end_chapter: Optional[int] = None,
    ) -> List[ImportCandidate]:
        book_id = self.extract_book_id(url_or_id)
        catalog = self.get_catalog(book_id)
        if not catalog:
            raise ValueError(f"WENKU_EMPTY_CATALOG: {book_id}")

        if end_chapter is None or end_chapter > len(catalog):
            end_chapter = len(catalog)

        target_chapters = [
            c for c in catalog if start_chapter <= c["ordinal"] <= end_chapter
        ]

        candidates: List[ImportCandidate] = []
        for i, ch_meta in enumerate(target_chapters):
            ordinal = ch_meta["ordinal"]
            ch_url = ch_meta["url"]
            ch_data = self.fetch_chapter_content(book_id, ordinal, ch_url)

            warnings = []
            if ch_data["is_locked"]:
                warnings.append("VIP_LOCK_PREVIEW_ONLY")
            if not ch_data["text"]:
                warnings.append("EMPTY_CHAPTER")

            candidates.append(
                ImportCandidate(
                    ordinal=ordinal,
                    title=ch_data["title"],
                    text=ch_data["text"],
                    source_path=f"wenku://{book_id}/{ordinal}",
                    warnings=tuple(warnings),
                )
            )

            if self.delay > 0 and i < len(target_chapters) - 1:
                time.sleep(self.delay)

        return candidates

    def get_rankings(self, rank_type: str = "hot") -> List[Dict[str, Any]]:
        if rank_type not in self.RANK_TYPES:
            rank_type = "hot"

        rank_id, label = self.RANK_TYPES[rank_type]
        url = f"{self.BASE_URL}/rank/{rank_id}"
        with self._client() as client:
            resp = client.get(url)
            if resp.status_code != 200:
                raise ValueError(f"WENKU_RANKINGS_FAILED: {resp.status_code}")

            tree = html.fromstring(resp.text)
            results: List[Dict[str, Any]] = []
            seen_ids = set()

            for a in tree.xpath("//a[contains(@href, '/detail/')]"):
                href = a.get("href", "")
                match = re.search(r"/detail/(\d+)", href)
                if not match:
                    continue
                b_id = match.group(1)
                if b_id in seen_ids:
                    continue

                title_nodes = (
                    a.xpath(".//h4/text()")
                    or a.xpath(".//span/text()")
                    or [a.get("title", "")]
                )
                b_title = title_nodes[0].strip() if title_nodes else ""
                if b_title:
                    seen_ids.add(b_id)
                    results.append({
                        "bookId": b_id,
                        "title": b_title,
                        "url": f"{self.BASE_URL}/detail/{b_id}",
                    })

            return results
