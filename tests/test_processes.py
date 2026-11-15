"""Этап «Процессы»: декларация, диаграммы, промпты, проверки и работа вместе с движком."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from idea_graph_core import HUMAN, Store
from idea_graph_engine import Adapters, Project, Registry, Settings
from idea_graph_engine.agents import Adapter, TurnResult
from idea_graph_engine.runtime import EngineError
from idea_graph_processes import assess, bank_summary, conclude, diagrams, manifest, prepare, selected_scenarios

STATIC = Path(__file__).resolve().parents[1] / "src" / "idea_graph_processes" / "static"


class Ctx:
    def __init__(self, store):
        self.store = store
        self.bank = store.bank


@pytest.fixture
def store(tmp_path):
    return Store.create(tmp_path / "p", "Проект")


def add(store, kind, title, parent=None, body="", status="accepted", attrs=None, links=None):
    op = {"op": "add_card", "kind": kind, "title": title, "body": body, "status": status}
    if parent:
        op["parent"] = parent
    if attrs:
        op["attrs"] = attrs
    if links:
        op["links"] = links
    return store.apply([op], HUMAN)["ids"][0]


GOOD = {"type": "sequence", "participants": ["Пациент", "Регистратура"],
        "steps": [{"from": "Пациент", "to": "Регистратура", "text": "Звонит", "pain": {"what": "Не дозвониться", "often": "часто",
                                                                                      "cost": "20 минут", "importance": "высокая"}},
                  {"from": "Регистратура", "to": "Пациент", "text": "Называет время"}]}


# ---------------------------------------------------------------------- декларация

def test_manifest_declares_steps_kinds_gates_and_static():
    m = manifest()
    stage = m["stage"]
    assert [s["id"] for s in stage["steps"]] == ["roles", "scenarios", "select", "asis", "solution"]
    assert [s["needs"] for s in stage["steps"]] == [[], ["roles"], ["scenarios"], ["select"], ["asis"]]
    assert stage["steps"][2]["requires"] == {"kind": "сценарий", "field": "Автоматизировать", "value": "да", "min": 1}
    assert stage["steps"][3]["target_kind"] == "сценарий" and stage["steps"][1]["visual"]["module"] == "usecase.js"
    kinds = {k["kind"]: k for k in stage["kinds"]}
    assert set(kinds) == {"сфера", "роль", "сценарий", "как есть", "решение", "как будет"}
    assert kinds["сценарий"]["chips"] == {"edge": "участвует", "label": "Роли"} and stage["hidden_edges"] == ["участвует"]
    assert [f["name"] for f in kinds["сценарий"]["fields"]] == ["Автоматизировать", "Важность", "Частота"]
    assert kinds["как будет"]["renderer"]["tobe"] is True and "tobe" not in kinds["как есть"]["renderer"]
    assert (Path(m["static"]) / "sequence.js").is_file() and (Path(m["static"]) / "usecase.js").is_file()
    assert m["banks"] == {"reads": ["competitors", "analysis"], "writes": []}
    assert "Диаграмма последовательности" in m["skill"] and stage["auto"]["limits"][0]["key"] == "roles"


# ---------------------------------------------------------------------- диаграммы

def test_normalize_problems_and_text_roundtrip():
    clean = diagrams.normalize({"participants": ["А"], "steps": [
        {"from": "А", "to": "Б", "text": " Делает   дело ", "mark": "auto", "pain": {"what": "долго", "importance": "ужас"}},
        {"from": "А", "to": "Б", "text": ""}, "не шаг"]})
    assert clean["participants"] == ["А", "Б"] and len(clean["steps"]) == 1
    assert clean["steps"][0]["text"] == "Делает дело" and clean["steps"][0]["pain"]["importance"] == "" and clean["steps"][0]["mark"] == "auto"
    assert diagrams.problems(GOOD) == [] and "в диаграмме нет шагов" in diagrams.problems({"participants": ["А"]})
    assert any("не объявлен" in p for p in diagrams.problems({"participants": ["А"], "steps": [{"from": "А", "to": "Б", "text": "x"}]}))
    assert diagrams.problems("не объект") == ["диаграмма должна быть объектом"]
    with pytest.raises(ValueError):
        diagrams.normalize({"participants": [f"р{i}" for i in range(13)], "steps": []})
    text = diagrams.to_text(GOOD)
    assert text.splitlines()[0] == "1. Пациент → Регистратура: Звонит [боль: Не дозвониться | часто | 20 минут | высокая]"
    assert diagrams.parse_text(text) == diagrams.normalize(GOOD)


def test_parse_text_formats_marks_and_junk():
    parsed = diagrams.parse_text("""
        1. + Система -> БД: сохраняет запись
        ~ Пациент => Система: подтверждает
        * Система → Пациент: напоминает [боль: забывают | иногда | | средняя]
        - Регистратура → Пациент: звонит
        мусор без стрелки
        Пациент → Врач
    """)
    assert [(s["from"], s["to"], s["mark"]) for s in parsed["steps"]] == [
        ("Система", "БД", "new"), ("Пациент", "Система", "changed"), ("Система", "Пациент", "auto"), ("Регистратура", "Пациент", "removed")]
    assert parsed["steps"][2]["pain"] == {"what": "забывают", "often": "иногда", "cost": "", "importance": "средняя"}
    assert parsed["participants"] == ["Система", "БД", "Пациент", "Регистратура"]


def test_mermaid_export():
    text = diagrams.to_mermaid({**GOOD, "steps": GOOD["steps"] + [{"from": "Пациент", "to": "Регистратура", "text": "лишний; шаг", "mark": "removed"}]})
    lines = [line.strip() for line in text.splitlines()]
    assert lines[0] == "sequenceDiagram" and "participant P1 as Пациент" in lines and "P1->>P2: Звонит" in lines
    assert "Note over P2: боль: Не дозвониться" in lines and "P1->>P2: (убран) лишний, шаг" in lines


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")
def test_javascript_parser_matches_python(tmp_path):
    sample = ("1. + Система -> БД: сохраняет запись\n~ Пациент => Система: подтверждает\n"
              "* Система → Пациент: напоминает [боль: забывают | иногда | | средняя]\nмусор\n- Регистратура → Пациент: звонит")
    script = tmp_path / "run.mjs"
    script.write_text(f"import {{parseText, toText, normalize}} from {json.dumps((STATIC / 'sequence.js').as_uri())};\n"
                      f"const d = parseText({json.dumps(sample)});\nconsole.log(JSON.stringify({{d, t: toText(d), n: normalize({json.dumps(GOOD)})}}));\n",
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    expected = diagrams.parse_text(sample)
    assert out["d"]["participants"] == expected["participants"]
    assert [(s["from"], s["to"], s["text"], s["mark"], s.get("pain")) for s in out["d"]["steps"]] == \
        [(s["from"], s["to"], s["text"], s["mark"], s.get("pain")) for s in expected["steps"]]
    assert out["t"] == diagrams.to_text(expected) and out["n"] == diagrams.normalize(GOOD)


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")
def test_javascript_renders_svg_and_usecase(tmp_path):
    graph = {"cards": [{"id": "c1", "kind": "роль", "title": "Врач", "body": ""}, {"id": "c2", "kind": "сценарий", "title": "Приём",
                                                                                     "body": "Автоматизировать: да"}],
             "edges": [{"id": "e1", "from": "c2", "to": "c1", "kind": "участвует"}]}
    script = tmp_path / "run.mjs"
    script.write_text(f"import * as seq from {json.dumps((STATIC / 'sequence.js').as_uri())};\n"
                      f"import * as uc from {json.dumps((STATIC / 'usecase.js').as_uri())};\n"
                      "const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');\n"
                      f"const a = seq.svg({json.dumps(GOOD)}, {{esc}});\nconst n = seq.node({{attrs: {{diagram: {json.dumps(GOOD)}}}}}, {{esc}});\n"
                      f"const b = uc.svg({json.dumps(graph)}, {{esc}});\nconsole.log(JSON.stringify({{a, n, b, e: seq.svg({{}}, {{esc}})}}));\n", encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert out["a"].startswith("<svg") and "Регистратура" in out["a"] and "seq-pain" in out["a"] and out["a"].count("<line") >= 4
    assert "шагов: 2" in out["n"] and "болей: <b" in out["n"] and "uc-link" in out["b"] and "chosen" in out["b"] and "Врач" in out["b"]
    assert "пустая" in out["e"]


# ---------------------------------------------------------------------- банки, промпты, проверки

def test_bank_summary_counts_competitors(store):
    ctx = Ctx(store)
    assert "Банк analysis пуст" in bank_summary(ctx)
    bank = store.bank("analysis")
    bank.put("analysis", {"roles": [{"name": "Пациент"}, {"name": "Врач"}], "processes": [{"name": "Запись на приём"}]}, "package:recon", key="a")
    bank.put("analysis", {"roles": [{"name": "Пациент"}], "processes": [{"name": "Запись на приём"}, "Оплата"]}, "package:recon", key="b")
    text = bank_summary(ctx)
    assert "записей 2" in text and "Пациент (2)" in text and "Врач (1)" in text and "Запись на приём (2)" in text and "Оплата (1)" in text


def test_assess_prepare_conclude(store):
    sphere = add(store, "сфера", "Клиники", body="Запись")
    scenario = add(store, "сценарий", "Пациент записывается на приём", sphere, body="Описание\nАвтоматизировать: да")
    add(store, "сценарий", "Оплата", sphere, body="Автоматизировать: нет")
    ctx = Ctx(store)
    assert [c["id"] for c in selected_scenarios(store)] == [scenario]
    info = assess(store)
    assert len(info["gaps"]) == 3 and info["broken"] == [] and conclude(ctx, "готово") == "есть_пробелы"
    asis = add(store, "как есть", "Как есть: запись", scenario, attrs={"diagram": GOOD})
    add(store, "решение", "Онлайн-запись", scenario)
    add(store, "как будет", "Как будет: запись", scenario, attrs={"diagram": GOOD})
    assert assess(store)["clean"] and conclude(ctx, "готово") == "готово"
    store.apply([{"op": "update_card", "id": asis, "attrs": {"diagram": {"participants": [], "steps": []}}}], HUMAN)
    assert "в диаграмме нет шагов" in assess(store)["broken"][0]
    text = prepare(ctx)
    assert "Этап «Процессы»" in text and "выбрано для автоматизации: 1" in text and "Сломанные диаграммы" in text


# ---------------------------------------------------------------------- вместе с движком

class Script(Adapter):
    name = "script"
    label = "Сценарий"

    def __init__(self):
        self.notes = []

    def found(self):
        return "script"

    def run(self, request):
        project, token = request.extra["project"], request.extra["token"]
        self.notes.append((request.model, request.note))
        call = lambda tool, **a: project.tool_call(token, tool, a)  # noqa: E731
        if request.model.startswith("a"):
            call("decide", choice="choice", options=[1], rationale="Вариант 1 самый частый и дешёвый")
        elif "Шаг этапа «Роли»" in request.note and "Ответ на твой вопрос" not in request.note:
            call("ask", question="Какие роли?", options=["Пациент", "Врач"], recommended=[1])
        elif "Ответ на твой вопрос" in request.note:
            call("graph_apply", ops=[{"op": "add_card", "kind": "роль", "title": "Пациент", "parent": "c1", "decision": "last"}])
            call("finish", outcome="готово", summary="роль добавлена")
        else:
            call("finish", outcome="готово", summary="ок")
        return TurnResult(session_id="s", text="", cost=0.0)


@pytest.fixture
def project(tmp_path):
    store = Store.create(tmp_path / "p", "Проект")
    store.apply([{"op": "add_card", "kind": "сфера", "title": "Клиники"}], HUMAN)
    settings = Settings(tmp_path / "s.json")
    settings.data["default_slot"] = {"agent": "script", "model": "main", "effort": ""}
    settings.data["advisers"] = [{"agent": "script", "model": f"a{i}", "effort": ""} for i in (1, 2, 3)]
    settings.data["reviewer"] = {"agent": "script", "model": "rev", "effort": ""}
    registry = Registry(discover=False)
    registry.register(manifest())
    adapters = Adapters()
    script = Script()
    adapters.add(script)
    built = Project(tmp_path / "p", settings, registry, adapters)
    built.PROPAGATE_DELAY = 0.05
    built.script = script
    return built


def wait(project, statuses=("finished", "error", "asking"), timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        run = project.runs.get("processes")
        if run is not None and run.status in statuses:
            return run
        time.sleep(0.01)
    raise AssertionError("не дождались")


def test_roles_step_dialog_human_decision_and_gate(project):
    project.start("processes", step="roles", action="suggest", target="c1")
    run = wait(project, ("asking",))
    note = project.script.notes[0][1]
    assert "Предложи роли участников процессов сферы c1 «Клиники»" in note and "Банк analysis пуст" in note
    project.answer("processes", run.question["id"], {"type": "choice", "indexes": [1]})
    wait(project, ("finished",))
    role = next(c for c in project.store.cards.values() if c["kind"] == "роль")
    assert role["decision"]["by"] == HUMAN and role["status"] == "proposed"
    with pytest.raises(EngineError) as info:
        project.approve_step("processes", "roles")
    assert info.value.code == "pending"
    project.approve_step("processes", "roles", accept_all=True)
    steps = {s["id"]: s for s in project.state()["stages"]["processes"]["steps"]}
    assert steps["roles"]["status"] == "approved" and steps["select"]["required"] == {"ok": False, "count": 0, "min": 1}
    with pytest.raises(EngineError) as info:
        project.approve_step("processes", "select")
    assert info.value.code == "empty_step"


def test_auto_mode_council_decides_with_rationale_on_the_card(project):
    project.start("processes", auto={"targets": ["c1"]})
    wait(project, ("finished", "error"))
    run = project.runs["processes"]
    assert run.status == "finished" and run.autorun["queue"] == [["c1", s] for s in ("roles", "scenarios", "select", "asis", "solution")]
    role = next(c for c in project.store.cards.values() if c["kind"] == "роль")
    decision = role["decision"]
    assert decision["by"] == "council" and decision["rationale"].startswith("Вариант 1 самый частый") and not decision["disputed"]
    joined = "\n".join(n for _, n in project.script.notes if _ == "main")
    assert "АВТОМАТИЧЕСКИЙ РЕЖИМ" in joined and "не больше 8 ролей и 6 сценариев" in joined
    assert "Сценариев, отмеченных «Автоматизировать: да», нет" in joined                  # как есть и решение: выбирать нечего


def test_asis_prompt_targets_one_scenario_or_all_selected(project):
    sphere = "c1"
    add(project.store, "роль", "Пациент", sphere)
    one = add(project.store, "сценарий", "Запись", sphere, body="Автоматизировать: да", links=[{"to": "c2", "kind": "участвует"}])
    add(project.store, "сценарий", "Оплата", sphere, body="Автоматизировать: нет")
    project.start("processes", step="asis", action="suggest", target=one)
    wait(project, ("finished",))
    note = project.script.notes[-1][1]
    assert "Работай с одним сценарием" in note and "c3 «Запись» (роли: Пациент)" in note and "attrs.diagram" in note
    project.start("processes", step="asis", action="generate")
    wait(project, ("finished",))
    note = project.script.notes[-1][1]
    assert "каждым сценарием, отмеченным" in note and "Запись" in note and "Оплата" not in note
    assert "роль, которой не было среди ролей сценария" in note
    project.start("processes", step="solution", action="suggest", target=one)
    wait(project, ("finished",))
    assert "убрать шаг" in project.script.notes[-1][1] and "Система" in project.script.notes[-1][1]


def test_changing_a_role_after_approval_proposes_edits_for_scenarios(project):
    role = add(project.store, "роль", "Врач", "c1")
    scenario = add(project.store, "сценарий", "Врач принимает пациента", "c1")
    project.approve_step("processes", "roles")
    project.human_ops([{"op": "update_card", "id": role, "title": "Терапевт"}])
    wait(project, ("finished",))
    run = project.runs["processes"]
    assert run.role == "proposer"
    note = project.script.notes[-1][1]
    assert "Человек изменил утверждённые карточки" in note and "Сценарии" in note and "Врач" in note
    steps = {s["id"]: s for s in project.state()["stages"]["processes"]["steps"]}
    assert steps["scenarios"]["stale"]["cards"] == [role] and steps["asis"]["stale"] is not None
    assert scenario in project.store.cards
