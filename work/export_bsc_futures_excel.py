from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile


WORKSHEET_NAME = "BSC Futures FDV"
HEADER_ROW = 7
DATA_START_ROW = HEADER_ROW + 1

TITLE_FILL = "0F4C5C"
SUMMARY_LABEL_FILL = "E6F1F5"
SURFACE_FILL = "F8FBFC"
HEADER_FILL = "1D7085"
HELD_FILL = "CFE8CF"


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def text_cell(reference: str, value: Any, style: int) -> str:
    escaped = escape(str(value), {'"': "&quot;"})
    return (
        f'<c r="{reference}" s="{style}" t="inlineStr">'
        f'<is><t xml:space="preserve">{escaped}</t></is></c>'
    )


def number_cell(reference: str, value: Any, style: int) -> str:
    return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'


def blank_cell(reference: str, style: int) -> str:
    return f'<c r="{reference}" s="{style}"/>'


def xml_document(body: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body


def write_styles() -> str:
    return xml_document(
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="5">'
        '<numFmt numFmtId="164" formatCode="0.00&quot; M&quot;"/>'
        '<numFmt numFmtId="165" formatCode="0.00000000"/>'
        '<numFmt numFmtId="166" formatCode="0.0000"/>'
        '<numFmt numFmtId="167" formatCode="$#,##0.00"/>'
        '<numFmt numFmtId="168" formatCode="#,##0"/>'
        '</numFmts>'
        '<fonts count="4">'
        '<font><sz val="11"/><color rgb="FF000000"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FF12343B"/><name val="Calibri"/></font>'
        '<font><i/><sz val="11"/><color rgb="FF344054"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="7">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{TITLE_FILL}"/><bgColor indexed="64"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{SUMMARY_LABEL_FILL}"/><bgColor indexed="64"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{SURFACE_FILL}"/><bgColor indexed="64"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{HEADER_FILL}"/><bgColor indexed="64"/></patternFill></fill>'
        f'<fill><patternFill patternType="solid"><fgColor rgb="FF{HELD_FILL}"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFE2E8F0"/></left><right style="thin"><color rgb="FFE2E8F0"/></right><top style="thin"><color rgb="FFE2E8F0"/></top><bottom style="thin"><color rgb="FFE2E8F0"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="27">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="3" fillId="4" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="5" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="168" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="167" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="165" fontId="0" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="166" fontId="0" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="168" fontId="0" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="167" fontId="0" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="6" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="165" fontId="0" fillId="6" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="166" fontId="0" fillId="6" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="168" fontId="0" fillId="6" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="167" fontId="0" fillId="6" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def data_styles(row: dict[str, Any], index: int) -> tuple[int, int, int, int, int, int, int]:
    if row["has_holding"]:
        return 20, 21, 22, 23, 24, 25, 26
    if index % 2 == 0:
        return 13, 14, 15, 16, 17, 18, 19
    return 6, 7, 8, 9, 10, 11, 12


def write_row(row_number: int, cells: list[str]) -> str:
    return f'<row r="{row_number}">{"".join(cells)}</row>'


def write_sheet(payload: dict[str, Any]) -> str:
    rows = payload.get("rows", [])
    last_row = max(DATA_START_ROW + len(rows) - 1, HEADER_ROW)
    sheet_rows = [
        write_row(1, [text_cell("A1", "BSC Tokens with Binance Futures Ranked by FDV", 1)]),
        write_row(2, [text_cell("A2", "Generated At", 2), text_cell("B2", payload.get("generated_at", ""), 3), text_cell("D2", "Filters applied: chainId=56, stockState=false, offline=false, and not (listingCex=true and cexOffDisplay=true). Rows with holdings are highlighted in green.", 4)]),
        write_row(3, [text_cell("A3", "Total Count", 2), number_cell("B3", payload.get("total_count", len(rows)), 3)]),
        write_row(4, [text_cell("A4", "Wallet", 2), text_cell("B4", payload.get("wallet_address", ""), 3)]),
        write_row(5, [text_cell("A5", "Held Tokens", 2), number_cell("B5", payload.get("held_token_count", 0), 3)]),
    ]

    headers = [
        "Symbol",
        "Futures",
        "FDV (M USD)",
        "Token Price",
        "Market Price",
        "Gap (%)",
        "Holder Count",
        "Holding Amount",
        "Holding Value (USD)",
        "Match Method",
        "Contract Address",
    ]
    sheet_rows.append(
        write_row(
            HEADER_ROW,
            [text_cell(f"{column_name(index)}{HEADER_ROW}", value, 5) for index, value in enumerate(headers, 1)],
        )
    )

    for index, row in enumerate(rows):
        row_number = DATA_START_ROW + index
        (
            text_style,
            center_style,
            fdv_style,
            price_style,
            gap_style,
            holder_style,
            currency_style,
        ) = data_styles(row, index)
        cells = [
            text_cell(f"A{row_number}", row["symbol"], center_style),
            text_cell(f"B{row_number}", row["futures_symbol"], center_style),
            number_cell(f"C{row_number}", row["fdv_m"], fdv_style),
            number_cell(f"D{row_number}", row["token_price"], price_style),
            blank_cell(f"E{row_number}", price_style)
            if row["market_price"] is None
            else number_cell(f"E{row_number}", row["market_price"], price_style),
            blank_cell(f"F{row_number}", gap_style)
            if row["price_gap_pct"] is None
            else number_cell(f"F{row_number}", row["price_gap_pct"], gap_style),
            blank_cell(f"G{row_number}", holder_style)
            if row["holder_count"] is None
            else number_cell(f"G{row_number}", row["holder_count"], holder_style),
            text_cell(f"H{row_number}", row["holding_amount"], text_style),
            number_cell(f"I{row_number}", row["holding_value_usd"], currency_style),
            text_cell(f"J{row_number}", row["match_method"], text_style),
            text_cell(f"K{row_number}", row["contract_address"], text_style),
        ]
        sheet_rows.append(write_row(row_number, cells))

    columns = (
        '<cols>'
        '<col min="1" max="1" width="12.86" customWidth="1"/>'
        '<col min="2" max="2" width="16" customWidth="1"/>'
        '<col min="3" max="3" width="17.14" customWidth="1"/>'
        '<col min="4" max="5" width="15.71" customWidth="1"/>'
        '<col min="6" max="6" width="12" customWidth="1"/>'
        '<col min="7" max="7" width="14" customWidth="1"/>'
        '<col min="8" max="8" width="20" customWidth="1"/>'
        '<col min="9" max="9" width="20" customWidth="1"/>'
        '<col min="10" max="10" width="34.29" customWidth="1"/>'
        '<col min="11" max="11" width="48.57" customWidth="1"/>'
        '</cols>'
    )
    return xml_document(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:K{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0">'
        '<pane ySplit="7" topLeftCell="A8" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A8" sqref="A8"/>'
        '</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'{columns}'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '<mergeCells count="2"><mergeCell ref="A1:K1"/><mergeCell ref="D2:K5"/></mergeCells>'
        f'<autoFilter ref="A7:K{last_row}"/>'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        '</worksheet>'
    )


def export_workbook(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with ZipFile(output_path, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            xml_document(
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                '</Types>'
            ),
        )
        workbook.writestr(
            "_rels/.rels",
            xml_document(
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                '</Relationships>'
            ),
        )
        workbook.writestr(
            "xl/workbook.xml",
            xml_document(
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="BSC Futures FDV" sheetId="1" r:id="rId1"/></sheets>'
                '</workbook>'
            ),
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            xml_document(
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>'
            ),
        )
        workbook.writestr("xl/styles.xml", write_styles())
        workbook.writestr("xl/worksheets/sheet1.xml", write_sheet(payload))
        workbook.writestr(
            "docProps/core.xml",
            xml_document(
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:creator>BSC Futures FDV Exporter</dc:creator>'
                '<cp:lastModifiedBy>BSC Futures FDV Exporter</cp:lastModifiedBy>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created_at}</dcterms:modified>'
                '</cp:coreProperties>'
            ),
        )
        workbook.writestr(
            "docProps/app.xml",
            xml_document(
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
                '<Application>Python Standard Library</Application>'
                '</Properties>'
            ),
        )
