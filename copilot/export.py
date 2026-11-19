"""Excel output. Status counts and the tender-form column are live formulas, so reviewer
overrides (column M) flow through to the Summary sheet."""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .models import STATUSES, MatchResult, Requirement

FONT = "Arial"
FILLS = {"Compliant": "C6EFCE", "Partial": "FFEB9C", "Non-compliant": "FFC7CE",
         "Not found": "D9D9D9", "Needs human review": "FCE4D6"}
HEADERS = ["Req ID", "Tender ref", "Tender requirement", "Required value", "Priority", "AI status",
           "Offered value", "Evidence (verbatim)", "Source (file, PDF page)", "Reasoning", "Confidence",
           "Verifier note", "Reviewer override", "Effective status", "Tender form entry", "Reviewer notes"]
WIDTHS = [8, 18, 48, 20, 12, 18, 22, 46, 26, 44, 11, 36, 18, 18, 14, 30]
thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def sheet_title(product: str, used: set[str]) -> str:
    base = ("Matrix - " + re.sub(r"[\[\]:*?/\\]", "", product))[:31]
    name, n = base, 2
    while name in used:
        name = base[:28] + f"~{n}"
        n += 1
    used.add(name)
    return name


def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor="1F3864")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER


def write_workbook(path: str, reqs: list[Requirement], results: dict[str, list[MatchResult]], meta: dict) -> None:
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    used = {"Summary"}
    sheets: list[tuple[str, str]] = []

    req_by_id = {r.req_id: r for r in reqs}
    for product, rows in results.items():
        title = sheet_title(product, used)
        sheets.append((product, title))
        ws = wb.create_sheet(title)
        ws.append(HEADERS)
        _style_header(ws, 1, len(HEADERS))
        ws.row_dimensions[1].height = 32
        for i, m in enumerate(rows, start=2):
            r = req_by_id[m.req_id]
            src = f"{m.source_file}, p.{m.page}" if m.source_file else ""
            ws.append([r.req_id, r.clause_ref, r.clause_text, r.required_value, r.priority, m.status,
                       m.offered_value, m.evidence_quote, src, m.reasoning, round(m.confidence, 2),
                       "; ".join(x for x in (m.verifier_note, r.extraction_note) if x), None,
                       f'=IF(M{i}="",F{i},M{i})',
                       f'=IF(N{i}="Compliant","YES",IF(N{i}="Non-compliant","NO","REVIEW"))', None])
            for c in range(1, len(HEADERS) + 1):
                cell = ws.cell(row=i, column=c)
                cell.font = Font(name=FONT, size=10)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.border = BORDER
            ws.cell(row=i, column=11).number_format = "0.00"
        last = max(len(rows) + 1, 2)
        for idx, w in enumerate(WIDTHS, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w
        ws.freeze_panes = "D2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{last}"
        dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"M2:M{last}")
        for col in ("F", "N"):
            for status, color in FILLS.items():
                ws.conditional_formatting.add(
                    f"{col}2:{col}{last}",
                    FormulaRule(formula=[f'${col}2="{status}"'], fill=PatternFill("solid", bgColor=color, fgColor=color)))

    # ---- Summary ------------------------------------------------------
    s = summary
    s["A1"] = "Pre-Sales Copilot - compliance summary"
    s["A1"].font = Font(name=FONT, bold=True, size=14)
    s["A2"] = f"Tender: {meta.get('tender', '')}   |   Model: {meta.get('model', '')}   |   Run: {meta.get('run_at', '')}"
    s["A3"] = ("AI-generated draft. Every row needs engineer review before anything is sent to a customer. "
               "Use the 'Reviewer override' column on each matrix sheet; counts below update automatically.")
    for ref in ("A2", "A3"):
        s[ref].font = Font(name=FONT, size=10, italic=True)
    s["A5"] = "Status (after reviewer overrides)"
    for j, (_, title) in enumerate(sheets, start=2):
        s.cell(row=5, column=j, value=title.replace("Matrix - ", ""))
    _style_header(s, 5, 1 + len(sheets))
    for i, status in enumerate(STATUSES, start=6):
        s.cell(row=i, column=1, value=status).font = Font(name=FONT, size=10)
        s.cell(row=i, column=1).fill = PatternFill("solid", fgColor=FILLS[status])
        for j, (_, title) in enumerate(sheets, start=2):
            q = title.replace("'", "''")
            c = s.cell(row=i, column=j, value=f"=COUNTIF('{q}'!$N$2:$N$500,$A{i})")
            c.font = Font(name=FONT, size=10)
    tot = 6 + len(STATUSES)
    s.cell(row=tot, column=1, value="Total requirements").font = Font(name=FONT, bold=True, size=10)
    for j in range(2, 2 + len(sheets)):
        col = get_column_letter(j)
        c = s.cell(row=tot, column=j, value=f"=SUM({col}6:{col}{tot - 1})")
        c.font = Font(name=FONT, bold=True, size=10)
    s.column_dimensions["A"].width = 34
    for j in range(2, 2 + len(sheets)):
        s.column_dimensions[get_column_letter(j)].width = 26
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
