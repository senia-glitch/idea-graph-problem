"""Этап «Процессы»: роли → сценарии → выбор → «как есть» с болями → вид автоматизации → «как будет».

Что делает: помогает начальнику разработки описать бизнес-процессы сферы и решить, что и как автоматизировать.
Опирается на банки проекта (анализ конкурентов из этапа «Разведка»), если они есть; без них работает так же,
но предложения строятся без опоры на конкурентов.

Весь код этапа — декларация (шаги, виды карточек, ворота, промпты) и проверки; механику (запуск агентов,
совет, ревью, предложения правок, ворота) выполняет движок, а рисует диаграммы статический модуль ``static``.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import diagrams
from .diagrams import IMPORTANCE

__version__ = "0.2.0"

SPHERE, ROLE, SCENARIO = "сфера", "роль", "сценарий"
ASIS, SOLUTION, TOBE = "как есть", "решение", "как будет"
PARTICIPATES = "участвует"
AUTOMATE_FIELD = "Автоматизировать"
AUTOMATE_VALUES = ("да", "нет", "позже")
FREQUENCY = ("редко", "иногда", "часто", "постоянно")
AUTOMATION_KINDS = ("убрать шаг", "автоматизировать шаг", "заменить роль системой", "интеграция с внешним сервисом",
                    "уведомление", "другое")
DEFAULT_LIMITS = {"roles": 8, "scenarios": 6}
HERE = Path(__file__).parent


def _read(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------- чтение графа и банков

def _cards(store, kind: str) -> list[dict]:
    return sorted((c for c in store.cards.values() if c["kind"] == kind), key=lambda c: int(c["id"][1:]))


def _field(card: dict, name: str) -> str:
    """Значение поля из описания карточки: последняя строка «Название: значение» (строчными буквами)."""
    found = re.findall(rf"(?im)^\s*{re.escape(name)}\s*:\s*(.+?)\s*$", card.get("body") or "")
    return found[-1].strip().rstrip(".").lower() if found else ""


def _children(store, parent_id: str, kind: str) -> list[dict]:
    out = []
    for edge in store.edges.values():
        if edge["kind"] == "детализирует" and edge["to"] == parent_id:
            card = store.cards.get(edge["from"])
            if card and card["kind"] == kind:
                out.append(card)
    return sorted(out, key=lambda c: int(c["id"][1:]))


def _roles_of(store, scenario_id: str) -> list[dict]:
    ids = [e["to"] for e in store.edges.values() if e["kind"] == PARTICIPATES and e["from"] == scenario_id]
    return [store.cards[i] for i in ids if i in store.cards]


def selected_scenarios(store) -> list[dict]:
    return [c for c in _cards(store, SCENARIO) if _field(c, AUTOMATE_FIELD) == "да"]


def bank_summary(ctx) -> str:
    """Что известно о конкурентах (банк «Разведки»): роли и процессы с числом конкурентов, у которых они найдены."""
    records = ctx.bank("analysis").list("analysis")
    if not records:
        return "Банк analysis пуст: анализ конкурентов не проводился. Предлагай, опираясь на здравый смысл и текст человека."
    roles: dict[str, int] = {}
    processes: dict[str, int] = {}
    for record in records:
        data = record["data"] or {}
        for role in data.get("roles") or []:
            name = (role.get("name") if isinstance(role, dict) else str(role)).strip()
            if name:
                roles[name] = roles.get(name, 0) + 1
        for process in data.get("processes") or []:
            name = (process.get("name") if isinstance(process, dict) else str(process)).strip()
            if name:
                processes[name] = processes.get(name, 0) + 1
    top = lambda table: "; ".join(f"{k} ({v})" for k, v in sorted(table.items(), key=lambda kv: -kv[1])[:15]) or "—"  # noqa: E731
    return (f"Анализ конкурентов (записей {len(records)}; в скобках — у скольких конкурентов найдено).\n"
            f"Роли: {top(roles)}.\nПроцессы: {top(processes)}.")


def _sphere_line(card: dict | None) -> str:
    if card is None:
        return "сфера не указана"
    body = (card.get("body") or "").strip()
    return f"{card['id']} «{card['title']}»" + (f" ({body})" if body else "")


def _sphere_of(ctx, target: dict | None) -> dict | None:
    if target is None:
        found = _cards(ctx.store, SPHERE)
        return found[0] if found else None
    if target["kind"] == SPHERE:
        return target
    for edge in ctx.store.edges.values():           # вверх по «детализирует» до сферы
        if edge["kind"] == "детализирует" and edge["from"] == target["id"]:
            parent = ctx.store.cards.get(edge["to"])
            if parent is not None:
                return _sphere_of(ctx, parent)
    found = _cards(ctx.store, SPHERE)
    return found[0] if found else None


def _scenario_block(ctx, scenario: dict) -> str:
    roles = ", ".join(r["title"] for r in _roles_of(ctx.store, scenario["id"])) or "роли не указаны"
    return f"{scenario['id']} «{scenario['title']}» (роли: {roles})" + (
        f"\n    {scenario['body'].strip()}" if (scenario.get("body") or "").strip() else "")


def _targets_text(ctx, target: dict | None) -> str:
    """С чем работать на шагах «как есть» и «решение»: с выбранным сценарием или со всеми выбранными для автоматизации."""
    if target is not None and target["kind"] == SCENARIO:
        return "Работай с одним сценарием:\n  " + _scenario_block(ctx, target)
    chosen = selected_scenarios(ctx.store)
    if not chosen:
        return "Сценариев, отмеченных «Автоматизировать: да», нет: скажи об этом в finish и попроси человека отметить сценарии."
    return "Работай с каждым сценарием, отмеченным «Автоматизировать: да»:\n" + "\n".join("  " + _scenario_block(ctx, s) for s in chosen)


# ---------------------------------------------------------------------- промпты шагов

DIAGRAM_HELP = (
    "Диаграмму записывай в attrs.diagram карточки: {\"type\":\"sequence\",\"participants\":[\"Пациент\",\"Регистратура\"],"
    "\"steps\":[{\"from\":\"Пациент\",\"to\":\"Регистратура\",\"text\":\"Звонит и просит запись\","
    "\"pain\":{\"what\":\"…\",\"often\":\"часто\",\"cost\":\"20 минут\",\"importance\":\"низкая|средняя|высокая|критическая\"},"
    "\"mark\":\"\"}]}. Шаги по порядку, участники — только роли сценария и (в «как будет») «Система», «БД», внешние ресурсы; "
    "pain — только если на шаге есть реальная боль; mark — только в «как будет»: new, changed, auto или removed."
)


def _p_roles_suggest(ctx, step, target) -> str:
    sphere = _sphere_of(ctx, target)
    return (f"Предложи роли участников процессов сферы {_sphere_line(sphere)}.\n{bank_summary(ctx)}\n"
            "Задай человеку один вопрос через ask: «Какие роли участвуют в процессах этой сферы?» — 2–4 готовых варианта "
            "(названия ролей, можно несколько), рекомендуй подходящие. После ответа каждая выбранная роль — отдельная карточка "
            f"вида «{ROLE}» (add_card, status proposed, parent — карточка сферы, title — название роли существительным, body — одна "
            "строка, что роль делает), свои слова человека, если это ещё одна роль, — тоже отдельная карточка. Решение записывай "
            'через "decision":"last". Потом, если вариантов ролей осталось много, можно задать следующий вопрос. Закончи finish.')


def _p_roles_review(ctx, step, target) -> str:
    return (f"Проверь роли сферы {_sphere_line(_sphere_of(ctx, target))}: полнота относительно банка конкурентов, дубли и "
            "синонимы, лишнее.\n" + bank_summary(ctx) + "\nЗамечания оставь карточками «замечание» (parent — проверяемая карточка или сфера).")


def _p_roles_clarify(ctx, step, target) -> str:
    return ("Проверь, что названия ролей единообразны и нет двух ролей с одним смыслом. Неточности вынеси человеку одним вопросом "
            "через ask с вариантами (оставить обе, объединить в одну, переименовать).")


def _p_scenarios_suggest(ctx, step, target) -> str:
    roles = ", ".join(f"{r['id']} «{r['title']}»" for r in _cards(ctx.store, ROLE)) or "ролей пока нет"
    return (f"Предложи сценарии (бизнес-процессы) сферы {_sphere_line(_sphere_of(ctx, target))}. Роли: {roles}.\n{bank_summary(ctx)}\n"
            "Задай один вопрос через ask: «Какие бизнес-процессы автоматизируем?» — 2–4 варианта (ситуации одной строкой), можно "
            f"несколько, рекомендуй. Каждый выбранный сценарий — отдельная карточка вида «{SCENARIO}» (status proposed, parent — "
            f"карточка сферы, title — ситуация одной строкой, body — суть) и связи «{PARTICIPATES}» от сценария к каждой роли-участнице "
            f'(links: [{{"to":"<id роли>","kind":"{PARTICIPATES}"}}]). Если сценарию нужна роль, которой нет, не создавай её молча: '
            "спроси через ask, добавить ли её, подменить существующей или убрать из сценария. Закончи finish.")


def _p_scenarios_review(ctx, step, target) -> str:
    return ("Проверь сценарии: нет ли пропущенных процессов относительно банка конкурентов, у каждого ли указаны роли-участницы, "
            "нет ли дублей.\n" + bank_summary(ctx) + "\nЗамечания оставь карточками «замечание».")


def _p_scenarios_clarify(ctx, step, target) -> str:
    return ("Найди неточности: сценарии с ролью, которой нет среди карточек «роль», сценарии без ролей, роли без сценариев. "
            "По каждой неточности задай человеку вопрос через ask с вариантами решения (по одному за ход).")


def _p_select_suggest(ctx, step, target) -> str:
    cards = "\n".join("  " + _scenario_block(ctx, s) for s in _cards(ctx.store, SCENARIO)) or "  сценариев нет"
    return ("Предложи, какие сценарии автоматизировать. Сценарии:\n" + cards + f"\n{bank_summary(ctx)}\n"
            "Задай один вопрос через ask: «Какие сценарии автоматизируем?» — 2–4 осмысленные подборки сценариев (по важности "
            "и по тому, что автоматизируют конкуренты), рекомендуй. После ответа у выбранных сценариев обнови body "
            f"(update_card), оставив текст и поставив отдельной последней строкой «{AUTOMATE_FIELD}: да», у остальных — «{AUTOMATE_FIELD}: нет»; "
            "по возможности добавь строки «Важность: …» (низкая, средняя, высокая, критическая) и «Частота: …» (редко, иногда, часто, "
            "постоянно). Принятые карточки, созданные человеком, не меняй: перечисли их в finish, человек отметит их сам.")


def _p_select_review(ctx, step, target) -> str:
    return ("Проверь выбор сценариев для автоматизации: не выбраны ли редкие и малозначимые, не пропущены ли частые и дорогие. "
            "Замечания оставь карточками «замечание» (parent — сценарий).")


def _p_asis_suggest(ctx, step, target) -> str:
    return ("Опиши, как сценарий проходит СЕЙЧАС, от начала до конца, по шагам.\n" + _targets_text(ctx, target) + "\n" + bank_summary(ctx)
            + f"\nДля каждого сценария создай карточку вида «{ASIS}» (status proposed, parent — карточка сценария, title «Как есть: "
            "<сценарий>», body — итоговая проблема сценария в 1–2 строках) с диаграммой последовательности. "
            + DIAGRAM_HELP + " Боли ставь только там, где они реально есть. Неточности (шаг, который выполняет роль не из сценария) "
            "не домысливай: задай человеку вопрос через ask с вариантами (добавить роль, заменить роль, убрать шаг). Закончи finish.")


def _p_asis_generate(ctx, step, target) -> str:
    return ("Построй диаграмму «как есть» из текста. Текст шагов возьми из тела карточки «как есть» (graph_read card) и из сообщений "
            "человека; строки вида «Кто → Кому: что» превращай в шаги по порядку. " + DIAGRAM_HELP + "\n" + _targets_text(ctx, target)
            + "\nЕсли в тексте участвует роль, которой не было среди ролей сценария, спроси через ask (добавить роль, заменить, убрать шаг), "
            "а не добавляй молча. Обнови карточку (update_card с attrs) и закончи finish.")


def _p_asis_review(ctx, step, target) -> str:
    return ("Проверь диаграммы «как есть»: порядок шагов логичен, участники — роли сценария, боли реальны и у них есть частота и цена, "
            "ничего не пропущено относительно процессов конкурентов.\n" + bank_summary(ctx)
            + "\nЗамечания оставь карточками «замечание» (parent — карточка «как есть»).")


def _p_asis_clarify(ctx, step, target) -> str:
    return ("Найди неточности в диаграммах «как есть»: участник, которого нет среди ролей сценария, шаг без исполнителя, разрыв в "
            "последовательности. По каждой задай человеку вопрос через ask с вариантами решения (по одному за ход).")


def _p_solution_suggest(ctx, step, target) -> str:
    return ("Предложи вид автоматизации и опиши, как будет.\n" + _targets_text(ctx, target) + "\n"
            + f"Для каждого сценария задай через ask вопрос «Что автоматизируем?» с 2–4 вариантами из видов: {', '.join(AUTOMATION_KINDS)} "
            "(можно по отдельным шагам, не только роль целиком; можно несколько), рекомендуй. После ответа создай карточку вида "
            f"«{SOLUTION}» (status proposed, parent — карточка сценария, body — что и зачем автоматизируем, attrs.solution = "
            '{"items":[{"step":<номер шага или null>,"kind":"<вид>","note":"<коротко>"}]}), затем карточку «' + TOBE + "» "
            "(parent — карточка сценария, title «Как будет: <сценарий>») с диаграммой: те же участники плюс «Система», «БД» или "
            "внешний ресурс; отличия от «как есть» отмечай в mark. " + DIAGRAM_HELP + " Закончи finish.")


def _p_solution_generate(ctx, step, target) -> str:
    return ("Построй диаграмму «как будет» по карточке «решение» и диаграмме «как есть» (graph_read card). Добавь участников «Система», "
            "«БД» или внешний ресурс там, где это нужно, и отметь отличия. " + DIAGRAM_HELP + "\n" + _targets_text(ctx, target)
            + "\nОбнови или создай карточку «как будет» (parent — сценарий) и закончи finish.")


def _p_solution_review(ctx, step, target) -> str:
    return ("Проверь решение: покрывает ли «как будет» боли из «как есть», нет ли новых разрывов, понятна ли роль «Системы» и «БД». "
            "Замечания оставь карточками «замечание» (parent — карточка решения или «как будет»).")


def _p_solution_clarify(ctx, step, target) -> str:
    return ("Найди неточности между «как есть», решением и «как будет»: боль без решения, шаг решения без шага в диаграмме, участник "
            "без роли. По каждой задай человеку вопрос через ask с вариантами (по одному за ход).")


# ---------------------------------------------------------------------- вводные, проверки, итог

def assess(store) -> dict:
    """Что осталось: у каких выбранных сценариев нет «как есть», «решения», «как будет»; сломанные диаграммы."""
    gaps, broken = [], []
    for scenario in selected_scenarios(store):
        for kind, label in ((ASIS, "«как есть»"), (SOLUTION, "«решение»"), (TOBE, "«как будет»")):
            if not _children(store, scenario["id"], kind):
                gaps.append(f"у сценария {scenario['id']} «{scenario['title']}» нет карточки {label}")
    for kind in (ASIS, TOBE):
        for card in _cards(store, kind):
            found = diagrams.problems((card.get("attrs") or {}).get("diagram"))
            if found:
                broken.append(f"{card['id']} «{card['title']}»: " + "; ".join(found))
    return {"gaps": gaps, "broken": broken, "clean": not (gaps or broken)}


def prepare(ctx) -> str:
    store = ctx.store
    lines = ["Этап «Процессы»."]
    sphere = _cards(store, SPHERE)
    lines.append("Сферы: " + ("; ".join(_sphere_line(c) for c in sphere) if sphere else "ещё не написаны (их пишет человек)."))
    lines.append(f"Ролей: {len(_cards(store, ROLE))}; сценариев: {len(_cards(store, SCENARIO))}, "
                 f"выбрано для автоматизации: {len(selected_scenarios(store))}; «как есть»: {len(_cards(store, ASIS))}; "
                 f"решений: {len(_cards(store, SOLUTION))}; «как будет»: {len(_cards(store, TOBE))}.")
    info = assess(store)
    if info["broken"]:
        lines.append("Сломанные диаграммы (исправь):")
        lines.extend("  - " + item for item in info["broken"])
    return "\n".join(lines)


def conclude(ctx, outcome: str) -> str:
    return "готово" if assess(ctx.store)["clean"] and selected_scenarios(ctx.store) else "есть_пробелы"


def auto_note(ctx, targets: list[dict], limits: dict | None) -> str:
    names = "; ".join(_sphere_line(c) for c in targets)
    cap = (f"не больше {limits['roles']} ролей и {limits['scenarios']} сценариев на сферу" if limits
           else "число ролей и сценариев не ограничено, но не делай работу ради работы: только то, что подтверждено банком "
                "конкурентов или текстом человека, и только адекватное")
    return ("АВТОМАТИЧЕСКИЙ РЕЖИМ. Человека рядом нет. Работай только со сферами: " + names + ". "
            f"Пройди шаги по порядку: роли, сценарии, выбор для автоматизации, «как есть», решение и «как будет» ({cap}). "
            "Если выбор неочевиден и от него многое зависит (какие роли, сценарии, что автоматизировать, вид автоматизации), задай "
            "ровно один вопрос через ask: на него ответит совет из трёх агентов, и обоснование попадёт в карточку через "
            '"decision":"last". Очевидное решай сам. Всё создаётся со статусом proposed, ничего не утверждай. Каждый выбранный вариант — '
            "своя карточка. Закончив шаг, вызови finish.")


def manifest() -> dict:
    return {
        "id": "processes",
        "name": "Процессы",
        "description": "Роли, сценарии, выбор для автоматизации, как процесс идёт сейчас (с болями) и как будет после автоматизации — "
                       "с диаграммами.",
        "kind": "agent",
        "order": 5,
        "model_level": "сильная",
        "outcomes": ["готово", "есть_пробелы"],
        "skill": _read("skill.md"),
        "static": str(HERE / "static"),
        "banks": {"reads": ["competitors", "analysis"], "writes": []},
        "prepare": prepare,
        "conclude": conclude,
        "stage": {
            "roots": SPHERE,
            "hidden_edges": [PARTICIPATES],
            "kinds": [
                {"kind": SPHERE, "title": "Сфера", "color": "#e8833a"},
                {"kind": ROLE, "title": "Роль", "color": "#5aa0ff"},
                {"kind": SCENARIO, "title": "Сценарий", "color": "#bb9af7",
                 "chips": {"edge": PARTICIPATES, "label": "Роли"},
                 "fields": [{"name": AUTOMATE_FIELD, "values": list(AUTOMATE_VALUES)},
                            {"name": "Важность", "values": list(IMPORTANCE)},
                            {"name": "Частота", "values": list(FREQUENCY)}]},
                {"kind": ASIS, "title": "Как есть", "color": "#ff8fa3", "renderer": {"module": "sequence.js", "name": "sequence"}},
                {"kind": SOLUTION, "title": "Решение", "color": "#7fc97f"},
                {"kind": TOBE, "title": "Как будет", "color": "#6fd3c7",
                 "renderer": {"module": "sequence.js", "name": "sequence", "tobe": True}},
            ],
            "steps": [
                {"id": "roles", "title": "Роли", "question": "Выберите роли участников процессов этой сферы или напишите свои.",
                 "produces": [ROLE], "needs": [], "target_kind": SPHERE,
                 "prompts": {"suggest": _p_roles_suggest, "review": _p_roles_review, "clarify": _p_roles_clarify}},
                {"id": "scenarios", "title": "Сценарии",
                 "question": "Опишите бизнес-процессы для этих ролей или выберите из предложений.",
                 "produces": [SCENARIO], "needs": ["roles"], "target_kind": SPHERE,
                 "visual": {"module": "usecase.js", "name": "usecase", "title": "Диаграмма вариантов использования"},
                 "prompts": {"suggest": _p_scenarios_suggest, "review": _p_scenarios_review, "clarify": _p_scenarios_clarify}},
                {"id": "select", "title": "Выбор сценариев", "question": "Выберите сценарии, подлежащие автоматизации разрабатываемым ПО.",
                 "produces": [], "needs": ["scenarios"], "target_kind": SPHERE,
                 "requires": {"kind": SCENARIO, "field": AUTOMATE_FIELD, "value": "да", "min": 1},
                 "prompts": {"suggest": _p_select_suggest, "review": _p_select_review}},
                {"id": "asis", "title": "Как есть", "question": "Опишите, как сейчас проходит сценарий от начала до конца, по шагам.",
                 "produces": [ASIS], "needs": ["select"], "target_kind": SCENARIO,
                 "prompts": {"suggest": _p_asis_suggest, "generate": _p_asis_generate, "review": _p_asis_review,
                             "clarify": _p_asis_clarify}},
                {"id": "solution", "title": "Решение", "question": "Выберите или опишите желаемый вид автоматизации.",
                 "produces": [SOLUTION, TOBE], "needs": ["asis"], "target_kind": SCENARIO,
                 "prompts": {"suggest": _p_solution_suggest, "generate": _p_solution_generate, "review": _p_solution_review,
                             "clarify": _p_solution_clarify}},
            ],
            "auto": {"targets": SPHERE, "note": auto_note,
                     "limits": [{"key": "roles", "name": "Ролей на сферу, не больше", "default": DEFAULT_LIMITS["roles"]},
                                {"key": "scenarios", "name": "Сценариев на сферу, не больше", "default": DEFAULT_LIMITS["scenarios"]}]},
        },
    }
