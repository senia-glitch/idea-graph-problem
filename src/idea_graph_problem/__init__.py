"""Пакет «Этап 1. Проблема».

Строит иерархию карточек по слоям: домен → сценарий → проблема → требуемый результат.
Каждый слой — запись в таблице ``LAYERS`` (вопрос, критерий «достаточно», вид карточки, родитель).
Чтобы расширить этап новым слоем или вопросом, достаточно добавить запись в таблицу:
скилл для агента и поиск незакрытых мест строятся из неё.

Этап никогда не закрывается: код лишь показывает «незакрытые места» — где в дереве не хватает
потомка или поля. Решает всегда человек; агент — помощник, который уточняет и рекомендует.
"""

from __future__ import annotations

import re
from pathlib import Path

from idea_graph_core import ACCEPTED, DETAIL, PROPOSED, ROOT_KIND, is_human

__version__ = "0.1.0"

DOMAIN, SCENARIO, PROBLEM, RESULT = "домен", "сценарий", "проблема", "результат"
IMPORTANCE = ("низкая", "средняя", "высокая", "критическая")
MAX_FOLLOWUPS = 5          # уточняющих вопросов на одну карточку; меньше можно, если контекст полон

# Слои этапа: сверху вниз. ``human_only`` — карточку вида пишет только человек.
# ``field`` — обязательное поле в описании карточки: последняя строка «<Название>: <значение>».
LAYERS: list[dict] = [
    {"kind": DOMAIN, "parent": ROOT_KIND, "human_only": True,
     "question": "Кто испытывает проблему?",
     "sufficient": "названа конкретная группа людей с отличительным признаком, а не «все» и не «люди»"},
    {"kind": SCENARIO, "parent": DOMAIN,
     "question": "В какой ситуации она возникает?",
     "sufficient": "указан момент, место или событие, после которого возникает проблема"},
    {"kind": PROBLEM, "parent": SCENARIO,
     "question": "Что именно не так?",
     "sufficient": "описано конкретное событие или затруднение, а не готовое решение («нет X»)",
     "field": {"name": "Важность", "values": IMPORTANCE,
               "question": "Насколько это важно?",
               "sufficient": "известны частота и цена проблемы (время, деньги, риск, усилия); "
                             "итог записан одним словом: " + ", ".join(IMPORTANCE)}},
    {"kind": RESULT, "parent": PROBLEM,
     "question": "Какой результат человек пытается получить?",
     "sufficient": "результат сформулирован без упоминания способа его достижения"},
]
KINDS = [layer["kind"] for layer in LAYERS]
_BY_KIND = {layer["kind"]: layer for layer in LAYERS}


def field_value(card: dict, field: dict) -> str | None:
    """Значение поля из описания карточки («Важность: высокая») или None, если его нет или оно не из списка."""
    found = re.findall(rf"(?im)^\s*{re.escape(field['name'])}\s*:\s*(.+?)\s*$", card.get("body") or "")
    if not found:
        return None
    value = found[-1].strip().rstrip(".").lower()
    return value if value in field["values"] else None


def _cards(store, kind: str) -> list[dict]:
    return sorted((c for c in store.cards.values() if c["kind"] == kind), key=lambda c: int(c["id"][1:]))


def _parent(store, card: dict) -> dict | None:
    for edge in store.edges.values():
        if edge["kind"] == DETAIL and edge["from"] == card["id"]:
            return store.cards.get(edge["to"])
    return None


def _name(card: dict) -> str:
    return f"{card['id']} «{card['title']}»"


def assess(store) -> dict:
    """Незакрытые места дерева. Только код, без модели.

    ``gaps`` — чего не хватает (каждое: слой, карточка, текст, вопрос для агента);
    ``pending`` — карточки, ждущие решения человека; ``misplaced`` — карточки не на своём месте.
    """
    gaps: list[dict] = []
    pending: list[str] = []
    misplaced: list[str] = []

    for layer in LAYERS:
        for card in _cards(store, layer["kind"]):
            if card["status"] == PROPOSED:
                pending.append(f"{_name(card)} ({layer['kind']})")
            parent = _parent(store, card)
            if parent is None or parent["kind"] != layer["parent"]:
                misplaced.append(f"{_name(card)} ({layer['kind']}) должна быть потомком карточки вида «{layer['parent']}»")
            if layer.get("human_only") and not is_human(card.get("author", "")):
                misplaced.append(f"{_name(card)}: карточку «{layer['kind']}» создал не человек — её пишет только пользователь")

    domains = [c for c in _cards(store, DOMAIN) if c["status"] == ACCEPTED]
    if not domains:
        gaps.append({"layer": DOMAIN, "card": None, "question": _BY_KIND[DOMAIN]["question"],
                     "text": "Нет ни одного домена. Его пишет пользователь вручную: кто испытывает проблему?"})

    for index, layer in enumerate(LAYERS):
        if index == 0:
            continue
        child_kind = layer["kind"]
        for parent in (c for c in _cards(store, layer["parent"]) if c["status"] == ACCEPTED):
            owned = [c for c in _cards(store, child_kind) if c["status"] == ACCEPTED
                     and (_parent(store, c) or {}).get("id") == parent["id"]]
            if not owned:
                gaps.append({"layer": child_kind, "card": parent["id"], "question": layer["question"],
                             "text": f"У {layer['parent']} {_name(parent)} нет ни одного принятого "
                                     f"«{child_kind}». {layer['question']}"})
            field = layer.get("field")
            for child in owned if field else []:
                if field_value(child, field) is None:
                    gaps.append({"layer": child_kind, "card": child["id"], "question": field["question"],
                                 "text": f"У {child_kind} {_name(child)} не указано поле «{field['name']}» "
                                         f"(последняя строка описания: «{field['name']}: "
                                         f"{' | '.join(field['values'])}»). {field['question']}"})
    return {"gaps": gaps, "pending": pending, "misplaced": misplaced,
            "clean": not (gaps or pending or misplaced)}


