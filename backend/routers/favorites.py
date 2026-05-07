"""收藏夹。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user
from models import Favorite, User
from schemas_auth import FavoriteAddBody, FavoriteOut
from url_normalize import normalize_original_url

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.post("/add", response_model=FavoriteOut, status_code=status.HTTP_201_CREATED)
def add_favorite(
    body: FavoriteAddBody,
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> Favorite:
    norm = normalize_original_url(body.original_url)
    if not norm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="original_url 无效")

    row = Favorite(
        user_id=current.id,
        title=body.title.strip(),
        price_cny=float(body.price_cny),
        image_url=body.image_url.strip(),
        original_url=body.original_url.strip(),
        original_url_normalized=norm,
        platform=body.platform.strip(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已收藏过该链接",
        ) from None
    db.refresh(row)
    return row


@router.delete("/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
    favorite_id: int | None = Query(None, description="收藏记录 id"),
    original_url: str | None = Query(None, min_length=8, description="商品原网链接（与 id 二选一）"),
) -> None:
    if favorite_id is None and not (original_url and original_url.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请提供 favorite_id 或 original_url",
        )

    q = select(Favorite).where(Favorite.user_id == current.id)
    if favorite_id is not None:
        q = q.where(Favorite.id == favorite_id)
    else:
        norm = normalize_original_url(original_url or "")
        if not norm:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="original_url 无效")
        q = q.where(Favorite.original_url_normalized == norm)

    fav = db.scalar(q)
    if fav is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到收藏记录")

    db.delete(fav)
    db.commit()


@router.get("", response_model=list[FavoriteOut])
def list_favorites(
    db: Annotated[Session, Depends(get_db)],
    current: Annotated[User, Depends(get_current_user)],
) -> list[Favorite]:
    rows = db.scalars(
        select(Favorite)
        .where(Favorite.user_id == current.id)
        .order_by(Favorite.created_at.desc()),
    ).all()
    return list(rows)
