"""empty message

Revision ID: 9bcece58a05e
Revises: fbc75ad94bb1
Create Date: 2024-11-02 19:38:13.845300

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9bcece58a05e'
down_revision = 'fbc75ad94bb1'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('book_progress', schema=None) as batch_op:
        batch_op.add_column(sa.Column('paragraph_index', sa.Integer(), nullable=False, server_default="0"))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('book_progress', schema=None) as batch_op:
        batch_op.drop_column('paragraph_index')

    # ### end Alembic commands ###