def tree(store) -> list[str]:
    """Дерево этапа для вводных агента: отступ — глубина, у проблемы показана важность."""
    out: list[str] = []

    def walk(card: dict, depth: int) -> None:
        layer = _BY_KIND[card["kind"]]
        mark = "" if card["status"] == ACCEPTED else " [ждёт решения]"
        extra = ""
        if layer.get("field"):
            extra = f" · {layer['field']['name'].lower()}: {field_value(card, layer['field']) or 'не указана'}"
        out.append("  " * depth + f"- {card['kind']} {card['id']} «{card['title']}»{extra}{mark}")
        index = KINDS.index(card["kind"])
        if index + 1 < len(LAYERS):
            for child in _cards(store, KINDS[index + 1]):
                if (_parent(store, child) or {}).get("id") == card["id"]:
                    walk(child, depth + 1)

    for domain in _cards(store, DOMAIN):
        walk(domain, 0)
    return out or ["(доменов пока нет)"]


def prepare(ctx) -> str:
    """Вводные в начале запуска: что уже есть и какие места не закрыты."""
    info = assess(ctx.store)
    lines = ["Этап 1 «Проблема». Дерево сейчас:"]
    lines.extend("  " + line for line in tree(ctx.store))
    if info["misplaced"]:
        lines.append("Не на своём месте (сначала скажи об этом человеку, не исправляй за него домены):")
        lines.extend("  - " + item for item in info["misplaced"])
    if info["pending"]:
        lines.append("Ждут решения человека (черновики):")
        lines.extend("  - " + item for item in info["pending"])
    if info["gaps"]:
        lines.append("Незакрытые места (проверено кодом), по порядку:")
        lines.extend("  - " + gap["text"] for gap in info["gaps"][:12])
        lines.append("Начни с первого, если человек не указал другого. Расширять можно и вширь: "
                     "новый сценарий, новая проблема, новый результат.")
    elif not info["pending"] and not info["misplaced"]:
        lines.append("Незакрытых мест нет. Скажи об этом человеку и предложи, что можно расширить; "
                     "если он ничего не просит — вызови finish с итогом «пробелов_нет».")
    return "\n".join(lines)


def conclude(ctx, outcome: str) -> str:
    """Итог считает код: этап не закрывается, итог лишь говорит, есть ли незакрытые места."""
    info = assess(ctx.store)
    ctx.state["gaps"] = [gap["text"] for gap in info["gaps"]]
    ctx.state["last_revision"] = ctx.store.revision
    ctx.save_state()
    return "пробелов_нет" if info["clean"] else "есть_пробелы"


def layers_text() -> str:
    """Таблица слоёв для скилла — строится из ``LAYERS``."""
    out = []
    for number, layer in enumerate(LAYERS, 1):
        who = "**пишет только человек**" if layer.get("human_only") else "предлагает агент, принимает человек"
        parent = "корень идеи" if layer["parent"] == ROOT_KIND else f"«{layer['parent']}»"
        out.append(f"{number}. **{layer['kind']}** (потомок: {parent}; {who})\n"
                   f"   - Вопрос: {layer['question']}\n   - Достаточно, если: {layer['sufficient']}")
        field = layer.get("field")
        if field:
            out.append(f"   - Поле «{field['name']}» (последняя строка описания: «{field['name']}: "
                       f"{' | '.join(field['values'])}»)\n     - Вопрос: {field['question']}\n"
                       f"     - Достаточно, если: {field['sufficient']}")
    return "\n".join(out)


def manifest() -> dict:
    skill = (Path(__file__).parent / "skill.md").read_text(encoding="utf-8")
    return {
        "id": "problem",
        "name": "Этап 1. Проблема",
        "description": "Помогает сформулировать проблему слоями: домен → сценарий → проблема → результат. "
                       "Уточняет, рекомендует варианты, структурирует; решает всегда человек.",
        "kind": "agent",
        "order": 5,
        "reads": ["идея", *KINDS],
        "writes": [SCENARIO, PROBLEM, RESULT],
        "asks": True,
        "web_search": False,
        "model_level": "сильная",
        "outcomes": ["пробелов_нет", "есть_пробелы"],
        "skill": skill.replace("{{LAYERS}}", layers_text()).replace("{{MAX_FOLLOWUPS}}", str(MAX_FOLLOWUPS)),
        "prepare": prepare,
        "conclude": conclude,
    }
