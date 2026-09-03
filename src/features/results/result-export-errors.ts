type ResultExportScope = 'overview' | 'details' | 'reconciliation';

export function resultExportErrorMessage(
  error: unknown,
  dateFrom?: string,
  dateTo?: string,
  _scopes: ResultExportScope[] = [],
  search = '',
) {
  const code = String((error as { code?: string })?.code ?? 'internal_error');
  const range = dateFrom && dateTo ? ` từ ${formatDate(dateFrom)} đến ${formatDate(dateTo)}` : '';

  if (code === 'result_export_busy_bulk') {
    return 'Đang tải kết quả tất cả ở màn Hóa đơn. Hãy chờ tác vụ đó hoàn tất.';
  }
  if (code === 'result_export_busy_results') {
    return 'Đang tạo Excel trong tab Kết quả. Hãy chờ tác vụ đó hoàn tất.';
  }
  if (code === 'result_export_runtime_unavailable') {
    return 'Bộ tạo Excel cục bộ chưa sẵn sàng.';
  }
  if (code === 'artifact_task_active') {
    return 'Đang có một tiến trình tải hoặc xuất file khác. Vui lòng chờ tiến trình hiện tại hoàn tất.';
  }
  if (code === 'result_export_no_overview_data') {
    return `Không có dữ liệu Tổng quan${range} để tạo Excel.`;
  }
  if (code === 'result_export_no_detail_data') {
    return `Không có dữ liệu Chi tiết${range} để tạo Excel. Hãy đồng bộ phạm vi Chi tiết trước.`;
  }
  if (code === 'result_export_empty') {
    return search.trim()
      ? 'Không tồn tại hóa đơn phù hợp với lựa chọn hiện tại.'
      : 'Không tồn tại hóa đơn trong thời gian này.';
  }
  if (code === 'result_reconciliation_coverage_missing') {
    return 'Chưa đủ dữ liệu Tổng quan và Chi tiết để đối chiếu trong khoảng thời gian này.';
  }
  if (code === 'result_job_not_found') {
    return 'Chưa có dữ liệu đồng bộ của tài khoản này để xuất Excel.';
  }
  if (code === 'result_export_template_missing') {
    return 'Thiếu mẫu Excel gốc của crawler. Hãy kiểm tra/cài lại bộ runtime.';
  }
  if (code === 'artifact_write_denied') {
    return 'Không có quyền ghi vào thư mục lưu trữ đã chọn. Hãy chọn thư mục khác.';
  }
  if (code === 'artifact_write_failed' || code === 'invalid_artifact_directory') {
    return 'Không thể ghi file vào thư mục lưu trữ. Hãy kiểm tra đường dẫn, dung lượng trống và quyền ghi.';
  }
  if (code === 'invalid_result_export_range') {
    return 'Khoảng ngày xuất Excel không hợp lệ.';
  }
  if (code === 'result_export_failed') {
    return 'Không thể dựng file Excel từ dữ liệu đã lưu. Xem Nhật ký để biết lỗi kỹ thuật cụ thể.';
  }
  return `Không thể xuất Excel (${code}). Xem Nhật ký để biết chi tiết.`;
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}
