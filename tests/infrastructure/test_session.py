import pytest
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import sessionmaker

from infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


@pytest.mark.parametrize("exit_path", ["normal", "exception", "generator_close"])
def test_unit_of_work_ends_session_on_every_exit(exit_path):
    # Same exit paths as the former get_db dependency, now owned by the UoW.
    # No binding: this checks local session lifecycle, not persisted rollback.
    factory = sessionmaker(close_resets_only=False)

    def dependency():
        with SqlAlchemyUnitOfWork(factory) as uow:
            session = uow._session
            yield session

    generator = dependency()
    session = next(generator)
    transaction = session.begin()

    if exit_path == "normal":
        with pytest.raises(StopIteration):
            next(generator)
    elif exit_path == "exception":
        error = RuntimeError("synthetic request failure")
        with pytest.raises(RuntimeError) as caught:
            generator.throw(error)
        assert caught.value is error
    else:
        generator.close()

    assert not transaction.is_active
    assert not session.in_transaction()
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        session.begin()


def test_unit_of_work_requires_an_active_context_and_cannot_be_reused():
    uow = SqlAlchemyUnitOfWork(sessionmaker())
    with pytest.raises(RuntimeError, match="active unit of work"):
        uow.commit()
    with uow:
        pass
    with pytest.raises(RuntimeError, match="active unit of work"):
        uow.commit()
    with pytest.raises(RuntimeError, match="cannot be reused"), uow:
        pass
