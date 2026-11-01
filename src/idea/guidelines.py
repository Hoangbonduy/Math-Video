"""Dynamic guideline loading and lossless extraction of the six-line idea."""

import hashlib
import re

from src.shared.paths import PROJECT_ROOT
from src.shared._workflow_store import WorkflowError

GUIDELINE_PATH = PROJECT_ROOT / 'guidelines' / 'guidelines.md'
# Legacy fallback only; the supplied file is authoritative when both exist.
SUPPLIED_GUIDELINE_PATH = PROJECT_ROOT / 'guidelines.md'
CLOSING_SENTENCE = 'Đăng ký kênh Axiora để biết thêm chi tiết.'
GOAL_MARKER = 'Ý tưởng kịch bản (6 dòng):'


def guideline_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_guidelines():
    path = GUIDELINE_PATH if GUIDELINE_PATH.exists() else SUPPLIED_GUIDELINE_PATH
    try:
        text = path.read_text(encoding='utf-8-sig').strip()
    except FileNotFoundError:
        raise WorkflowError('GUIDELINE_MISSING', f'Không tìm thấy {GUIDELINE_PATH} hoặc {SUPPLIED_GUIDELINE_PATH}') from None
    except (OSError, UnicodeError) as exc:
        raise WorkflowError('GUIDELINE_UNREADABLE', f'Không đọc được guideline: {exc}') from None
    if not text:
        raise WorkflowError('GUIDELINE_EMPTY', 'File guidelines.md đang trống')
    return text, guideline_digest(text)


def parse_idea_goal(goal_text):
    lines = [re.sub(r'^\s*\d+[.)]\s*', '', line.strip())
             for line in (goal_text or '').splitlines() if line.strip()]
    line5 = lines[4] if len(lines) >= 5 else ''
    answers = re.findall(r'\(([^)]+)\)', line5)
    # Remove answer annotations and labels before extracting the question verbatim.
    without_answers = re.sub(r'\([^)]*\)', '', line5).strip()
    without_label = re.sub(r'^.*?:\s*', '', without_answers, count=1)
    questions = re.findall(r'[^.!?…]*\?', without_label)
    question = questions[-1].strip(' ;—–-') if questions else ''
    return dict(raw_lines=lines,
                hook=lines[0] if lines else '',
                origin=lines[1] if len(lines) >= 2 else '',
                climb=lines[2] if len(lines) >= 3 else '',
                payoff=lines[3] if len(lines) >= 4 else '',
                twist_and_question=line5,
                line6=lines[5] if len(lines) >= 6 else CLOSING_SENTENCE,
                has_final_question='?' in without_answers,
                final_question=question,
                forbidden_spoken_answers=[re.sub(r'^(?:đáp án|answer)\s*:\s*', '', answer.strip(), flags=re.I)
                                          for answer in answers if answer.strip()])


def extract_goal_info(brief):
    if isinstance(brief.get('structured_goal'), dict):
        return brief['structured_goal']
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
        return text  # Saved guidelines before the phase-specific document format.
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
