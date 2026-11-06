"""Shared visual declarations and the three-part scene handoff."""

import re

from src.shared._workflow_store import WorkflowError

GLOBAL_LABELS = (
    ('objects', 'Đối tượng dùng chung'),
    ('layout', 'Bố cục, góc nhìn và phong cách hình ảnh'),
    ('math', 'Hệ tọa độ, đơn vị, thông số và quan hệ toán học'),
    ('continuity', 'Quy tắc kế thừa trạng thái và ràng buộc xuyên suốt'),
)
SCENE_HEADINGS = (
    '1. Kế thừa & thay đổi',
    '2. Timeline hình ảnh',
    '3. Tiêu chí đạt',
)

VISUAL_INSTRUCTIONS = """
KHAI BÁO TOÀN CỤC — chỉ một lần ở đầu kịch bản, trong global_visuals:
- objects: tên/ID ổn định, cấu tạo, số lượng, kích thước và trạng thái ban đầu của đối tượng dùng chung.
- layout: bố cục, góc nhìn và phong cách hình ảnh.
- math: hệ tọa độ, đơn vị, thông số và quan hệ toán học dùng chung.
- continuity: quy tắc kế thừa trạng thái và các ràng buộc xuyên suốt.
Ghi cụ thể theo nội dung, không chép lại chỗ trống của mẫu. Nếu một mục không áp dụng,
ghi rõ lý do; không bịa dữ kiện. Không lặp khai báo toàn cục dưới từng lời đọc.

Mỗi scene.visual_intent là chuỗi nhiều dòng với đúng ba tiêu đề, theo thứ tự:
1. Kế thừa & thay đổi
Kế thừa: Khởi tạo theo khai báo toàn cục nếu là s1; các cảnh sau lấy Trạng thái cuối
cảnh trước (ghi ID cảnh trước cùng trạng thái cụ thể).
Thay đổi: Đối tượng mới kèm cấu tạo, kích thước / Đối tượng bị bỏ / Thay đổi bố cục /
Trạng thái cũ được gọi lại / Giữ nguyên. Ghi thay đổi thực tế; không liệt kê mọi lựa chọn.
2. Timeline hình ảnh
- Mốc giây CỤC BỘ từ 0 của cảnh hoặc mốc lời đọc/sự kiện → Đối tượng/nhóm →
Thao tác + thông số/đích đến → Chữ, số nguyên văn và điểm nhấn đồng thời nếu có
(nếu không có, ghi Không có) → Kết quả.
Mỗi sự kiện trên một dòng; không dùng giây toàn video làm giây cục bộ.
Nếu chữ nguyên văn chứa ký hiệu →, đặt toàn bộ chữ đó trong dấu ngoặc kép.
Kết cảnh: Vị trí, trạng thái và giá trị cụ thể của các đối tượng giữ sang cảnh sau.
3. Tiêu chí đạt
- Kết quả hình học, chuyển động hoặc hiển thị cụ thể có thể kiểm tra.
- Điều kiện riêng về ý nghĩa, thứ tự hoặc thời điểm tiết lộ thông tin.

Giữ danh tính và kích thước đối tượng nhất quán; mọi reset/gọi lại trạng thái cũ phải
được khai báo ở Thay đổi. Phân biệt thay đổi camera/bố cục với thay đổi vật liệu.
Không tự dựng lại trạng thái ban đầu giữa các cảnh. Mô tả mốc và đích đến đủ rõ để
bước thiết kế cảnh thực hiện; không dùng “trượt một nấc” mà thiếu khoảng dịch.
"""


def _fail(message):
    raise WorkflowError('INVALID_SCRIPT', 'Hình ảnh: ' + message)


def scene_sections(text):
    """Split only the canonical format; old free-form scripts remain readable."""
    pattern = r'(?m)^(' + '|'.join(re.escape(h) for h in SCENE_HEADINGS) + r')\s*$'
    parts = re.split(pattern, text.strip())
    if len(parts) != 7 or parts[0].strip() or tuple(parts[1::2]) != SCENE_HEADINGS:
        _fail('visual_intent phải có đúng 3 mục: ' + '; '.join(SCENE_HEADINGS))
    bodies = [part.strip() for part in parts[2::2]]
    if any(not body for body in bodies):
        _fail('Mỗi mục hình ảnh phải có nội dung')
    return bodies


def _field(body, label):
    match = re.search(r'(?m)^' + re.escape(label) + r':[^\S\n]*(\S[^\n]*)$', body)
    if not match:
        _fail('Thiếu nội dung ' + label + ':')
    return match[1]


def timeline_cells(line):
    """Allow literal arrows inside quoted on-screen captions."""
    cells, current, closing = [], [], None
    for char in line.strip().removeprefix('- '):
        if closing:
            current.append(char)
            if char == closing:
                closing = None
        elif char in ('"', '“'):
            closing = '"' if char == '"' else '”'
            current.append(char)
        elif char == '→':
            cells.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append(''.join(current).strip())
    return cells


def validate_visuals(script):
    """Validate the handoff format, without pretending to prove its geometry."""
    for index, scene in enumerate(script['scenes']):
        inheritance, timeline, criteria = scene_sections(scene['visual_intent'])
        inherited = _field(inheritance, 'Kế thừa')
        _field(inheritance, 'Thay đổi')
        if index == 0:
            if 'khai báo toàn cục' not in inherited.casefold():
                _fail('s1 phải khởi tạo theo khai báo toàn cục')
        else:
            previous = script['scenes'][index - 1]['scene_id']
            if not re.search(r'(?<!\w)' + re.escape(previous) + r'(?!\w)', inherited):
                _fail(scene['scene_id'] + ': Kế thừa phải chỉ rõ trạng thái cuối ' + previous)
        _field(timeline, 'Kết cảnh')
        events = [line.strip().removeprefix('- ').strip() for line in timeline.splitlines()
                  if line.strip() and not line.startswith('Kết cảnh:')]
        if not events or any(len(timeline_cells(line)) != 5 or
                             any(not cell for cell in timeline_cells(line)) for line in events):
            _fail(scene['scene_id'] + ': mỗi mốc timeline cần 5 phần ngăn bằng →')
        checks = [line[2:].strip() for line in criteria.splitlines() if line.startswith('- ')]
        if len(checks) < 2 or any(not check for check in checks):
            _fail(scene['scene_id'] + ': cần tiêu chí kết quả và điều kiện ý nghĩa/thứ tự/thời điểm')


def global_markdown(script):
    if 'global_visuals' not in script:
        return []
    lines = ['## Khai báo toàn cục', '']
    for key, label in GLOBAL_LABELS:
        lines += ['### ' + label, '', script['global_visuals'][key], '']
    return lines


def scene_markdown(visual):
    try:
        bodies = scene_sections(visual)
    except WorkflowError:
        return visual
    blocks = []
    for heading, body in zip(SCENE_HEADINGS, bodies):
        # Blank lines keep state fields separate when rendered as Markdown.
        lines = body.splitlines()
        formatted = []
        for line in lines:
            if line.startswith(('Kế thừa:', 'Thay đổi:', 'Kết cảnh:')) and formatted:
                formatted.append('')
            formatted.append(line)
        blocks.append('#### ' + heading + '\n\n' + '\n'.join(formatted))
    return '\n\n'.join(blocks)
