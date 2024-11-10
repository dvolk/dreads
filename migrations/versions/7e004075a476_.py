"""empty message

Revision ID: 7e004075a476
Revises: 
Create Date: 2024-11-10 13:58:21.348502

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7e004075a476'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('book',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(), nullable=False),
    sa.Column('author', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('chapters_count', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('filename')
    )
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_book_author'), ['author'], unique=False)

    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('book_progress',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('chapter_index', sa.Integer(), nullable=False),
    sa.Column('paragraph_index', sa.Integer(), nullable=False),
    sa.Column('updated_datetime', sa.DateTime(), nullable=True),
    sa.Column('book_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['book.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('book_progress', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_book_progress_book_id'), ['book_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_book_progress_user_id'), ['user_id'], unique=False)

    op.create_table('chapter',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('index', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('book_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['book.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chapter', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chapter_book_id'), ['book_id'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('chapter', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chapter_book_id'))

    op.drop_table('chapter')
    with op.batch_alter_table('book_progress', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_book_progress_user_id'))
        batch_op.drop_index(batch_op.f('ix_book_progress_book_id'))

    op.drop_table('book_progress')
    op.drop_table('user')
    with op.batch_alter_table('book', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_book_author'))

    op.drop_table('book')
    # ### end Alembic commands ###