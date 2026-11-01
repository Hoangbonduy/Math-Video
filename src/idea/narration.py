"""Narration-first drafting and editorial checks for newly generated scripts."""

import json
import re

from src.idea.content import (SCRIPT_SCHEMA, _object, _TEXT, align_evidence_spacing,
                              check_schema, validate_script, writing_messages)
from src.shared._workflow_store import WorkflowError


PROMPT_VERSION = 2
NARRATION_SCHEMA = _object(dict(title=_TEXT, main_idea=_TEXT,
                               claims=SCRIPT_SCHEMA['properties']['claims'], narration=_TEXT))


def narration_messages(topic, brief, sources, *, previous=None, feedback=None):
    messages = writing_messages(topic, brief, sources, previous=previous, feedback=feedback)
    messages[0]['content'] += (
        " Bước hiện tại: chỉ viết narration thành MỘT đoạn lời đọc liên tục cùng title, main_idea và claims. "
        "Chưa chia scenes, chưa viết hướng dẫn hình ảnh vào narration. "
        "Giữ dữ kiện chi tiết cần hiện trên hình trong claims có bằng chứng để bước sau sử dụng.")
    return messages


def editing_messages(topic, brief, sources, draft, *, previous=None, feedback=None):
    messages = writing_messages(topic, brief, sources, previous=previous, feedback=feedback)
    messages[0]['content'] += (
        " Bước hiện tại: bạn là biên tập viên, rà soát narration_draft trước khi chia cảnh. "
        "Đọc toàn đoạn, phát hiện và VIẾT LẠI các chỗ lặp nghĩa, thiếu cầu nối, nhảy mốc, "
        "câu dài hoặc nặng số; kiểm tra payoff trả đúng hook và kết tự nhiên. "
        "Sau khi sửa liền mạch, chia thành scenes, mỗi cảnh 1–2 câu và một ý mới. "
        "Nối các scene.narration phải khôi phục đúng đoạn đã biên tập. "
        "Chuyển số chi tiết từ claims sang visual_intent, nêu rõ chữ/số cần hiện và hành động Manim; "
        "không làm mất điều kiện giả định hay nguồn của dữ kiện. Giữ ID cảnh cũ khi sửa nếu phù hợp. "
        "Trả SCRIPT_SCHEMA với title, main_idea, claims, scenes; không trả narration_draft.")
    # Keep topic, brief, sources and feedback available in both phases.
    context = json.loads(messages[1]['content'])
    context['narration_draft'] = draft
    messages[1]['content'] = json.dumps(context, ensure_ascii=False)
    return messages


def validate_narration(draft, sources, brief):
    draft = align_evidence_spacing(draft, sources, schema=NARRATION_SCHEMA)
    check_schema(draft, NARRATION_SCHEMA)
    # Reuse evidence/ID/duration validation without inventing final scene boundaries.
    script = dict(title=draft['title'], main_idea=draft['main_idea'], claims=draft['claims'],
                  scenes=[dict(scene_id='narration_draft', narration=draft['narration'],
                               visual_intent='Chưa chia cảnh',
                               claim_refs=[claim['claim_id'] for claim in draft['claims']])])
    validate_script(script, sources, brief)
    return draft


def _sentences(text):
    # Decimal/thousands separators have no following whitespace and stay intact.
    return [part.strip() for part in re.split(r'(?<=[.!?…])\s+', text.strip()) if part.strip()]


_NUMBER_WORDS = set('không một mốt hai ba bốn tư năm lăm sáu bảy tám chín mười mươi trăm nghìn ngàn triệu tỷ tỉ lẻ linh phẩy'.split())
_MAGNITUDES = set('trăm nghìn ngàn triệu tỷ tỉ phẩy'.split())


def _long_numbers(text):
    total = len(re.findall(r'(?<!\w)(?:\d+(?:[.,]\d+)+|\d{1,3}(?:[ \u00a0]\d{3})+|\d{4,})(?!\w)', text))
    # Count spelled-out runs as well as digits; punctuation closes each run.
    run = []
    for token in re.findall(r'\w+|[^\w\s]', text.casefold()) + ['.']:
        if token in _NUMBER_WORDS:
            run.append(token)
        else:
            if (len(run) >= 5 and _MAGNITUDES.intersection(run)) or ('phẩy' in run and len(run) >= 3):
                total += 1
            run = []
    return total


def validate_editorial(script):
    """Catch concrete failures; semantic repetition/transitions are edited by the model.

    Do not require a fixed list of transition words: that would reject natural links.
    The 8–14 syllable sentence length is a writing target, not a brittle hard gate.
    """
    issues, seen = [], {}
    all_narration = ' '.join(scene['narration'] for scene in script['scenes'])
    for scene in script['scenes']:
        sid, text = scene['scene_id'], scene['narration']
        sentences = _sentences(text)
        if len(sentences) > 2:
            issues.append(f'{sid}: mỗi cảnh chỉ có 1–2 câu; hãy chia lại tại ranh giới ý')
        for sentence in sentences:
            key = ' '.join(re.findall(r'\w+', sentence.casefold()))
            if key in seen:
                issues.append(f'{sid}: lặp câu đã có ở {seen[key]}; bỏ ý thừa và nối lại mạch kể')
            seen[key] = sid
        normalized = ' '.join(re.findall(r'\w+', text.casefold()))
        for filler in ('hãy xem điều gì xảy ra', 'cùng tìm hiểu nhé', 'như bạn thấy trên màn hình'):
            if filler in normalized:
                issues.append(f'{sid}: bỏ câu đệm/tả lại hình: {filler}')
        for sentence in sentences:
            words = re.findall(r'\w+', sentence.casefold())
            if 'follow' in words:
                if not 5 <= len(words) <= 7 or re.search(r'giả định|mô hình|ngoài đời|thực tế', sentence.casefold()):
                    issues.append(f'{sid}: follow phải là câu riêng 5–7 tiếng, tách khỏi lưu ý')
    if _long_numbers(all_narration) > 1:
        issues.append('Cả video chỉ đọc tối đa một số dài; chuyển số và so sánh chi tiết sang visual_intent')
    if issues:
        raise WorkflowError('INVALID_SCRIPT', 'Biên tập lời đọc: ' + '; '.join(issues))
