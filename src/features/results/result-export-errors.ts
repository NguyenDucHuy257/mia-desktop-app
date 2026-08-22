type ResultExportScope = 'overview' | 'details';

export function resultExportErrorMessage(
  error: unknown,
  dateFrom?: string,
  dateTo?: string,
  scopes: ResultExportScope[] = [],
) {
  const code = String((error as { code?: string })?.code ?? 'internal_error');
  const range = dateFrom && dateTo ? ` từ ${formatDate(dateFrom)} đến ${formatDate(dateTo)}` : '';

  if (code === 'result_export_no_overview_data') {
    return `Không có dữ liệu Tổng quan${range} để tạo Excel.`;
  }
  if (code === 'result_export_no_detail_data') {
    return `Không có dữ liệu Chi tiết${range} để tạo Excel. Hãy đồng bộ phạm vi Chi tiết trước.`;
  }
  if (code === 'result_export_empty') {
    if (scopes.length === 1 && scopes[0] === 'overview') {
      return `Không có dữ liệu Tổng quan${range} để tạo Excel.`;
    }
    if (scopes.length === 1 && scopes[0] === 'details') {
      return `Không có dữ liệu Chi tiết${range} để tạo Excel. Hãy đồng bộ phạm vi Chi tiết trước.`;
    }
    return `Không có dữ liệu hóa đơn${range} phù hợp với lựa chọn hiện tại.`;
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
