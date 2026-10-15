from pathlib import Path
from zara.guardian.action import Level, Action, Decision


def test_level_ordering():
    assert Level.BLOCKED > Level.CRITICAL > Level.CONFIRM > Level.SAFE
    assert max(Level.CONFIRM, Level.BLOCKED) is Level.BLOCKED


def test_decision_flags():
    a = Action(kind="shell", description="borrar", command="rm x",
               paths=(Path("/x"),))
    d = Decision(action=a, level=Level.CRITICAL, reason="sudo",
                 matched_rule="r1", needs_backup=True)
    assert d.requires_confirmation is True
    assert d.is_blocked is False

    d2 = Decision(action=a, level=Level.SAFE, reason="ok",
                  matched_rule=None, needs_backup=False)
    assert d2.requires_confirmation is False
