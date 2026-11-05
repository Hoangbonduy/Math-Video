"""Dynamic guideline loading and lossless extraction of the six-line idea."""

import hashlib
import re

from src.shared.paths import PROJECT_ROOT
from src.shared._workflow_store import WorkflowError

GUIDELINE_PATH = PROJECT_ROOT / 'guidelines' / 'guidelines.md'
GOAL_MARKER = 'Ý tưởng kịch bản (6 dòng):'


def guideline_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_guidelines():
    path = GUIDELINE_PATH
    try:
        text = path.read_text(encoding='utf-8-sig').strip()
    except FileNotFoundError:
        raise WorkflowError('GUIDELINE_MISSING', f'Không tìm thấy {GUIDELINE_PATH}') from None
    except (OSError, UnicodeError) as exc:
        raise WorkflowError('GUIDELINE_UNREADABLE', f'Không đọc được guideline: {exc}') from None
    if not text:
        raise WorkflowError('GUIDELINE_EMPTY', 'File guidelines.md đang trống')
    return text, guideline_digest(text)


def parse_idea_goal(goal_text):
    # Preserve numbered slots: omitting line 5 must not turn line 6 into line 5.
    slots, lines = {}, []
    next_slot = 1
    for raw in (goal_text or '').splitlines():
        match = re.match(r'^\s*([1-6])[.)]\s*(.*)$', raw)
        if match:
            slot, text = int(match[1]), match[2].strip()
        elif raw.strip():
            slot, text = next_slot, raw.strip()
        else:
            continue
        slots[slot] = text
        lines.append(text)
        next_slot = slot + 1
    def content(slot):
        return re.sub(r'^[^:?!]*:\s*', '', slots.get(slot, ''), count=1).strip()
    line5, closing = content(5), content(6)
    answers = re.findall(r'\(([^)]+)\)', line5)
    without_answers = re.sub(r'\([^)]*\)', '', line5).strip()
    # A period inside a decimal/thousands separator is not a sentence boundary.
    parts = re.split(r'(?<=[.!?…])\s+', without_answers)
    questions = [part for part in parts if part.endswith('?')]
    question = questions[-1].strip(' ;—–-') if questions else ''
    question_followup = without_answers.split(questions[-1], 1)[1].strip() if questions else ''
    return dict(raw_lines=lines,
                hook=slots.get(1, ''), origin=slots.get(2, ''),
                climb=slots.get(3, ''), payoff=slots.get(4, ''),
                twist_and_question=slots.get(5, ''), line6=slots.get(6, ''),
                has_line5=bool(without_answers), has_line6=bool(closing),
                closing_text=closing, has_final_question=bool(question),
                final_question=question, question_followup=question_followup,
                forbidden_spoken_answers=[re.sub(r'^(?:đáp án|answer)\s*:\s*', '', answer.strip(), flags=re.I)
                                          for answer in answers if answer.strip()])


def extract_goal_info(brief):
    if isinstance(brief.get('structured_goal'), dict):
        goal = brief['structured_goal']
        if 'has_line5' in goal and 'closing_text' in goal:
            return goal
        return parse_idea_goal('\n'.join(goal.get('raw_lines', [])))
    if 'goal_raw' in brief:
        return parse_idea_goal(brief['goal_raw'])
    requirements = brief.get('requirements', '')
    if GOAL_MARKER not in requirements:
        return parse_idea_goal('')
    goal = requirements.split(GOAL_MARKER, 1)[1]
    goal = re.split(r'\n(?:Cách thể hiện:|Cách hiện bằng Manim)', goal, maxsplit=1)[0]
    return parse_idea_goal(goal)


def guidelines_for_phase(text, phase):
    """Keep phase B and its self-check out of the narration judge's rubric."""
    if phase != 'narration':
        return text
    sections = re.split(r'(?im)(?=^##\s+PHẦN\s+[ABC]\b)', text)
    if len(sections) == 1:
        return text
    kept = []
    for section in sections:
        if re.match(r'(?i)^##\s+PHẦN\s+B\b', section):
            continue
        kept.append('\n'.join(line for line in section.splitlines() if '[Pha 2]' not in line))
    return '\n'.join(kept).strip()


def user_idea_source(brief):
    """Separate user-provided mathematical premises from retrieved publications."""
    lines = extract_goal_info(brief)['raw_lines']
    return dict(source_id='user_idea', text='\n'.join(lines)) if lines else None
