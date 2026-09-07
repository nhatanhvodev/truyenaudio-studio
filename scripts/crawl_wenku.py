#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wenku Novel Crawler (https://wenku.read.qq.com/)
Cào và tải dữ liệu tiểu thuyết từ nền tảng Wenku (QQ Reading).
Hỗ trợ:
- Tải metadata, ảnh bìa, danh sách chương
- Tải nội dung chương (xuất TXT gộp, thư mục từng chương, JSON)
- Phát hiện chương VIP / khoá bản quyền
- Bảng xếp hạng (Nguyệt phiếu, Hot, Bốc hơi, Miễn phí, Phong thần)
- Tương thích thư mục nguồn của Truyen Audio Studio
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import httpx
    from lxml import html
except ImportError:
    print("Thiếu thư viện 'httpx' hoặc 'lxml'. Vui lòng chạy: pip install httpx lxml")
    sys.exit(1)

# Đảm bảo in tiếng Việt và ký tự tiếng Trung không bị lỗi trên Windows cmd/powershell
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sanitize_filename(name: str) -> str:
    """Loại bỏ các ký tự không hợp lệ cho tên file/thư mục trên Windows/Linux."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


class WenkuCrawler:
    BASE_URL = "https://wenku.read.qq.com"

    RANK_TYPES = {
        "monthly": ("553426_1", "Bảng Nguyệt Phiếu (月票榜)"),
        "hot": ("553428_1", "Bảng Đang Hot (热门榜)"),
        "soaring": ("553430_1", "Bảng Tăng Trưởng (飙升榜)"),
        "god": ("553433_1", "Bảng Phong Thần (封神榜)"),
        "free": ("553434", "Bảng Sách Miễn Phí (免费好书)"),
        "finish": ("553436", "Bảng Hoàn Thành (完本好书)"),
    }

    def __init__(
        self,
        output_dir: str = "data/crawled_novels",
        delay: float = 0.3,
        timeout: float = 15.0,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.output_dir = Path(output_dir)
        self.delay = delay
        self.timeout = timeout
        default_headers = {
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
        if headers:
            default_headers.update(headers)

        self.client = httpx.Client(
            headers=default_headers,
            follow_redirects=True,
            timeout=self.timeout,
        )

    def extract_book_id(self, input_str: str) -> str:
        """Trích xuất Book ID từ URL hoặc chuỗi số ID."""
        input_str = input_str.strip()
        if input_str.isdigit():
            return input_str

        # Các định dạng URL:
        # https://wenku.read.qq.com/detail/1059978960
        # https://wenku.read.qq.com/chapter/1059978960
        # https://wenku.read.qq.com/read/1059978960/1
        match = re.search(r"/(?:detail|chapter|read)/(\d+)", input_str)
        if match:
            return match.group(1)

        digits = re.findall(r"\d{7,15}", input_str)
        if digits:
            return digits[0]

        raise ValueError(f"Không nhận diện được Book ID từ: {input_str}")

    def fetch_book_info(self, book_id: str) -> Dict[str, Any]:
        """Lấy thông tin chi tiết và metadata của tiểu thuyết."""
        url = f"{self.BASE_URL}/detail/{book_id}"
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Không thể truy cập {url} (Mã trạng thái: {resp.status_code})"
            )

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
        latest_ch_el = tree.xpath(
            "//meta[@property='og:novel:latest_chapter_name']/@content"
        )
        words_el = tree.xpath(
            "//p[contains(@class, 'book-info__categories')]//span/text()"
        )

        title = title_el[0].strip() if title_el else f"book_{book_id}"
        author = author_el[0].strip() if author_el else "Unknown"
        category = category_el[0].strip() if category_el else "Unknown"
        status = status_el[0].strip() if status_el else "Unknown"
        description = "\n".join(
            p.strip() for p in desc_el if p.strip()
        ) if desc_el else ""
        cover_url = cover_el[0].strip() if cover_el else ""
        latest_chapter = latest_ch_el[0].strip() if latest_ch_el else ""
        word_count_text = words_el[0].strip() if words_el else ""

        return {
            "book_id": book_id,
            "title": title,
            "author": author,
            "category": category,
            "status": status,
            "description": description,
            "cover_url": cover_url,
            "latest_chapter": latest_chapter,
            "word_count_text": word_count_text,
            "source_url": url,
            "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def fetch_catalog(self, book_id: str) -> List[Dict[str, Any]]:
        """Lấy danh sách tất cả các chương theo thứ tự xuôi chuẩn."""
        url = f"{self.BASE_URL}/chapter/{book_id}"
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Không thể lấy danh mục chương {url} (Mã: {resp.status_code})"
            )

        tree = html.fromstring(resp.text)

        # Trang Wenku có 2 ul.book-dir: 1 ẩn (style='display:none;' là đảo ngược) và 1 hiện (xuôi)
        uls = tree.xpath(
            "//ul[contains(@class, 'book-dir') and not(contains(@style, 'display:none'))]"
        )
        if not uls:
            uls = tree.xpath("//ul[contains(@class, 'book-dir')]")

        chapters: List[Dict[str, Any]] = []
        seen_urls = set()

        if uls:
            target_ul = uls[0]
            lis = target_ul.xpath(".//li[contains(@class, 'list')]")
            for idx, li in enumerate(lis, start=1):
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
                    title_nodes[0].strip() if title_nodes else a.get("title", f"Chương {idx}")
                )

                time_nodes = a.xpath(".//p[contains(@class, 'time')]/text()")
                update_time = time_nodes[0].strip() if time_nodes else ""

                # Phát hiện gắn nhãn VIP hoặc yêu cầu APP
                is_app_free = bool(a.xpath(".//span[contains(@class, 'app-free')]"))

                # Trích xuất số thứ tự chương từ url /read/<book_id>/<num>
                match = re.search(r"/read/\d+/(\d+)", href)
                order_num = int(match.group(1)) if match else idx

                chapters.append({
                    "index": order_num,
                    "title": ch_title,
                    "url": href,
                    "update_time": update_time,
                    "is_app_free": is_app_free,
                })

        # Sắp xếp lại theo số thứ tự index
        chapters.sort(key=lambda x: x["index"])
        return chapters

    def fetch_chapter(
        self,
        book_id: str,
        chapter_index: int,
        chapter_url: Optional[str] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Tải nội dung 1 chương cụ thể với cơ chế tự động thử lại."""
        if not chapter_url:
            chapter_url = f"{self.BASE_URL}/read/{book_id}/{chapter_index}"

        for attempt in range(1, max_retries + 1):
            try:
                resp = self.client.get(chapter_url)
                if resp.status_code == 200:
                    tree = html.fromstring(resp.text)

                    # Tiêu đề chương
                    title_el = tree.xpath(
                        "//h1[contains(@class, 'chapter-title')]/text()"
                    )
                    chapter_title = (
                        title_el[0].strip()
                        if title_el
                        else f"第{chapter_index}章"
                    )

                    # Nội dung các đoạn văn
                    paragraphs = tree.xpath("//div[@id='article']//p/text()")
                    cleaned_paragraphs = [
                        p.strip() for p in paragraphs if p.strip()
                    ]

                    # Kiểm tra xem có bị paywall/VIP không
                    is_locked = (
                        bool(tree.xpath("//div[contains(@class, 'purchase')]"))
                        or any("上QQ阅读APP免费读" in p for p in paragraphs)
                        or any("登录订阅本章" in p for p in paragraphs)
                    )

                    return {
                        "index": chapter_index,
                        "title": chapter_title,
                        "url": chapter_url,
                        "paragraphs": cleaned_paragraphs,
                        "content": "\n\n".join(cleaned_paragraphs),
                        "is_locked": is_locked,
                        "word_count": sum(len(p) for p in cleaned_paragraphs),
                        "success": True,
                    }
                elif resp.status_code in (429, 503):
                    time.sleep(1.0 * attempt)
                else:
                    break
            except Exception as e:
                if attempt == max_retries:
                    return {
                        "index": chapter_index,
                        "title": f"Chương {chapter_index}",
                        "url": chapter_url,
                        "paragraphs": [],
                        "content": "",
                        "is_locked": False,
                        "word_count": 0,
                        "success": False,
                        "error": str(e),
                    }
                time.sleep(0.5 * attempt)

        return {
            "index": chapter_index,
            "title": f"Chương {chapter_index}",
            "url": chapter_url,
            "paragraphs": [],
            "content": "",
            "is_locked": False,
            "word_count": 0,
            "success": False,
            "error": f"Lỗi HTTP status {resp.status_code}",
        }

    def fetch_rankings(self, rank_type: str = "hot") -> List[Dict[str, Any]]:
        """Lấy danh sách tiểu thuyết từ Bảng xếp hạng Wenku."""
        if rank_type not in self.RANK_TYPES:
            rank_type = "hot"

        rank_id, label = self.RANK_TYPES[rank_type]
        url = f"{self.BASE_URL}/rank/{rank_id}"
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Không thể lấy bảng xếp hạng {url} (Mã: {resp.status_code})")

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
                    "book_id": b_id,
                    "title": b_title,
                    "url": f"{self.BASE_URL}/detail/{b_id}",
                })

        return results

    def save_cover_image(self, cover_url: str, target_path: Path) -> bool:
        """Tải và lưu ảnh bìa tiểu thuyết."""
        if not cover_url:
            return False
        try:
            r = self.client.get(cover_url)
            if r.status_code == 200:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(r.content)
                return True
        except Exception:
            pass
        return False

    def crawl_novel(
        self,
        url_or_id: str,
        start_chapter: int = 1,
        end_chapter: Optional[int] = None,
        output_format: str = "all",
        save_cover: bool = True,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Quy trình cào toàn diện tiểu thuyết và xuất file."""
        book_id = self.extract_book_id(url_or_id)
        print(f"\n[*] Đang lấy thông tin tiểu thuyết (Book ID: {book_id})...")
        info = self.fetch_book_info(book_id)
        title = info["title"]
        author = info["author"]
        print(f"[+] Tên truyện: {title}")
        print(f"[+] Tác giả:   {author}")
        print(f"[+] Thể loại:  {info['category']} | Trạng thái: {info['status']}")

        print(f"[*] Đang lấy danh mục chương...")
        catalog = self.fetch_catalog(book_id)
        total_chapters = len(catalog)
        print(f"[+] Tìm thấy tổng cộng {total_chapters} chương.")

        if total_chapters == 0:
            print("[-] Không tìm thấy chương nào để tải.")
            return {"info": info, "catalog": [], "chapters": []}

        if end_chapter is None or end_chapter > total_chapters:
            end_chapter = total_chapters

        # Lọc danh sách chương cần tải
        target_chapters = [
            c for c in catalog if start_chapter <= c["index"] <= end_chapter
        ]

        # Chuẩn bị thư mục lưu trữ
        safe_title = sanitize_filename(f"{title}_{book_id}")
        book_dir = self.output_dir / safe_title
        book_dir.mkdir(parents=True, exist_ok=True)

        if save_cover and info["cover_url"]:
            cover_ext = ".webp" if "webp" in info["cover_url"].lower() else ".jpg"
            cover_file = book_dir / f"cover{cover_ext}"
            if self.save_cover_image(info["cover_url"], cover_file):
                print(f"[+] Đã tải ảnh bìa: {cover_file.name}")

        chapters_dir = book_dir / "chapters"
        if output_format in ("all", "folder"):
            chapters_dir.mkdir(parents=True, exist_ok=True)

        downloaded_chapters: List[Dict[str, Any]] = []
        vip_count = 0
        failed_count = 0

        print(
            f"[*] Bắt đầu tải {len(target_chapters)} chương "
            f"(từ chương {start_chapter} đến {end_chapter})...\n"
        )

        single_txt_path = book_dir / f"{sanitize_filename(title)}.txt"
        single_txt_file = None
        if output_format in ("all", "single"):
            single_txt_file = open(single_txt_path, "w", encoding="utf-8")
            # Ghi tiêu đề & metadata vào đầu file
            single_txt_file.write(f"{title}\n")
            single_txt_file.write(f"Tác giả: {author}\n")
            single_txt_file.write(f"Thể loại: {info['category']}\n")
            single_txt_file.write(f"Trạng thái: {info['status']}\n")
            single_txt_file.write(f"Nguồn: {info['source_url']}\n")
            single_txt_file.write(f"{'='*60}\n")
            single_txt_file.write(f"Giới thiệu:\n{info['description']}\n")
            single_txt_file.write(f"{'='*60}\n\n")

        try:
            for i, ch_meta in enumerate(target_chapters, start=1):
                ch_idx = ch_meta["index"]
                ch_url = ch_meta["url"]

                ch_data = self.fetch_chapter(book_id, ch_idx, ch_url)
                if not ch_data["success"]:
                    failed_count += 1
                    status_str = "[THẤT BẠI]"
                elif ch_data["is_locked"]:
                    vip_count += 1
                    status_str = "[VIP/BẢN QUYỀN - CHỈ CÓ ĐOẠN XEM TRƯỚC]"
                else:
                    status_str = f"[{ch_data['word_count']} chữ]"

                ch_title = ch_data["title"]
                print(f"[{i}/{len(target_chapters)}] {ch_title} {status_str}")

                # Lưu vào thư mục từng chương (rất thuận tiện import vào Truyen Audio Studio)
                if output_format in ("all", "folder") and ch_data["content"]:
                    file_name = f"{ch_idx:04d}_{sanitize_filename(ch_title)}.txt"
                    ch_path = chapters_dir / file_name
                    with open(ch_path, "w", encoding="utf-8") as cf:
                        cf.write(f"{ch_title}\n\n")
                        cf.write(ch_data["content"])
                        cf.write("\n")

                # Ghi vào file đơn gộp
                if single_txt_file and ch_data["content"]:
                    single_txt_file.write(f"{ch_title}\n\n")
                    single_txt_file.write(ch_data["content"])
                    single_txt_file.write("\n\n" + ("-" * 40) + "\n\n")

                downloaded_chapters.append(ch_data)

                if on_progress:
                    on_progress(i, len(target_chapters), ch_data)

                # Delay lịch sự tránh bị chặn
                if self.delay > 0 and i < len(target_chapters):
                    time.sleep(self.delay)

        finally:
            if single_txt_file:
                single_txt_file.close()

        # Lưu thông tin metadata.json
        meta_json_path = book_dir / "metadata.json"
        with open(meta_json_path, "w", encoding="utf-8") as jf:
            json.dump(
                {
                    "info": info,
                    "total_downloaded": len(downloaded_chapters),
                    "vip_chapters_count": vip_count,
                    "failed_count": failed_count,
                    "chapters": [
                        {
                            "index": c["index"],
                            "title": c["title"],
                            "url": c["url"],
                            "is_locked": c.get("is_locked", False),
                            "word_count": c.get("word_count", 0),
                        }
                        for c in downloaded_chapters
                    ],
                },
                jf,
                ensure_ascii=False,
                indent=2,
            )

        print("\n" + "=" * 60)
        print(f"[+] Hoàn tất cào tiểu thuyết: {title}")
        print(f"[+] Thư mục kết quả: {book_dir.resolve()}")
        if output_format in ("all", "single"):
            print(f"[+] File TXT gộp: {single_txt_path.name}")
        if output_format in ("all", "folder"):
            print(f"[+] Thư mục từng chương: chapters/ ({len(downloaded_chapters)} files)")
        print(f"[+] File metadata: metadata.json")
        if vip_count > 0:
            print(
                f"[!] Chú ý: Có {vip_count} chương thuộc diện khoá VIP/cần App "
                f"(đã lấy phần trích đoạn miễn phí nếu có)."
            )
        print("=" * 60 + "\n")

        return {
            "book_dir": str(book_dir),
            "title": title,
            "chapters_count": len(downloaded_chapters),
            "vip_count": vip_count,
            "single_txt": str(single_txt_path) if single_txt_file else None,
        }


def interactive_menu():
    """Giao diện dòng lệnh tương tác (Interactive CLI) thân thiện bằng tiếng Việt."""
    crawler = WenkuCrawler()

    while True:
        print("\n" + "=" * 62)
        print("         CRAWLER TIỂU THUYẾT WENKU (wenku.read.qq.com)")
        print("=" * 62)
        print("1. Tải truyện theo Link hoặc Book ID")
        print("2. Xem Bảng xếp hạng và chọn truyện để tải")
        print("3. Chỉ xem thông tin truyện & danh mục chương")
        print("4. Thoát")
        print("-" * 62)

        choice = input("Vui lòng chọn (1-4): ").strip()
        if choice == "1":
            inp = input("\nNhập Link truyện hoặc Book ID (Ví dụ: 1059978960): ").strip()
            if not inp:
                continue
            try:
                book_id = crawler.extract_book_id(inp)
                info = crawler.fetch_book_info(book_id)
                catalog = crawler.fetch_catalog(book_id)
                print(f"\n[+] Tên truyện: {info['title']}")
                print(f"[+] Tác giả:   {info['author']}")
                print(f"[+] Tổng số chương: {len(catalog)}")

                range_str = input(
                    f"Nhập phạm vi chương muốn tải (Enter để tải hết 1-{len(catalog)}, hoặc ví dụ: 1-20): "
                ).strip()
                start_ch = 1
                end_ch = len(catalog)
                if range_str:
                    parts = range_str.split("-")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        start_ch = int(parts[0])
                        end_ch = int(parts[1])
                    elif parts[0].isdigit():
                        end_ch = int(parts[0])

                fmt_input = input(
                    "Định dạng xuất (1: Cả hai, 2: 1 file TXT gộp, 3: Thư mục từng chương): "
                ).strip()
                fmt_map = {"1": "all", "2": "single", "3": "folder"}
                selected_fmt = fmt_map.get(fmt_input, "all")

                crawler.crawl_novel(
                    book_id,
                    start_chapter=start_ch,
                    end_chapter=end_ch,
                    output_format=selected_fmt,
                )
            except Exception as e:
                print(f"[-] Đã xảy ra lỗi: {e}")

        elif choice == "2":
            print("\nChọn loại bảng xếp hạng:")
            rank_keys = list(WenkuCrawler.RANK_TYPES.keys())
            for idx, rk in enumerate(rank_keys, start=1):
                print(f"{idx}. {WenkuCrawler.RANK_TYPES[rk][1]}")

            r_choice = input(f"Chọn (1-{len(rank_keys)}): ").strip()
            if r_choice.isdigit() and 1 <= int(r_choice) <= len(rank_keys):
                selected_rank = rank_keys[int(r_choice) - 1]
                print(f"\n[*] Đang lấy danh sách {WenkuCrawler.RANK_TYPES[selected_rank][1]}...")
                try:
                    books = crawler.fetch_rankings(selected_rank)
                    if not books:
                        print("[-] Không tìm thấy truyện.")
                        continue
                    print("\nDanh sách truyện:")
                    for idx, b in enumerate(books[:20], start=1):
                        print(f"{idx:2d}. [{b['book_id']}] {b['title']}")

                    pick = input("\nNhập số thứ tự truyện muốn tải (hoặc 0 để quay lại): ").strip()
                    if pick.isdigit() and 1 <= int(pick) <= len(books):
                        chosen_book = books[int(pick) - 1]
                        limit_input = input(
                            "Tải bao nhiêu chương đầu? (Enter để tải 10 chương đầu thử nghiệm): "
                        ).strip()
                        limit = int(limit_input) if limit_input.isdigit() else 10
                        crawler.crawl_novel(
                            chosen_book["book_id"],
                            start_chapter=1,
                            end_chapter=limit,
                            output_format="all",
                        )
                except Exception as e:
                    print(f"[-] Lỗi: {e}")

        elif choice == "3":
            inp = input("\nNhập Link truyện hoặc Book ID: ").strip()
            if not inp:
                continue
            try:
                book_id = crawler.extract_book_id(inp)
                info = crawler.fetch_book_info(book_id)
                catalog = crawler.fetch_catalog(book_id)
                print("\n" + "=" * 50)
                print(f"Tiêu đề:       {info['title']}")
                print(f"Tác giả:       {info['author']}")
                print(f"Thể loại:      {info['category']}")
                print(f"Trạng thái:    {info['status']}")
                print(f"Số chữ:        {info['word_count_text']}")
                print(f"Chương mới:    {info['latest_chapter']}")
                print(f"Tổng số chương:{len(catalog)}")
                print(f"Ảnh bìa:       {info['cover_url']}")
                print("-" * 50)
                print("Giới thiệu:")
                print(info["description"][:300] + ("..." if len(info["description"]) > 300 else ""))
                print("=" * 50)
                if catalog:
                    print("\n3 chương đầu tiên:")
                    for c in catalog[:3]:
                        print(f"  - [{c['index']}] {c['title']}")
                    print("3 chương mới nhất:")
                    for c in catalog[-3:]:
                        print(f"  - [{c['index']}] {c['title']}")
            except Exception as e:
                print(f"[-] Lỗi: {e}")

        elif choice == "4":
            print("Tạm biệt!")
            break


def main():
    parser = argparse.ArgumentParser(
        description="Wenku Novel Crawler (https://wenku.read.qq.com/)"
    )
    parser.add_argument(
        "--url",
        "-u",
        help="Link truyện trên Wenku (Ví dụ: https://wenku.read.qq.com/detail/1059978960)",
    )
    parser.add_argument(
        "--id",
        "-i",
        help="Book ID tiểu thuyết (Ví dụ: 1059978960)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="data/crawled_novels",
        help="Thư mục lưu trữ (mặc định: data/crawled_novels)",
    )
    parser.add_argument(
        "--range",
        "-r",
        help="Phạm vi chương muốn tải, dạng 'start-end' hoặc 'count' (Ví dụ: '1-50' hoặc '20')",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["all", "single", "folder"],
        default="all",
        help="Định dạng xuất: 'single' (1 file TXT gộp), 'folder' (mỗi chương 1 file TXT), 'all' (cả hai)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Thời gian nghỉ giữa mỗi chương tính bằng giây (mặc định: 0.3s)",
    )
    parser.add_argument(
        "--rank",
        choices=list(WenkuCrawler.RANK_TYPES.keys()),
        help="Xem bảng xếp hạng (monthly, hot, soaring, god, free, finish)",
    )

    args = parser.parse_args()

    # Nếu không truyền tham số nào, mở menu tương tác
    if len(sys.argv) == 1:
        interactive_menu()
        return

    crawler = WenkuCrawler(output_dir=args.output, delay=args.delay)

    if args.rank:
        books = crawler.fetch_rankings(args.rank)
        label = WenkuCrawler.RANK_TYPES[args.rank][1]
        print(f"\n=== BẢNG XẾP HẠNG: {label} ===")
        for idx, b in enumerate(books, start=1):
            print(f"{idx:2d}. [{b['book_id']}] {b['title']} -> {b['url']}")
        return

    target = args.url or args.id
    if not target:
        print("[-] Vui lòng chỉ định --url hoặc --id. Sử dụng --help để xem hướng dẫn.")
        sys.exit(1)

    start_ch = 1
    end_ch = None
    if args.range:
        parts = args.range.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            start_ch = int(parts[0])
            end_ch = int(parts[1])
        elif parts[0].isdigit():
            end_ch = int(parts[0])

    crawler.crawl_novel(
        target,
        start_chapter=start_ch,
        end_chapter=end_ch,
        output_format=args.format,
    )


if __name__ == "__main__":
    main()
