import uuid

from app.models import Base, uuid7


def test_uuid7_uses_rfc_9562_version() -> None:
    value = uuid7()

    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_required_tables_are_registered() -> None:
    assert {"payments", "outbox"} <= set(Base.metadata.tables)
