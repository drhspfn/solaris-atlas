from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base


class StatusType(Base):
    __tablename__ = "status_type"
    __table_args__ = ({"schema": "ops"},)
    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    terminal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text)
