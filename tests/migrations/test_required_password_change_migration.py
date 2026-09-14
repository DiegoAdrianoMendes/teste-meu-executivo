from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic/versions/op7i0e3g2b86_add_required_password_change.py"


def test_required_password_change_is_a_forward_only_extension_of_current_head() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "op7i0e3g2b86"' in source
    assert 'down_revision: str | Sequence[str] | None = "no6h9d2f1a75"' in source
    assert '"password_change_required"' in source
    assert '"temporary_password_issued_at"' in source
    assert "ck_users_required_password_change_has_temporary_password" in source
    assert "uq_users_single_platform_owner" not in source
