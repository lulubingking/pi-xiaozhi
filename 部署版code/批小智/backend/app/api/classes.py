"""班级和学生管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import ApiError, success_payload
from app.core.utils import new_id, utc_now
from app.db.session import get_db
from app.models import ClassRoom, Student, User
from app.schemas import ClassCreateRequest, ClassPatchRequest, StudentInput, StudentPatchRequest

router = APIRouter(prefix="/classes", tags=["classes"])


def student_payload(student: Student) -> dict:
    return {
        "id": student.id,
        "class_id": student.class_id,
        "student_code": student.student_code,
        "display_name": student.display_name,
        "status": student.status,
        "created_at": student.created_at,
        "updated_at": student.updated_at,
    }


def class_payload(room: ClassRoom, students: list[Student] | None = None) -> dict:
    result = {
        "id": room.id,
        "name": room.name,
        "status": room.status,
        "version": room.version,
        "student_count": len(students) if students is not None else None,
        "created_at": room.created_at,
        "updated_at": room.updated_at,
    }
    if students is not None:
        result["students"] = [student_payload(item) for item in students]
    return result


def get_owned_class(db: Session, user_id: str, class_id: str) -> ClassRoom:
    room = db.scalar(select(ClassRoom).where(ClassRoom.id == class_id, ClassRoom.owner_user_id == user_id))
    if not room:
        raise ApiError(404, "CLASS_NOT_FOUND", "班级不存在或当前账号无权访问。")
    return room


def validate_student_codes(students: list[StudentInput]) -> None:
    codes = [item.student_code for item in students]
    if len(codes) != len(set(codes)):
        raise ApiError(422, "DUPLICATE_STUDENT_CODE", "同一批学生中存在重复学号，请修正后重试。")


@router.get("")
def list_classes(
    request: Request,
    status: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(ClassRoom).where(ClassRoom.owner_user_id == user.id).order_by(ClassRoom.updated_at.desc())
    if status:
        query = query.where(ClassRoom.status == status)
    rooms = list(db.scalars(query))
    counts = dict(
        db.execute(
            select(ClassRoom.id, func.count(Student.id))
            .join(Student, Student.class_id == ClassRoom.id, isouter=True)
            .where(ClassRoom.owner_user_id == user.id)
            .group_by(ClassRoom.id)
        ).all()
    )
    data = [{**class_payload(room), "student_count": counts.get(room.id, 0)} for room in rooms]
    return success_payload(request, data, meta={"page": 1, "page_size": len(data), "total": len(data)})


@router.post("")
def create_class(payload: ClassCreateRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    validate_student_codes(payload.students)
    existing = db.scalar(
        select(ClassRoom).where(ClassRoom.owner_user_id == user.id, ClassRoom.name == payload.name, ClassRoom.status == "active")
    )
    if existing:
        raise ApiError(409, "DUPLICATE_CLASS_NAME", "当前账号已有同名启用班级，请更换班级名称。")

    now = utc_now()
    room = ClassRoom(
        id=new_id("class"), owner_user_id=user.id, name=payload.name, status="active", version=1, created_at=now, updated_at=now
    )
    db.add(room)
    db.flush()
    students = [
        Student(
            id=new_id("stu"), class_id=room.id, student_code=item.student_code, display_name=item.display_name,
            status="active", created_at=now, updated_at=now
        )
        for item in payload.students
    ]
    db.add_all(students)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(422, "DUPLICATE_STUDENT_CODE", "学生学号在该班级内必须唯一，请检查后重试。") from exc
    return success_payload(request, class_payload(room, students))


@router.get("/{class_id}")
def get_class(class_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = get_owned_class(db, user.id, class_id)
    students = list(db.scalars(select(Student).where(Student.class_id == room.id).order_by(Student.student_code)))
    return success_payload(request, class_payload(room, students))


@router.patch("/{class_id}")
def update_class(
    class_id: str,
    payload: ClassPatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room = get_owned_class(db, user.id, class_id)
    if room.version != payload.version:
        raise ApiError(409, "RESOURCE_VERSION_CONFLICT", "班级已被其他请求修改，请刷新后再提交。", [{"expected": payload.version, "actual": room.version}])
    if payload.name is not None:
        duplicate = db.scalar(
            select(ClassRoom).where(
                ClassRoom.owner_user_id == user.id,
                ClassRoom.name == payload.name,
                ClassRoom.status == (payload.status or room.status),
                ClassRoom.id != room.id,
            )
        )
        if duplicate:
            raise ApiError(409, "DUPLICATE_CLASS_NAME", "当前账号已有同名班级，请更换班级名称。")
        room.name = payload.name
    if payload.status is not None:
        room.status = payload.status
    room.version += 1
    room.updated_at = utc_now()
    db.commit()
    students = list(db.scalars(select(Student).where(Student.class_id == room.id).order_by(Student.student_code)))
    return success_payload(request, class_payload(room, students))


@router.get("/{class_id}/students")
def list_students(class_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = get_owned_class(db, user.id, class_id)
    students = list(db.scalars(select(Student).where(Student.class_id == room.id).order_by(Student.student_code)))
    return success_payload(request, [student_payload(item) for item in students], meta={"page": 1, "page_size": len(students), "total": len(students)})


@router.post("/{class_id}/students")
def create_student(
    class_id: str,
    payload: StudentInput,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room = get_owned_class(db, user.id, class_id)
    if room.status != "active":
        raise ApiError(422, "CLASS_INACTIVE", "停用班级不能新增学生，请先启用班级。")
    existing = db.scalar(select(Student).where(Student.class_id == room.id, Student.student_code == payload.student_code))
    if existing:
        raise ApiError(422, "DUPLICATE_STUDENT_CODE", "该班级已存在相同学号，请修改后重试。")
    now = utc_now()
    student = Student(
        id=new_id("stu"), class_id=room.id, student_code=payload.student_code, display_name=payload.display_name,
        status="active", created_at=now, updated_at=now
    )
    db.add(student)
    db.commit()
    return success_payload(request, student_payload(student))


@router.patch("/students/{student_id}")
def update_student(
    student_id: str,
    payload: StudentPatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    student = db.scalar(
        select(Student).join(ClassRoom, Student.class_id == ClassRoom.id).where(Student.id == student_id, ClassRoom.owner_user_id == user.id)
    )
    if not student:
        raise ApiError(404, "STUDENT_NOT_FOUND", "学生不存在或当前账号无权访问。")
    if payload.student_code is not None and payload.student_code != student.student_code:
        duplicate = db.scalar(select(Student).where(Student.class_id == student.class_id, Student.student_code == payload.student_code, Student.id != student.id))
        if duplicate:
            raise ApiError(422, "DUPLICATE_STUDENT_CODE", "该班级已存在相同学号，请修改后重试。")
        student.student_code = payload.student_code
    if payload.display_name is not None:
        student.display_name = payload.display_name
    if payload.status is not None:
        student.status = payload.status
    student.updated_at = utc_now()
    db.commit()
    return success_payload(request, student_payload(student))
