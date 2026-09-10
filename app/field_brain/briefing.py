"""Read-only excerpts of daily notes; never convert prose into confirmed facts."""

def recent_records(rows):
    result, seen = [], set()
    for row in rows:
        content = " ".join((row.get("summary") or "").split())
        key = (str(row.get("occurred_at_iso") or "")[:10], content)
        if content and key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def note_sections(text):
    labels = {
        "오늘 한 일": "기록한 작업",
        "남은 일과 일정": "남은 일과 일정",
        "차량·장비": "장비 기록",
        "예상과 달랐던 일": "예상과 달랐던 일",
        "돈": "금액 메모",
    }
    boundaries = set(labels) | {"날짜", "현장", "함께 일한 사람", "폐기물·고물", "사장이 한 말", "궁금하거나 이상했던 점"}
    sections, current = {}, None
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("# ")
        if not line:
            continue
        head, separator, rest = line.replace("：", ":").partition(":")
        if head.strip() in boundaries:
            current = labels.get(head.strip())
            line = rest.strip() if separator else ""
        if current and line and line not in sections.setdefault(current, []):
            sections[current].append(line)
    return [{"title": title, "lines": lines[:3]} for title, lines in sections.items() if lines]
