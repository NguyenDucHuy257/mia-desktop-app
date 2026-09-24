export const RECOMMENDED_PROXY = Object.freeze({
  name: 'Proxy IPv4 Việt Nam – loại riêng',
  note: 'Ưu tiên Proxy có tài khoản/mật khẩu hoặc giới hạn theo máy, hỗ trợ HTTP/HTTPS và không dùng chung với người khác.',
  duration: 'Nên mua thử 1 tháng trước, sau đó tăng số lượng theo nhu cầu thực tế.',
});

export interface ProxyGuideStepData {
  number: number;
  title: string;
  description: string;
  image?: string;
  recommended?: boolean;
}

export const proxyGuideSteps: ProxyGuideStepData[] = [
  {
    number: 1,
    title: 'Đăng ký hoặc đăng nhập',
    description: 'Nhấn nút ở cuối hướng dẫn để mở website bằng trình duyệt mặc định. Nếu chưa có tài khoản, nhập tên đăng nhập, Gmail và mật khẩu để đăng ký; nếu đã có tài khoản thì đăng nhập.',
    image: step1Image,
  },
  {
    number: 2,
    title: 'Chọn đúng loại Proxy',
    description: 'Tại Trang chủ, tìm nhóm Proxy IPv4 Việt Nam cố định – xài riêng và chọn phiên bản phù hợp theo khuyến nghị của MIA.',
    image: step2Image,
    recommended: true,
  },
  {
    number: 3,
    title: 'Nhấn Mua ngay',
    description: 'Sau khi chọn đúng sản phẩm, nhấn nút “Mua ngay” để mở phần xác nhận đơn hàng.',
    image: step3Image,
  },
  {
    number: 4,
    title: 'Chọn số lượng và xác nhận',
    description: 'Nhập số lượng Proxy cần mua, kiểm tra lại tên sản phẩm, đơn giá và thành tiền rồi nhấn “Mua ngay”.',
    image: step4Image,
  },
  {
    number: 5,
    title: 'Nạp tiền nếu số dư chưa đủ',
    description: 'Mở mục “Nạp Tiền”, nhập số tiền cần nạp và tạo mã QR thanh toán. Nếu số dư đã đủ cho đơn mua, bạn có thể bỏ qua bước này.',
    image: step5Image,
  },
  {
    number: 6,
    title: 'Tải file TXT và Import vào MIA',
    description: 'Trong Lịch sử mua hàng, nhấn “Tải về” để lấy file TXT chứa danh sách Proxy. Sau đó trở lại MIA và dùng nút “Import file Proxy”. Không gửi file này cho người khác vì file có thể chứa thông tin đăng nhập Proxy.',
    image: step6Image,
  },
];
import step1Image from '../../assets/proxy-guide/step-1.png';
import step2Image from '../../assets/proxy-guide/step-2.png';
import step3Image from '../../assets/proxy-guide/step-3.png';
import step4Image from '../../assets/proxy-guide/step-4.png';
import step5Image from '../../assets/proxy-guide/step-5.png';
import step6Image from '../../assets/proxy-guide/step-6.png';
