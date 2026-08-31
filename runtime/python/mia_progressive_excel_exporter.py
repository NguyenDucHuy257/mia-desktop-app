"""Progress-enabled customer detail Excel renderer."""

from __future__ import annotations

from mia_detail_excel_exporter import CustomerDetailExcelExporter


class ProgressiveInvoiceDetailExcelExporter(CustomerDetailExcelExporter):
    def __init__(self, template_path, row_builder=None, *, progress) -> None:
        super().__init__(template_path, row_builder=row_builder, progress=progress)


__all__ = ["ProgressiveInvoiceDetailExcelExporter"]
