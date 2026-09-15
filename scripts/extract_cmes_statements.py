#!/usr/bin/env python3
"""Extract the main financial statements from CMES annual/interim reports.

The source PDFs are official disclosures already downloaded into financial-reports/.
The output is an ordered JSON dataset used to build the final workbook.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "financial-reports" / "招商局能源运输股份有限公司"
OUT_DIR = ROOT / "outputs" / "cmes_2023_2026h1"

REPORTS = {
    "2024": {
        "path": REPORT_DIR / "招商局能源运输股份有限公司_601872_2024_年度报告.pdf",
        "label": "2024年年度报告",
        "url": "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2025-03-28/601872_20250328_TRN3.pdf",
    },
    "2025": {
        "path": REPORT_DIR / "招商局能源运输股份有限公司_601872_2025_年度报告.pdf",
        "label": "2025年年度报告",
        "url": "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-03-27/601872_20260327_M9NZ.pdf",
    },
    "2026H1": {
        "path": REPORT_DIR / "招商局能源运输股份有限公司_601872_2026_半年度报告.pdf",
        "label": "2026年半年度报告",
        "url": "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-08-31/601872_20260831_PZBX.pdf",
    },
}

YEARS = ["2023", "2024", "2025", "2026H1"]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", "").replace("\n", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def canonical(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("(", "（").replace(")", "）")


def parse_value(value: object):
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    if text in {"-", "—", "－"}:
        return "-"
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(Decimal(text))
    except InvalidOperation:
        return clean_text(value)


def source_text(report_key: str, page: int) -> str:
    report = REPORTS[report_key]
    return (
        f"{report['label']}；{report['path'].name}；PDF第{page}页；"
        f"{report['url']}"
    )


class Dataset:
    def __init__(self):
        self.rows: OrderedDict[tuple, dict] = OrderedDict()

    def add(
        self,
        *,
        occurrence: int,
        table: str,
        level: str,
        item: str,
        year: str,
        value,
        report_key: str,
        page: int,
        unit: str = "元",
    ) -> None:
        base_key = (canonical(table), canonical(level), canonical(item))
        key = (*base_key, occurrence)
        if key not in self.rows:
            self.rows[key] = {
                "原表名称": table,
                "所在部分/层级": level,
                "项目": item,
                "periods": {y: {"value": None, "unit": "", "source": "", "present": False} for y in YEARS},
            }
        period = self.rows[key]["periods"][year]
        period.update(
            {
                "value": value,
                "unit": unit,
                "source": source_text(report_key, page),
                "present": True,
            }
        )


def table_at(pdf, page: int, table_index: int = 0):
    tables = pdf.pages[page - 1].extract_tables()
    return tables[table_index]


def is_header(row: list) -> bool:
    return clean_text(row[0]) == "项目"


def process_standard(
    ds: Dataset,
    *,
    pdf,
    table_name: str,
    base_level: str,
    specs: list[tuple[int, int]],
    report_key: str,
    year_columns: dict[str, int],
) -> None:
    section = ""
    counters: dict[str, int] = defaultdict(int)
    raw_rows: list[tuple[int, list]] = []
    for page, table_index in specs:
        for row in table_at(pdf, page, table_index):
            if not row or is_header(row):
                continue
            raw_rows.append((page, row))

    # Repair a page-break artifact seen in the parent-company income statement:
    # “3.其他权益工具投资公允价值” / “变动” is one original line item.
    repaired_rows: list[tuple[int, list]] = []
    for page, row in raw_rows:
        item = clean_text(row[0])
        if (
            item == "变动"
            and repaired_rows
            and clean_text(repaired_rows[-1][1][0]).endswith("公允价值")
            and all(parse_value(v) is None for v in row[1:])
        ):
            repaired_rows[-1][1][0] = clean_text(repaired_rows[-1][1][0]) + item
            continue
        repaired_rows.append((page, row))

    for page, row in repaired_rows:
            item = clean_text(row[0])
            if not item:
                continue
            vals = [parse_value(row[idx]) if idx < len(row) else None for idx in year_columns.values()]
            structural = all(v is None for v in vals) and (
                item.endswith("：")
                or re.match(r"^（[一二三四五六七八九十]+）按", item)
            )
            level = base_level if not section else f"{base_level}｜{section.rstrip('：')}"
            if structural:
                level = base_level
            # Occurrence counters are shared across the years extracted from the same row.
            base = (canonical(table_name), canonical(level), canonical(item))
            occurrence = counters[str(base)]
            counters[str(base)] += 1
            for year, col in year_columns.items():
                value = parse_value(row[col]) if col < len(row) else None
                ds.add(
                    occurrence=occurrence,
                    table=table_name,
                    level=level,
                    item=item,
                    year=year,
                    value=value,
                    report_key=report_key,
                    page=page,
                )
            if structural:
                section = item.rstrip("：")


CONSOLIDATED_COMPONENTS = [
    "归属于母公司所有者权益｜实收资本（或股本）",
    "归属于母公司所有者权益｜其他权益工具｜优先股",
    "归属于母公司所有者权益｜其他权益工具｜永续债",
    "归属于母公司所有者权益｜其他权益工具｜其他",
    "归属于母公司所有者权益｜资本公积",
    "归属于母公司所有者权益｜减：库存股",
    "归属于母公司所有者权益｜其他综合收益",
    "归属于母公司所有者权益｜专项储备",
    "归属于母公司所有者权益｜盈余公积",
    "归属于母公司所有者权益｜一般风险准备",
    "归属于母公司所有者权益｜未分配利润",
    "归属于母公司所有者权益｜其他",
    "归属于母公司所有者权益｜小计",
    "少数股东权益",
    "所有者权益合计",
]

PARENT_COMPONENTS = [
    "实收资本（或股本）",
    "其他权益工具｜优先股",
    "其他权益工具｜永续债",
    "其他权益工具｜其他",
    "资本公积",
    "减：库存股",
    "其他综合收益",
    "专项储备",
    "盈余公积",
    "未分配利润",
    "所有者权益合计",
]


def process_equity(
    ds: Dataset,
    *,
    pdf,
    table_name: str,
    report_key: str,
    year: str,
    specs: list[tuple[int, int, int]],
    components: list[str],
) -> None:
    counters: dict[tuple, int] = defaultdict(int)
    for page, table_index, header_rows in specs:
        table = table_at(pdf, page, table_index)
        for row in table[header_rows:]:
            if not row:
                continue
            item = clean_text(row[0])
            if not item:
                continue
            row_values = [parse_value(v) for v in row[1 : len(components) + 1]]
            for idx, component in enumerate(components):
                value = row_values[idx] if idx < len(row_values) else None
                occurrence = counters[(canonical(item), canonical(component))]
                counters[(canonical(item), canonical(component))] += 1
                ds.add(
                    occurrence=occurrence,
                    table=table_name,
                    level=component,
                    item=item,
                    year=year,
                    value=value,
                    report_key=report_key,
                    page=page,
                )


def load_pdfs():
    return {key: pdfplumber.open(meta["path"]) for key, meta in REPORTS.items()}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = load_pdfs()
    ds = Dataset()

    standard_specs = {
        "合并资产负债表": {
            "level": "合并",
            "2025": [(98, 0), (99, 0), (100, 0)],
            "2024": [(108, 0), (109, 0), (110, 0)],
            "2026H1": [(58, 0), (59, 0), (60, 0)],
        },
        "母公司资产负债表": {
            "level": "母公司",
            "2025": [(101, 0), (102, 0)],
            "2024": [(111, 0), (112, 0)],
            "2026H1": [(60, 1), (61, 0), (62, 0)],
        },
        "合并利润表": {
            "level": "合并",
            "2025": [(103, 0), (104, 0)],
            "2024": [(113, 0), (114, 0)],
            "2026H1": [(62, 1), (63, 0), (64, 0)],
        },
        "母公司利润表": {
            "level": "母公司",
            "2025": [(105, 0), (106, 0)],
            "2024": [(115, 0), (116, 0)],
            "2026H1": [(64, 1), (65, 0)],
        },
        "合并现金流量表": {
            "level": "合并",
            "2025": [(107, 0), (108, 0)],
            "2024": [(117, 0), (118, 0)],
            "2026H1": [(66, 0), (67, 0)],
        },
        "母公司现金流量表": {
            "level": "母公司",
            "2025": [(109, 0), (110, 0)],
            "2024": [(119, 0)],
            "2026H1": [(68, 0), (69, 0)],
        },
    }

    for table_name, spec in standard_specs.items():
        process_standard(
            ds,
            pdf=pdfs["2025"],
            table_name=table_name,
            base_level=spec["level"],
            specs=spec["2025"],
            report_key="2025",
            year_columns={"2025": 2, "2024": 3},
        )
        process_standard(
            ds,
            pdf=pdfs["2024"],
            table_name=table_name,
            base_level=spec["level"],
            specs=spec["2024"],
            report_key="2024",
            year_columns={"2023": 3},
        )
        process_standard(
            ds,
            pdf=pdfs["2026H1"],
            table_name=table_name,
            base_level=spec["level"],
            specs=spec["2026H1"],
            report_key="2026H1",
            year_columns={"2026H1": 2},
        )

    process_equity(
        ds,
        pdf=pdfs["2025"],
        table_name="合并所有者权益变动表",
        report_key="2025",
        year="2025",
        specs=[(111, 0, 4)],
        components=CONSOLIDATED_COMPONENTS,
    )
    process_equity(
        ds,
        pdf=pdfs["2025"],
        table_name="合并所有者权益变动表",
        report_key="2025",
        year="2024",
        specs=[(112, 0, 4)],
        components=CONSOLIDATED_COMPONENTS,
    )
    process_equity(
        ds,
        pdf=pdfs["2024"],
        table_name="合并所有者权益变动表",
        report_key="2024",
        year="2023",
        specs=[(122, 0, 3), (123, 0, 0)],
        components=CONSOLIDATED_COMPONENTS,
    )
    process_equity(
        ds,
        pdf=pdfs["2026H1"],
        table_name="合并所有者权益变动表",
        report_key="2026H1",
        year="2026H1",
        specs=[(70, 0, 4), (71, 0, 0)],
        components=CONSOLIDATED_COMPONENTS,
    )

    process_equity(
        ds,
        pdf=pdfs["2025"],
        table_name="母公司所有者权益变动表",
        report_key="2025",
        year="2025",
        specs=[(113, 0, 3)],
        components=PARENT_COMPONENTS,
    )
    process_equity(
        ds,
        pdf=pdfs["2025"],
        table_name="母公司所有者权益变动表",
        report_key="2025",
        year="2024",
        specs=[(114, 0, 3)],
        components=PARENT_COMPONENTS,
    )
    process_equity(
        ds,
        pdf=pdfs["2024"],
        table_name="母公司所有者权益变动表",
        report_key="2024",
        year="2023",
        specs=[(124, 1, 3), (125, 0, 0)],
        components=PARENT_COMPONENTS,
    )
    process_equity(
        ds,
        pdf=pdfs["2026H1"],
        table_name="母公司所有者权益变动表",
        report_key="2026H1",
        year="2026H1",
        specs=[(73, 0, 3)],
        components=PARENT_COMPONENTS,
    )

    rows = []
    for row in ds.rows.values():
        # Equity matrices contain many structurally blank intersections. Retain a
        # cell row only when at least one requested period has a value.
        if "所有者权益变动表" in row["原表名称"]:
            if not any(p["value"] is not None for p in row["periods"].values()):
                continue
        rows.append(row)

    output_path = OUT_DIR / "cmes_statement_data.json"
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        for year, period in row["periods"].items():
            if period["value"] is not None:
                counts[row["原表名称"]][year] += 1
    print(f"rows={len(rows)}")
    for table, by_year in counts.items():
        print(table, dict(by_year))
    print(output_path)


if __name__ == "__main__":
    main()
