# Luồng thiết kế cảnh (v3)

```text
Kịch bản + phong cách + khả năng hệ thống
  → Kế hoạch hình ảnh và chuyển động
  → Chọn recipe/API → đọc phần reference còn thiếu
  → Viết mã Manim bằng component và theme dùng chung
  → Kiểm tra cú pháp, quy tắc mã và runtime
  → Render trực tiếp 360 × 640, 30 FPS
      ├─ Thu hình học thực tế trong lúc render
      └─ Chọn 3–5 frame để đánh giá
  → Sửa đúng vấn đề → người dùng duyệt → xuất cuối
```

`scene_generation.py` chọn `scene_code_pipeline.py` cho lượt mới. JSON v3 chỉ lưu kế hoạch,
manifest và bằng chứng duyệt; mã Manim được viết trực tiếp. Các module v1/v2 phục vụ dữ liệu
đã lưu và đường tương thích. Mỗi lượt có fingerprint, cache request, ngân sách và bằng chứng
riêng; không xóa lịch sử cũ.

## Khung dọc và độ phân giải

`scene_theme.py` khóa khung **9 × 16 đơn vị Manim**, preview **360 × 640**, bản cuối
**1080 × 1920**, đều **30 FPS**. Không dùng sân khấu ngang 16:9 mặc định của Manim.
Chỉ mật độ pixel thay đổi; tọa độ, camera, cỡ chữ, nét vẽ và thời gian của cùng mã nguồn
giữ nguyên. Cấu hình ngang cũ vẫn đọc được, nhưng lượt v3 luôn dùng profile dọc.
Không nhân tọa độ/cỡ chữ theo hệ số 3 và không upscale preview thành bản cuối.

Vùng an toàn: lề trái/phải và trên 0.55 đơn vị; lề dưới 1.5 (180 px ở bản cuối,
60 px ở preview) cho phụ đề. `fit_to_safe_area` giới hạn thu nhỏ; `formula_lines` ngắt
công thức tại ranh giới ý nghĩa. Cỡ chữ hiệu dụng tối thiểu là 22. `vn_text(content, size=28)` là cách gọi chuẩn;
`font_size` được hỗ trợ để đọc mã đã sinh. Cỡ mặc định 28 dành khoảng an toàn cho biến đổi chữ số.
Không vừa ở cỡ tối thiểu thì chia cách trình bày thành các trạng thái/cụm hình xuất hiện
lần lượt trong cùng cảnh, giữ nguyên lời đọc và tổng thời lượng.

## Khả năng hệ thống và reference

`scene_references.py` cung cấp recipe, component, Python/Manim đang chạy và khả năng LaTeX.
Sau kế hoạch, chỉ đọc tài liệu cho API cần dùng (tối đa 16 API/12 tệp), đồng thời kiểm tra
signature/docstring **trên runtime thực tế**, gồm cả helper trong `manim_kit`.
Reference luôn kèm signature component; keyword không hợp lệ được báo trước khi render.
Các vòng sửa nhận cả lịch sử lỗi trước đó để tránh tái phạm lỗi đã sửa. Bộ reference `manim-0.22.1` có thể khác
phiên bản cài đặt; API cài đặt là nguồn quyết định. Thiếu API thì quay về kế hoạch.
Tên API nhận cả dạng ngắn (`Axes.plot`, `vn_text`) và tên đầy đủ
(`manim.Axes.plot`, `src.scene_design.manim_kit.vn_text`). Sai tên/namespace/recipe
được phản hồi về bước lập kế hoạch, không lặp lại bước viết mã chưa được chạy.
Không gửi cả reference vào mọi yêu cầu. MathTex dùng khi latex và dvisvgm sẵn sàng;
chữ tiếng Việt dùng DejaVu Sans. Công thức nhiều dòng bằng Text không cần LaTeX.

## Thu dữ liệu và sửa đúng tầng

Mã cảnh gọi `track(self, id, mobject, role, overlap_with=())` cho đối tượng có ý nghĩa,
đặc biệt từng nhãn/công thức tách khỏi panel/axes. Worker kiểm tra bbox thực tế mỗi frame;
đoạn chờ tĩnh được ghi như một khoảng lặp. Text/Tex chưa đăng ký vẫn được kiểm tra.
Đối tượng dự kiến phải được đăng ký, không cho bỏ kiểm tra bằng cách bỏ `track`.

