"""empty message

Revision ID: fbc75ad94bb1
Revises: d04b686f0233
Create Date: 2024-10-30 23:45:12.240807

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fbc75ad94bb1'
down_revision = 'd04b686f0233'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_book_author'), ['author'], unique=False)

    with op.batch_alter_table('book_progress', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_book_progress_book_id'), ['book_id'], unique=True)

    with op.batch_alter_table('chapter', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_book_id'), ['book_id'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('chapter', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_book_id'))

    with op.batch_alter_table('book_progress', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_book_progress_book_id'))

    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_book_author'))

    # ### end Alembic commands ###
