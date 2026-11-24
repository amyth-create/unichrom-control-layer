import pytest

from wingpc_agent.errors import ActionDenied, RecipeError
from wingpc_agent.recipes import RecipeBook
from wingpc_agent.safety import Gate, Policy


def test_default_recipes_load_and_are_unverified():
    book = RecipeBook.load("recipes/wingpc_default.yaml")
    names = set(book.recipes)
    assert {"ascii_export_raw_data", "sequence_start", "sequence_stop"} <= names
    assert all(not r.verified for r in book.recipes.values())
    assert book.get("sequence_stop").level == "hazardous"


def test_recipe_render_params():
    book = RecipeBook.load("recipes/wingpc_default.yaml")
    steps = book.get("ascii_export_raw_data").render(path=r"C:\x\out.txt")
    typed = [s for s in steps if s["kind"] == "type"][0]
    assert typed["text"] == r"C:\x\out.txt"
    with pytest.raises(RecipeError):
        book.get("ascii_export_raw_data").render()


def test_gate_levels(tmp_path):
    pol = Policy.load("recipes/policy_default.yaml")
    gate = Gate(pol, tmp_path / "audit.jsonl", interactive_confirm=False)
    gate.check("chrom_live_snapshot", {})            # observe
    gate.check("ui_click", {"path": "x"})            # control
    with pytest.raises(ActionDenied):
        gate.check("recipe_sequence_stop", {})       # hazardous w/o token
    with pytest.raises(ActionDenied):
        gate.check("process_kill", {})               # denied
    tok = gate.issue_token("recipe_sequence_stop", "test")
    gate.check("recipe_sequence_stop", {}, token=tok)  # ok once
    with pytest.raises(ActionDenied):
        gate.check("recipe_sequence_stop", {}, token=tok)  # token consumed
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) >= 6
