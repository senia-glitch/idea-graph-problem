from importlib import metadata

import pytest

from idea_graph_core import HUMAN, Store
from idea_graph_problem import (DOMAIN, IMPORTANCE, LAYERS, PROBLEM, RESULT, SCENARIO, assess, conclude, field_value,
                                manifest, prepare, tree)

AGENT = "package:problem"


class Ctx:
    def __init__(self, store):
        self.store = store
        self.state = {}

    def save_state(self):
        pass


@pytest.fixture
def store(tmp_path):
    return Store.create(tmp_path, "Сервис записи к врачу")


def add(store, kind, title, parent, body="", author=HUMAN, status=None):
    op = {"op": "add_card", "kind": kind, "title": title, "body": body, "parent": parent}
    if status:
        op["status"] = status
    store.apply([op], author)
    return max(store.cards, key=lambda c: int(c[1:]))


def texts(store):
    return [g["text"] for g in assess(store)["gaps"]]


def test_manifest_is_built_from_layers():
    m = manifest()
    assert m["id"] == "problem" and m["kind"] == "agent" and m["asks"] is True
    assert m["outcomes"] == ["пробелов_нет", "есть_пробелы"]
    skill = m["skill"]
    assert "{{" not in skill
    for layer in LAYERS:
        assert layer["question"] in skill and layer["sufficient"] in skill
    for phrase in ("Важность", "multiple: true", "один раз", "не больше 5", "всегда пишет человек"):
        assert phrase in skill


def test_entry_point_is_declared():
    assert "problem" in [p.name for p in metadata.entry_points(group="idea_graph.packages")]


def test_no_domain_is_the_first_gap(store):
    info = assess(store)
    assert not info["clean"] and "домен" in info["gaps"][0]["text"].lower()
    assert info["gaps"][0]["layer"] == DOMAIN and info["gaps"][0]["card"] is None


def test_gaps_walk_down_the_tree(store):
    domain = add(store, DOMAIN, "Пациенты частных клиник старше 30", "c1")
    assert any("нет ни одного принятого «сценарий»" in t for t in texts(store))
    scenario = add(store, SCENARIO, "Утром, когда нужно записаться на приём", domain, author=AGENT, status="accepted")
    assert any("нет ни одного принятого «проблема»" in t for t in texts(store))
    problem = add(store, PROBLEM, "Не дозвониться в регистратуру", scenario, author=AGENT, status="accepted")
    t = texts(store)
    assert any("не указано поле «Важность»" in x for x in t) and any("«результат»" in x for x in t)
    store.apply([{"op": "update_card", "id": problem, "body": "Звонят по 20 минут.\nВажность: высокая"}], HUMAN)
    assert not any("Важность" in x for x in texts(store))
    add(store, RESULT, "Записаться без звонка", problem, author=AGENT, status="accepted")
    assert assess(store)["clean"] and assess(store)["gaps"] == []


def test_several_results_and_branches(store):
    domain = add(store, DOMAIN, "Пациенты", "c1")
    s1 = add(store, SCENARIO, "Сценарий 1", domain, author=AGENT, status="accepted")
    s2 = add(store, SCENARIO, "Сценарий 2", domain, author=AGENT, status="accepted")
    p = add(store, PROBLEM, "Проблема", s1, body="Важность: средняя", author=AGENT, status="accepted")
    add(store, RESULT, "Результат 1", p, author=AGENT, status="accepted")
    add(store, RESULT, "Результат 2", p, author=AGENT, status="accepted")
    t = texts(store)
    assert len(t) == 1 and "Сценарий 2" in t[0]               # у второго сценария нет проблем
    assert "Сценарий 2" in " ".join(tree(store))


def test_proposed_cards_are_pending_not_counted(store):
    domain = add(store, DOMAIN, "Пациенты", "c1")
    add(store, SCENARIO, "Черновик сценария", domain, author=AGENT)         # предложен агентом
    info = assess(store)
    assert any("Черновик сценария" in p for p in info["pending"])
    assert any("нет ни одного принятого «сценарий»" in g["text"] for g in info["gaps"])


def test_domain_must_be_written_by_human(store):
    add(store, DOMAIN, "Все люди", "c1", author=AGENT, status="accepted")
    assert any("не человек" in m for m in assess(store)["misplaced"])


def test_card_on_wrong_place(store):
    add(store, SCENARIO, "Сценарий под корнем", "c1", author=AGENT, status="accepted")
    assert any("должна быть потомком карточки вида «домен»" in m for m in assess(store)["misplaced"])


def test_importance_field():
    field = LAYERS[2]["field"]
    assert field_value({"body": "Описание\nВажность: Критическая."}, field) == "критическая"
    assert field_value({"body": "Важность: очень высокая"}, field) is None
    assert field_value({"body": "Важность: низкая\nещё текст\nВажность: высокая"}, field) == "высокая"
    assert field_value({"body": ""}, field) is None
    assert set(IMPORTANCE) == {"низкая", "средняя", "высокая", "критическая"}


def test_prepare_and_conclude(store):
    ctx = Ctx(store)
    assert "доменов пока нет" in prepare(ctx) and conclude(ctx, "пробелов_нет") == "есть_пробелы"
    domain = add(store, DOMAIN, "Пациенты", "c1")
    s = add(store, SCENARIO, "С", domain, author=AGENT, status="accepted")
    p = add(store, PROBLEM, "П", s, body="Важность: низкая", author=AGENT, status="accepted")
    add(store, RESULT, "Р", p, author=AGENT, status="accepted")
    assert "Незакрытых мест нет" in prepare(ctx)
    assert conclude(ctx, "есть_пробелы") == "пробелов_нет" and ctx.state["gaps"] == []
