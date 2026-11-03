# Bản đồng bộ ngày 03/11/2026

Giữ nguyên ba tài liệu gốc. Các bản ở thư mục này là bản đề xuất đã chỉnh để nhóm review.

## File và cách dùng

- Dac_ta_yeu_cau_he_thong_SRS.docx: giữ nội dung cũ, sửa tham chiếu và bổ sung mục 11 làm rõ/thay thế các tiêu chí xung đột. Giữ 63 FR/16 NFR. Đọc mục 11 cùng các mục gốc.
- Phan_cong_va_theo_doi_cong_viec_nhom_14.xlsx: giữ STT/người phụ trách/điểm/ngày 65 task gốc; thêm 4 cột truy vết, giai đoạn, phụ thuộc và tiêu chí; thêm task 66–71. Ngày/sprint/điểm bổ sung là dự kiến; ưu tiên mốc phụ thuộc thay vì thứ tự dòng. Không coi mọi task là một ngày công.
- Khoi_tao_co_so_du_lieu_MySQL.sql: cài mới vào database social_media_mgmt_aligned, không tác động tên database gốc. 34 bảng. Không phải migration nâng cấp DB có dữ liệu; không tự động chạy trong lần chỉnh này.

## Thay đổi chính

1. MVP tắt bắt buộc duyệt; editor chỉ soạn; post.publish mới được đăng/lên lịch. M6 mở giai đoạn 2.
2. Lõi audit, lịch sử trạng thái, cấu hình phải làm sớm; UI có thể làm sau. Upload cơ bản trước đính kèm media.
3. Bổ sung xác minh email, khóa tạm, phiên đăng nhập/thu hồi, bảo vệ Admin cuối/owner, reconnect cập nhật token, ngắt kênh hủy lịch, retry và export nền.
4. Bổ sung auth_sessions/auth_action_tokens, is_system_admin, failed_login_attempts/locked_until.
5. Unique generated column chặn nhiều lịch active, nhiều approval pending, nhiều job success. FK job/schedule bảo đảm cùng variant; giữ lịch đã có job bằng RESTRICT thay vì xóa.
6. Lease cho worker/email và trường retry email; in_app_visible tách email-only khỏi danh sách/bộ đếm trong ứng dụng.
7. Không nhận định unique DB bảo đảm exactly-once ở mạng xã hội; xử lý timeout bằng idempotency/đối soát, không retry mù.

## Quy tắc bắt buộc ở service

- Transaction và khóa theo workspace/post/variant khi đổi owner, role, duyệt, tạo/hủy lịch và claim job.
- Admin active cuối cùng không được hạ/xóa/tạm ngưng; owner phải chuyển trước. Manager không cấp quyền Admin.
- Kiểm tra mọi ID cùng workspace, folder không vòng lặp, reply cùng post, conversation variant cùng account, assignee là member active; loại trừ deleted_at.
- Cấu hình UTC trên từng connection; timezone hiển thị lấy duy nhất workspaces.timezone.
- Mặc định workspace mới require_approval=false, allow_self_approval=false; ghi cùng transaction tạo workspace.
- Pending approval không sửa; sửa bài approved mất duyệt; kiểm tra lại duyệt/kênh khi worker chuẩn bị gửi.
- Email claim và retry có giới hạn; không tính email-only vào unread. Không log token, password hoặc response nhạy cảm.

## Kiểm tra đã làm và giới hạn

Đã kiểm tra ZIP/XML, số bảng, số task và cột truy vết bằng script. Chưa mở render Word/Excel và chưa chạy schema trên MySQL thật. Không coi kiểm tra cấu trúc là kiểm thử SQL runtime. Trước dùng chính thức: import DB trống MySQL 8, thử unique/FK, mở Word/Excel kiểm tra trình bày, xác minh quyền API nền tảng và chốt ước lượng sprint với nhóm.

Các giá trị 15 phút khóa, 30 phút idle, 7 ngày phiên, 24 giờ xác minh và retry 60/120/240 giây là mặc định thiết kế đề xuất; thay đổi phải đồng bộ cấu hình, SRS và kiểm thử.

## Doi ten file

Ten cu csdl_dong_bo.sql duoc nhac trong SRS nay tuong ung voi Khoi_tao_co_so_du_lieu_MySQL.sql. Chi doi ten file Word/Excel/SQL/JSON, giu nguyen noi dung ben trong. Script revise_documents.py khong con trong thu muc du an tai thoi diem doi ten.
