"""新的 orchestration repository：往返轉換與兩個資料庫層保證。

這些測試存在的理由是「還沒接上去的程式碼要先被驗證過」——3c-2 的 repository
會在 3c-3 變成部署路徑的承重結構，那時候再發現 CAS 寫錯就太晚了。
"""

import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models.orchestration import Deployment as DeploymentRow
from app.db.session import create_database_engine
from app.domain.orchestration.entities import Deployment
from app.domain.orchestration.exceptions import DuplicatePromotionError
from app.domain.orchestration.repositories import DeploymentFilters
from app.domain.orchestration.value_objects import DeploymentStatus, PublishStatus
from app.infrastructure.sqlite.orchestration.deployment_repository import (
    SqlAlchemyDeploymentRepository,
)
from tests.conftest import registered_component, seed_default_project

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def sessions(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'orch.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as setup:
        seed_default_project(setup)
    yield factory
    engine.dispose()


def make_deployment(session, *, build: int = 77, status=DeploymentStatus.WAITING) -> Deployment:
    component = registered_component(session, "backend")
    return Deployment(
        id=f"dep-{build}",
        project_id=component.project_id,
        component_id=component.id,
        component=component.key,
        drone_connection_id=component.project.drone_connection_id or "conn",
        drone_owner=component.drone_owner,
        drone_repository=component.drone_repo,
        source_build_number=build,
        commit_sha="a" * 40,
        branch="main",
        target="production",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def test_a_deployment_survives_the_round_trip(sessions) -> None:
    with sessions() as session:
        repository = SqlAlchemyDeploymentRepository(session)
        original = make_deployment(session)
        repository.add(original)
        session.commit()

        loaded = repository.find_by_id(original.id)

    assert loaded == original
    assert loaded.status is DeploymentStatus.WAITING
    assert loaded.publish_status is PublishStatus.NOT_PUBLISHED
    assert loaded.source_build_number == 77
    assert loaded.component == "backend"


def test_the_lifecycle_is_written_back_but_the_publish_columns_are_not(sessions) -> None:
    """兩條寫入路徑碰不相交的欄位，所以不會互相蓋掉。"""
    with sessions() as session:
        repository = SqlAlchemyDeploymentRepository(session)
        deployment = make_deployment(session)
        repository.add(deployment)
        session.commit()

        # 模擬 publish_service 那條還沒搬過來的路徑直接改發布欄位。
        row = session.get(DeploymentRow, deployment.id)
        row.publish_status = PublishStatus.PUBLISHED.value
        row.version = "v1.2.3"
        session.commit()

        deployment.claim_for_promotion(at=NOW)
        deployment.record_promotion(901, at=NOW)
        repository.save(deployment)
        session.commit()

        row = session.get(DeploymentRow, deployment.id)
        assert row.status == DeploymentStatus.DEPLOYING.value
        assert row.promotion_build_number == 901
        # entity 手上那份 publish_status 是舊的，但 save 沒有把它寫回去。
        assert row.publish_status == PublishStatus.PUBLISHED.value
        assert row.version == "v1.2.3"


def test_the_database_refuses_a_second_promotion_of_the_same_build(sessions) -> None:
    with sessions() as session:
        repository = SqlAlchemyDeploymentRepository(session)
        first = make_deployment(session)
        repository.add(first)
        session.commit()

        duplicate = make_deployment(session)
        duplicate._id = "dep-duplicate"

        with pytest.raises(DuplicatePromotionError):
            repository.add(duplicate)


def test_the_pre_flight_check_finds_the_occupied_slot(sessions) -> None:
    with sessions() as session:
        repository = SqlAlchemyDeploymentRepository(session)
        deployment = make_deployment(session)
        repository.add(deployment)
        session.commit()

        found = repository.find_active_promotion(
            drone_connection_id=deployment.drone_connection_id,
            drone_owner=deployment.drone_owner,
            drone_repository=deployment.drone_repository,
            source_build_number=deployment.source_build_number,
            target=deployment.target,
        )
        missing = repository.find_active_promotion(
            drone_connection_id=deployment.drone_connection_id,
            drone_owner=deployment.drone_owner,
            drone_repository=deployment.drone_repository,
            source_build_number=999,
            target=deployment.target,
        )

    assert found == deployment.id
    assert missing is None


def test_the_claim_is_won_by_exactly_one_caller(sessions) -> None:
    """entity 的認領只是記憶體裡的事；分勝負的是這句帶 expected 的 UPDATE。"""
    with sessions() as session:
        SqlAlchemyDeploymentRepository(session).add(make_deployment(session))
        session.commit()

    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def claim() -> None:
        with sessions() as session:
            repository = SqlAlchemyDeploymentRepository(session)
            deployment = repository.find_by_id("dep-77")
            deployment.claim_for_promotion(at=NOW)
            barrier.wait(timeout=10)
            won = repository.claim_for_promotion(deployment)
            session.commit()
            with lock:
                results.append(won)

    threads = [threading.Thread(target=claim) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [False, False, True]
    with sessions() as session:
        assert (
            SqlAlchemyDeploymentRepository(session).find_by_id("dep-77").status
            is DeploymentStatus.PROMOTING
        )


def test_searching_filters_and_counts(sessions) -> None:
    with sessions() as session:
        repository = SqlAlchemyDeploymentRepository(session)
        repository.add(make_deployment(session, build=1))
        repository.add(make_deployment(session, build=2, status=DeploymentStatus.SUCCESS))
        session.commit()

        waiting, total = repository.search(
            DeploymentFilters(status=DeploymentStatus.WAITING), limit=10, offset=0
        )
        everything, all_total = repository.search(DeploymentFilters(), limit=10, offset=0)

    assert [d.source_build_number for d in waiting] == [1]
    assert total == 1
    assert all_total == len(everything) == 2
