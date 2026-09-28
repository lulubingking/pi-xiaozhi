"""评分标准逻辑对象及不可覆盖的版本历史接口。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.utils import new_id, utc_now
from app.db.session import get_db
from app.models import Rubric, RubricExample, RubricPoint, RubricVersion, User
from app.schemas import RubricCreateRequest, RubricPatchRequest

router = APIRouter(prefix="/rubrics", tags=["rubrics"])


def score_text(value: Decimal | int | float | None) -> str | None:
    return f"{Decimal(value):.2f}" if value is not None else None


def get_owned_rubric(db: Session, user_id: str, rubric_id: str) -> Rubric:
    rubric = db.scalar(select(Rubric).where(Rubric.id == rubric_id, Rubric.owner_user_id == user_id))
    if not rubric:
        raise ApiError(404, "RUBRIC_NOT_FOUND", "评分标准不存在或当前账号无权访问。")
    return rubric


def latest_version(db: Session, rubric_id: str) -> RubricVersion | None:
    return db.scalar(select(RubricVersion).where(RubricVersion.rubric_id == rubric_id).order_by(RubricVersion.version_no.desc()).limit(1))


def _version_payload(version: RubricVersion, points: list[RubricPoint], examples: list[RubricExample]) -> dict:
    return {
        "id": version.id,
        "version_no": version.version_no,
        "total_score": score_text(version.total_score),
        "status": version.status,
        "source_version_id": version.source_version_id,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "points": [
            {"id": item.id, "label": item.label, "max_score": score_text(item.max_score), "sort_order": item.sort_order}
            for item in points
        ],
        "examples": [
            {"id": item.id, "rubric_point_id": item.rubric_point_id, "content": item.content, "created_at": item.created_at}
            for item in examples
        ],
    }


def version_payload(db: Session, version: RubricVersion | None) -> dict | None:
    if not version:
        return None
    points = list(db.scalars(select(RubricPoint).where(RubricPoint.rubric_version_id == version.id).order_by(RubricPoint.sort_order)))
    examples = list(db.scalars(select(RubricExample).where(RubricExample.rubric_version_id == version.id).order_by(RubricExample.created_at)))
    return _version_payload(version, points, examples)


def _rubric_payload(
    rubric: Rubric,
    version: RubricVersion | None,
    *,
    points: list[RubricPoint] | None = None,
    examples: list[RubricExample] | None = None,
    include_version: bool = False,
) -> dict:
    result = {
        "id": rubric.id,
        "name": rubric.name,
        "subject": rubric.subject,
        "question_type": rubric.question_type,
        "status": rubric.status,
        "current_version_no": version.version_no if version else None,
        "created_at": rubric.created_at,
        "updated_at": rubric.updated_at,
    }
    if include_version:
        result["current_version"] = _version_payload(version, points or [], examples or []) if version else None
    return result


def rubric_payload(db: Session, rubric: Rubric, include_version: bool = False) -> dict:
    version = latest_version(db, rubric.id)
    if not include_version:
        return _rubric_payload(rubric, version)
    return _rubric_payload(
        rubric,
        version,
        points=list(db.scalars(select(RubricPoint).where(RubricPoint.rubric_version_id == version.id).order_by(RubricPoint.sort_order))) if version else [],
        examples=list(db.scalars(select(RubricExample).where(RubricExample.rubric_version_id == version.id).order_by(RubricExample.created_at))) if version else [],
        include_version=True,
    )


def rubric_payloads(db: Session, rubrics: list[Rubric]) -> list[dict]:
    """Build list responses with bulk version/point/example queries."""

    rubric_ids = [rubric.id for rubric in rubrics]
    if not rubric_ids:
        return []
    versions_by_rubric: dict[str, RubricVersion] = {}
    for version in db.scalars(
        select(RubricVersion)
        .where(RubricVersion.rubric_id.in_(rubric_ids))
        .order_by(RubricVersion.rubric_id, RubricVersion.version_no.desc())
    ):
        versions_by_rubric.setdefault(version.rubric_id, version)
    version_ids = [version.id for version in versions_by_rubric.values()]
    points_by_version: dict[str, list[RubricPoint]] = {version_id: [] for version_id in version_ids}
    examples_by_version: dict[str, list[RubricExample]] = {version_id: [] for version_id in version_ids}
    if version_ids:
        for point in db.scalars(
            select(RubricPoint)
            .where(RubricPoint.rubric_version_id.in_(version_ids))
            .order_by(RubricPoint.rubric_version_id, RubricPoint.sort_order)
        ):
            points_by_version[point.rubric_version_id].append(point)
        for example in db.scalars(
            select(RubricExample)
            .where(RubricExample.rubric_version_id.in_(version_ids))
            .order_by(RubricExample.rubric_version_id, RubricExample.created_at)
        ):
            examples_by_version[example.rubric_version_id].append(example)
    return [
        _rubric_payload(
            rubric,
            (version := versions_by_rubric.get(rubric.id)),
            points=points_by_version.get(version.id, []) if version else [],
            examples=examples_by_version.get(version.id, []) if version else [],
            include_version=True,
        )
        for rubric in rubrics
    ]


def validate_points(total_score: Decimal, points: list) -> None:
    orders = [item.sort_order for item in points]
    if len(orders) != len(set(orders)):
        raise ApiError(422, "DUPLICATE_RUBRIC_POINT_ORDER", "评分点顺序必须唯一，请修正后重试。")


def create_version_records(
    db: Session,
    rubric: Rubric,
    user_id: str,
    payload: RubricCreateRequest,
    version_no: int,
    source_version_id: str | None,
) -> RubricVersion:
    now = utc_now()
    version = RubricVersion(
        id=new_id("rubricv"), rubric_id=rubric.id, version_no=version_no, total_score=payload.total_score,
        status="published", source_version_id=source_version_id, created_by=user_id, created_at=now
    )
    db.add(version)
    # 模型类按文档保持无 ORM relationship；显式 flush 保证外键父记录先落库。
    db.flush()
    points = [
        RubricPoint(
            id=new_id("point"), rubric_version_id=version.id, label=item.label, max_score=item.max_score, sort_order=item.sort_order
        )
        for item in payload.points
    ]
    db.add_all(points)
    for example in payload.examples:
        db.add(
            RubricExample(
                id=new_id("example"), rubric_version_id=version.id, rubric_point_id=None,
                content=example.content, created_at=now
            )
        )
    return version


@router.get("")
def list_rubrics(
    request: Request,
    subject: str | None = Query(default=None),
    question_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Rubric).where(Rubric.owner_user_id == user.id).order_by(Rubric.updated_at.desc())
    if subject:
        query = query.where(Rubric.subject == subject)
    if question_type:
        query = query.where(Rubric.question_type == question_type)
    if status:
        query = query.where(Rubric.status == status)
    items = list(db.scalars(query))
    return success_payload(request, rubric_payloads(db, items), meta={"page": 1, "page_size": len(items), "total": len(items)})


@router.post("")
def create_rubric(payload: RubricCreateRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    validate_points(payload.total_score, payload.points)
    now = utc_now()
    rubric = Rubric(
        id=new_id("rubric"), owner_user_id=user.id, name=payload.name, subject=payload.subject,
        question_type=payload.question_type, status="active", created_at=now, updated_at=now
    )
    db.add(rubric)
    db.flush()
    version = create_version_records(db, rubric, user.id, payload, 1, None)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return success_payload(request, {**rubric_payload(db, rubric), "current_version": version_payload(db, version)})


@router.get("/{rubric_id}")
def get_rubric(rubric_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rubric = get_owned_rubric(db, user.id, rubric_id)
    return success_payload(request, rubric_payload(db, rubric, include_version=True))


@router.patch("/{rubric_id}")
def update_rubric(
    rubric_id: str,
    payload: RubricPatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rubric = get_owned_rubric(db, user.id, rubric_id)
    current = latest_version(db, rubric.id)
    if not current or current.version_no != payload.version:
        actual = current.version_no if current else None
        raise ApiError(409, "RESOURCE_VERSION_CONFLICT", "评分标准已产生新版本，请刷新后再编辑。", [{"expected": payload.version, "actual": actual}])
    validate_points(payload.total_score, payload.points)
    version = create_version_records(db, rubric, user.id, payload, current.version_no + 1, current.id)
    rubric.name = payload.name
    rubric.subject = payload.subject
    rubric.question_type = payload.question_type
    rubric.updated_at = utc_now()
    db.commit()
    return success_payload(request, {**rubric_payload(db, rubric), "current_version": version_payload(db, version)})


@router.post("/{rubric_id}/activate")
def activate_rubric(rubric_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rubric = get_owned_rubric(db, user.id, rubric_id)
    version = latest_version(db, rubric.id)
    if not version:
        raise ApiError(422, "RUBRIC_VERSION_REQUIRED", "评分标准至少需要一个版本才能启用。")
    old_published = list(db.scalars(select(RubricVersion).where(RubricVersion.rubric_id == rubric.id, RubricVersion.status == "published")))
    for item in old_published:
        item.status = "retired"
    version.status = "published"
    rubric.status = "active"
    rubric.updated_at = utc_now()
    db.commit()
    return success_payload(request, rubric_payload(db, rubric, include_version=True))


@router.post("/{rubric_id}/deactivate")
def deactivate_rubric(rubric_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rubric = get_owned_rubric(db, user.id, rubric_id)
    rubric.status = "inactive"
    rubric.updated_at = utc_now()
    db.commit()
    return success_payload(request, rubric_payload(db, rubric, include_version=True))


@router.get("/{rubric_id}/versions")
def list_versions(rubric_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rubric = get_owned_rubric(db, user.id, rubric_id)
    versions = list(db.scalars(select(RubricVersion).where(RubricVersion.rubric_id == rubric.id).order_by(RubricVersion.version_no.desc())))
    return success_payload(request, [version_payload(db, item) for item in versions], meta={"page": 1, "page_size": len(versions), "total": len(versions)})
