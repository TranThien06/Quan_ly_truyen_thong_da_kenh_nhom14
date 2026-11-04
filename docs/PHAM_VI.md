# Phạm vi đã triển khai và điều kiện nghiệm thu

Backend Python + FastAPI có router và các hàm nghiệp vụ cho 10 module SRS. Đây là mã backend; không gồm giao diện web. Tài liệu này phân biệt chức năng nội bộ đã kiểm thử với tích hợp cần ứng dụng thật.

| Phần                  | Hiện trạng                                                                                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| M1 xác thực/workspace | Email một lần, phiên DB, khóa tạm, role/permission, lời mời, owner/Admin cuối, hồ sơ; có integration test                                |
| M2 kênh               | OAuth state một lần, chọn tài khoản, token mã hóa, reconnect/disconnect; mock đã test, live chưa có credentials để xác nhận              |
| M3/M6 nội dung/duyệt  | Bài và biến thể, media, preview dữ liệu, nhân bản, xóa mềm, duyệt lại, comment/history; có integration test                              |
| M4 worker             | Durable jobs, khóa post/variant, lịch đến hạn, hủy, retry backoff, xử lý worker gián đoạn; không tự gửi lại tình huống mơ hồ             |
| M5 media              | Một tệp/request, thumbnail bằng Pillow, MP4 qua FFmpeg, folder/tag, signed URL; ảnh đã test, video cần FFmpeg và kiểm thử thêm |
| M7 báo cáo            | Chỉ số ngày và snapshot checkpoint 1h/24h/7d, CSV/XLSX/PDF nền; thiếu chỉ số giữ NULL                                                    |
| M8 inbox              | Lọc, gán người, reply có request_id, sync dedupe; Meta Page DM/comment trong adapter; TikTok/Instagram inbox chưa bật live               |
| M9 thông báo          | Người nhận theo workspace, đã đọc, settings, email-only, encrypted outbox và retry                                                       |
| M10 quản trị          | Audit log chỉ đọc, cấu hình workspace/system, quyền hệ thống riêng; API không tự cấp system admin                                        |

## Adapter thật cần chạy nghiệm thu với tài khoản của nhóm

- Facebook: OAuth Page, text, nhiều ảnh và một video; đọc số follower và một số chỉ số bài, Page inbox/comments. Chưa triển khai Story/Reel Facebook, giới hạn media chi tiết theo từng định dạng chưa được xác nhận bằng ứng dụng thật.
- Instagram: chọn luồng Facebook Login như SRS, yêu cầu Page liên kết đã kết nối. Live hiện hỗ trợ một ảnh hoặc một Reel, chưa carousel/Story. Lưu container ID để poll tiếp, không coi trạng thái đang xử lý là đăng thành công.
- TikTok: OAuth, một video Direct Post từ URL, lưu publish_id để poll; chưa tự quyết định privacy, người dùng phải cung cấp privacy_level. Domain media cần được TikTok xác minh; signed URL riêng chưa đủ nếu domain chưa duyệt. Chưa có màn hình creator_info/consent theo quy trình UX của TikTok; frontend và app review phải bổ sung trước khi dùng công khai.
- Chỉ số không được adapter hoặc nền tảng cung cấp giữ NULL; không tạo số liệu giả trong live. Không phải toàn bộ chỉ số impressions/reach/clicks đều đã tích hợp.
- Meta token không giả định có thể refresh vô thời hạn; adapter đánh dấu cần reconnect khi không có đường làm mới an toàn. TikTok có refresh-token flow.
- OAuth callback hiện trả JSON cho frontend lấy connection_token/chọn tài khoản; cần frontend tích hợp popup hoặc redirect theo luồng đó. Không trả OAuth access token ra client.

Không thể nghiệm thu đăng thật bằng mock. Để nghiệm thu live, cần App ID/secret, scopes đã được duyệt, tài khoản test, API version, URL HTTPS và domain media. Xem nguồn chính thức ở README trước khi chốt giới hạn caption/media, token và quyền cho từng định dạng.

## Đối soát thay vì đăng trùng

Timeout sau khi gửi hoặc worker chết giữa chừng có thể xảy ra sau khi nền tảng đã đăng bài. Job khi đó failed + needs_reconciliation=1, chặn retry tự động và retry thủ công. Operator kiểm tra bài trên nền tảng, gửi external_post_id qua POST W/variants/{id}/reconcile. API xác minh bài thuộc tài khoản trước khi ghi nhận thành công. Nếu xác nhận chưa đăng nhưng không có mã bài, cần quy trình vận hành có kiểm soát sau backup; API không tự mở khóa để gửi lại mù quáng.

Khóa DB/unique chỉ bảo đảm dữ liệu cục bộ. Không tuyên bố exactly-once đối với mọi API ngoài không hỗ trợ idempotency. Email có thể được nhận hai lần nếu SMTP đã nhận nhưng worker chết trước ghi sent; message-id ổn định giảm trùng, không bảo đảm exactly-once email.

## Việc phải xác nhận trước production

- Kiểm thử thật các định dạng nền tảng được chọn; chỉ mở capability đã nghiệm thu.
- Thử MP4, PDF tiếng Việt với font TTF, SMTP và Docker trên máy triển khai.
- Kiểm thử tải 100 người đồng thời, đo p95 và kiểm thử lịch đúng 60 giây trên hạ tầng thực tế; chưa có kết quả benchmark production.
- Bố trí worker phù hợp tải; tác vụ đồng bộ lớn có thể chiếm thời gian, không cam kết mọi lịch trong 60 giây khi chạy một worker quá tải.
- Thiết lập backup ngoài ứng dụng, giữ ít nhất 7 ngày, diễn tập restore; ứng dụng không tự cung cấp dịch vụ backup hay SLA 99%.
- Chốt chính sách lưu trữ/xóa dữ liệu cá nhân và dữ liệu workspace dùng chung; deletion request hiện vô hiệu hóa và ghi nhận yêu cầu, chưa tự xóa tất cả dữ liệu cá nhân.
- JWT không được dùng; session opaque trong header Bearer. Nếu frontend chuyển sang cookie, phải bổ sung CSRF và cấu hình cookie trước khi đổi cơ chế.
- Rate limiter hiện lưu trong memory của mỗi process, cần store chung khi triển khai nhiều instance.

## Thay đổi so với tài liệu gốc

Sửa FK MySQL để schema thực sự import được với generated unique columns; source migration giữ lịch/job/approval thay vì xóa cứng. Backend thêm các bảng vận hành vào migration 002. SRS/workbook không tự được viết lại trong lần triển khai code này; dùng tài liệu phạm vi này khi review những khác biệt như upload một tệp/request và định dạng live còn giới hạn.
