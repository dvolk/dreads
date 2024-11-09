"""empty message

Revision ID: ca69f0b9297c
Revises: 9bcece58a05e
Create Date: 2024-11-09 21:46:26.982968

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ca69f0b9297c"
down_revision = "9bcece58a05e"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("book_progress", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("user_id", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.drop_index("ix_book_progress_book_id")
        batch_op.create_index(
            batch_op.f("ix_book_progress_book_id"), ["book_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_book_progress_user_id"), ["user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_book_progress_user_id", "user", ["user_id"], ["id"]
        )

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("book_progress", schema=None) as batch_op:
        batch_op.drop_constraint(None, type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_book_progress_user_id"))
        batch_op.drop_index(batch_op.f("ix_book_progress_book_id"))
        batch_op.create_index("ix_book_progress_book_id", ["book_id"], unique=1)
        batch_op.drop_column("user_id")

    op.drop_table("user")
    # ### end Alembic commands ###