"""Persistence boundary regression tests for the holdings account HTTP cases."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, Stock  # noqa: F401 - registers the relationship models


def test_delete_position_logs_relationship_names_before_commit():
    """After deleting a position the response still completes, and logging mustn't touch relationship objects detached from the session."""
    from src.modules.portfolio.api.accounts import delete_position

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    account = Account(name="Test account")
    stock = Stock(symbol="INFY", name="Infosys", market="IN")
    session.add_all([account, stock])
    session.flush()
    position = Position(
        account_id=account.id,
        stock_id=stock.id,
        cost_price=1500,
        quantity=100,
    )
    session.add(position)
    session.commit()

    result = delete_position(position.id, session)

    assert result == {"success": True}
    assert session.get(Position, position.id) is None
    session.close()
    engine.dispose()


def test_delete_position_does_not_read_detached_relationships_after_delete():
    """When relationship objects are unusable after the delete commit, the delete endpoint still returns normally."""
    from src.modules.portfolio.api.accounts import delete_position

    class Relation:
        def __init__(self, name: str, owner: "FakePosition"):
            self.name = name
            self._owner = owner

        def __getattribute__(self, attribute: str):
            if attribute == "name" and object.__getattribute__(self, "_owner").detached:
                raise AssertionError("lazy-loaded relationships mustn't be accessed after the delete commit")
            return object.__getattribute__(self, attribute)

    class FakePosition:
        id = 7

        def __init__(self):
            self.detached = False
            self.account = Relation("Test account", self)
            self.stock = Relation("Infosys", self)

    class FakeQuery:
        def __init__(self, position):
            self.position = position

        def filter(self, _condition):
            return self

        def first(self):
            return self.position

    class FakeSession:
        def __init__(self, position):
            self.position = position

        def query(self, _model):
            return FakeQuery(self.position)

        def delete(self, position):
            position.detached = True

        def commit(self):
            return None

    position = FakePosition()

    assert delete_position(position.id, FakeSession(position)) == {"success": True}
