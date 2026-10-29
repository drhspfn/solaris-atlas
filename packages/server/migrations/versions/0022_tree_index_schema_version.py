"""Track tree-index format for safe client reindexing."""
import sqlalchemy as sa
from alembic import op

revision = "0022_tree_index_schema_version"
down_revision = "0021_authored_tree_order"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    columns = sa.inspect(op.get_bind()).get_columns('authored_tree_import', schema='core')
    if not any(column['name'] == 'schema_version' for column in columns):
        op.add_column('authored_tree_import', sa.Column('schema_version', sa.Integer(),
                                                       nullable=False, server_default='1'), schema='core')


def downgrade():
    op.drop_column('authored_tree_import', 'schema_version', schema='core')
