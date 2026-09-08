#!/usr/bin/env python3
"""下载公开 PDF，并拒绝被伪装成 PDF 的 HTML 错误页面。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        add_help=False,
        usage="download_pdf.py --url 官方_PDF_直链 --output 目标文件.pdf [选项]",
    )
    parser._optionals.title = "选项"
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出")
    parser.add_argument("--url", required=True, help="官方 PDF 直链")
    parser.add_argument("--output", required=True, help="目标 .pdf 文件路径")
    parser.add_argument("--referer", help="官方搜索结果页或发行人页面")
    parser.add_argument("--timeout", type=float, default=60.0, help="请求超时秒数")
    parser.add_argument("--max-mib", type=int, default=250, help="最大文件大小（MiB）")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖现有文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output).expanduser().resolve()
    if output.suffix.lower() != ".pdf":
        raise SystemExit("拒绝写入：输出路径必须以 .pdf 结尾")
    if output.exists() and not args.overwrite:
        raise SystemExit(f"拒绝覆盖现有文件：{output}")

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
    }
    if args.referer:
        if "\r" in args.referer or "\n" in args.referer:
            raise SystemExit("来源页地址无效")
        headers["Referer"] = args.referer

    request = urllib.request.Request(args.url, headers=headers)
    max_bytes = args.max_mib * 1024 * 1024
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            status = getattr(response, "status", None) or 200
            if status < 200 or status >= 300:
                raise RuntimeError(f"HTTP 状态码异常：{status}")

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise RuntimeError(
                    f"Content-Length 超过 --max-mib 限制（{args.max_mib} MiB）"
                )

            digest = hashlib.sha256()
            size = 0
            head = b""
            tail = b""
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{output.name}.", suffix=".part", dir=output.parent,
                delete=False,
            ) as temporary:
                temp_path = Path(temporary.name)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError(
                            f"下载内容超过 --max-mib 限制（{args.max_mib} MiB）"
                        )
                    if len(head) < 1024:
                        head = (head + chunk)[:1024]
                    tail = (tail + chunk)[-65536:]
                    digest.update(chunk)
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())

            if size == 0:
                raise RuntimeError("下载文件为空")
            if b"%PDF-" not in head:
                sample = head[:120].decode("utf-8", errors="replace")
                raise RuntimeError(f"响应不是 PDF，开头内容为：{sample!r}")

            os.replace(temp_path, output)
            temp_path = None
            result = {
                "ok": True,
                "requested_url": args.url,
                "final_url": response.geturl(),
                "output": str(output),
                "bytes": size,
                "sha256": digest.hexdigest(),
                "content_type": response.headers.get_content_type(),
                "eof_marker": b"%%EOF" in tail,
            }
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return 0
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"下载失败：{exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
