"""Allow negative opening balances in weekly PAB.

Revision ID: a5d7e2c91f44
Revises: a4f6b2c9d817
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a5d7e2c91f44"
down_revision: Union[str, None] = "a4f6b2c9d817"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PAB_INPUT_CONSTRAINT = "ck_pab_week_input_quantities_nonnegative"
NONNEGATIVE_INPUTS = (
    "forecast_demand_base >= 0 AND firm_inbound_base >= 0 "
    "AND firm_po_remainder_base >= 0 AND unconfirmed_supply_base >= 0"
)


def upgrade() -> None:
    with op.batch_alter_table("pab_week") as batch_op:
        batch_op.drop_constraint(PAB_INPUT_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(PAB_INPUT_CONSTRAINT, NONNEGATIVE_INPUTS)


def downgrade() -> None:
    negative_openings = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM pab_week WHERE opening_base < 0")
    ).scalar_one()
    if negative_openings:
        raise RuntimeError(
            "Cannot restore the nonnegative PAB opening constraint while "
            f"{negative_openings} negative opening balance row(s) exist."
        )

    with op.batch_alter_table("pab_week") as batch_op:
        batch_op.drop_constraint(PAB_INPUT_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(
            PAB_INPUT_CONSTRAINT,
            "opening_base >= 0 AND " + NONNEGATIVE_INPUTS,
        )