`layout_telemetry.json` lưu số frame, vị trí/kích thước, cỡ chữ, mốc `self.play`, lỗi và
các mẫu hình học. Kiểm tra cục bộ phát hiện vượt vùng an toàn, chữ nhỏ, chồng bbox nhãn.
Chồng bbox hình/đường và sát mép là ứng viên cần xem ảnh, không tự kết luận va chạm thật.
Phép kiểm tra này không chứng minh ý nghĩa toán học hay mọi giao cắt đường cong.
AI dùng ảnh và tiêu chí kế hoạch để đánh giá phần còn lại.

Chỉ chọn tối đa 5 ảnh từ **video đã mã hóa**: ưu tiên lỗi/va chạm nghi ngờ/sát mép,
mở đầu, giữa/cuối play và frame cuối; ưu tiên tiếp các sự kiện vẽ, đổi số và di chuyển thay vì khoảng chờ tĩnh.
Reviewer nhận thời gian play cùng hộp bao đầu/cuối, biên chuyển động và số frame có thay đổi
của từng đối tượng; không suy ra thiếu chuyển động chỉ từ ảnh không chứa đúng mốc.
Text Transform được hiệu chuẩn theo cỡ chữ đích để tránh nhầm thay đổi glyph với thu font.
Sai lệch thời lượng trong 2 frame được chấp nhận nhất quán với cổng render.
Không gửi toàn bộ 300 ảnh của cảnh 10 giây. Video cực ngắn có thể có ít hơn 3 frame.

- Sai cú pháp/API/runtime, bố cục, nhịp hoặc liên hệ toán học: sửa **mã Manim**; giữ kế hoạch
  và tiêu chí, dùng lỗi có vị trí/thời gian làm phản hồi.
- Sai ý nghĩa, thiếu API hoặc `LAYOUT_REPLAN_REQUIRED`: quay về **kế hoạch hình ảnh**.
  Không xóa tiêu chí để giấu lỗi và không thu chữ tiếp dưới mức đọc được.
- Render hợp lệ nhưng AI chưa đạt sau số vòng cho phép: giữ bằng chứng và bản nháp để
  người dùng xem/góp ý/duyệt thủ công. Quyết định duyệt vẫn thuộc người dùng.

## Bản cuối và ghép tiếng

`render_final` render lại đúng mã đã duyệt ở 1080 × 1920 / 30 FPS; kiểm tra hash mã,
component/theme, phiên bản Manim rồi so sánh bbox theo mốc frame với preview.
Sai lệch vượt 0.03 đơn vị hoặc lỗi vùng an toàn sẽ chặn xuất. Đây là kiểm tra hình học,
không phải so pixel; anti-aliasing và độ rõ raster có thể khác theo độ phân giải.

C7 dùng bản render gốc này, giữ nguyên khung dọc và vùng phụ đề đã dành sẵn.
Khi ghép tiếng, C7 lưu tỷ lệ điều chỉnh tốc độ toàn cảnh theo thời lượng giọng đọc thực tế
trong `timing.json`, rồi đưa video có tiếng qua cổng duyệt hiện có, không sinh lại hình học.
Không áp cơ chế sửa timeline JSON cũ cho mã v3.


### Mẫu hình ảnh từ kịch bản ý tưởng

Kịch bản mới có `global_visuals` khai báo một lần và `visual_intent` ba mục:
Kế thừa & thay đổi, Timeline hình ảnh, Tiêu chí đạt. `script_visuals.py` đọc mẫu
thành `visual_handoff` cho planner, codegen và review; v3 lưu bản bàn giao tại
`visual_handoff.json`. Cảnh sau nhận `Kết cảnh` của cảnh trước, không khởi tạo
lại theo s1. Đây là trạng thái dự định trong kịch bản đã duyệt, chưa phải dữ liệu
đo từ clip trước.

Timeline dùng giây cục bộ hoặc mốc lời đọc/sự kiện. Planner ánh xạ vào thời lượng
cảnh thực tế, giữ nguyên chữ/số và thứ tự tiết lộ. Mọi tiêu chí hình ảnh đã duyệt
phải được chép nguyên văn vào `beats.criteria`; validator chặn việc bỏ tiêu chí,
review nhận cả bản bàn giao gốc. Kịch bản cũ chưa có `global_visuals` vẫn đi theo
luồng tương thích, không tự suy diễn trạng thái còn thiếu.
