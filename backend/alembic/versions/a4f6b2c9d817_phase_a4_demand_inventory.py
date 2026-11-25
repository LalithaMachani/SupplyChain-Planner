"""phase_a4_demand_inventory

Revision ID: a4f6b2c9d817
Revises: ed1d5fd97142
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "a4f6b2c9d817"
down_revision: Union[str, None] = "ed1d5fd97142"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UBIGINT = sa.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")
USMALLINT = sa.SmallInteger().with_variant(mysql.SMALLINT(unsigned=True), "mysql")


def upgrade() -> None:
    op.create_table(
        "inventory_ledger_control",
        sa.Column("ledger_key", sa.String(32), nullable=False),
        sa.Column(
            "committed_version",
            UBIGINT,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "committed_version >= 0",
            name="ck_inventory_ledger_version_nonnegative",
        ),
        sa.PrimaryKeyConstraint("ledger_key"),
    )
    op.create_table(
        "inventory_snapshot",
        sa.Column("inventory_snapshot_id", UBIGINT, autoincrement=True, nullable=False),
        sa.Column("snapshot_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "ledger_watermark",
            UBIGINT,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("source_name", sa.String(120), nullable=False),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("snapshot_status", sa.String(20), nullable=False),
        sa.Column("created_by_user_id", UBIGINT),
        sa.CheckConstraint(
            "snapshot_status IN ('draft', 'validated', 'superseded')",
            name="ck_inventory_snapshot_status",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.user_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("inventory_snapshot_id"),
    )
    op.create_index(
        "ix_inventory_snapshot_status_capture",
        "inventory_snapshot",
        ["snapshot_status", "captured_at"],
    )
    op.create_table(
        "inventory_balance",
        sa.Column("inventory_snapshot_id", UBIGINT, nullable=False),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("location_id", UBIGINT, nullable=False),
        sa.Column("quantity_on_hand_base", sa.Numeric(28, 8), nullable=False),
        sa.CheckConstraint(
            "quantity_on_hand_base >= 0",
            name="ck_inventory_balance_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_snapshot_id"],
            ["inventory_snapshot.inventory_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.sku_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["location.location_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("inventory_snapshot_id", "sku_id", "location_id"),
    )
    op.create_index(
        "ix_inventory_balance_snapshot_location",
        "inventory_balance",
        ["inventory_snapshot_id", "location_id", "sku_id"],
    )

    op.create_table(
        "inventory_receipt_posting",
        sa.Column(
            "inventory_receipt_posting_id",
            UBIGINT,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("inbound_shipment_line_id", UBIGINT, nullable=False),
        sa.Column("purchase_order_line_id", UBIGINT),
        sa.Column("pending_line_id", UBIGINT, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("posting_type", sa.String(16), nullable=False),
        sa.Column("prior_quantity_received", sa.Numeric(18, 4), nullable=False),
        sa.Column("target_quantity_received", sa.Numeric(18, 4), nullable=False),
        sa.Column("quantity_delta_uom", sa.Numeric(18, 4), nullable=False),
        sa.Column("uom_to_base_factor", sa.Numeric(18, 8), nullable=False),
        sa.Column("prior_line_received_base", sa.Numeric(38, 12), nullable=False),
        sa.Column("target_line_received_base", sa.Numeric(38, 12), nullable=False),
        sa.Column("prior_po_received_base", sa.Numeric(28, 8)),
        sa.Column("target_po_received_base", sa.Numeric(28, 8)),
        sa.Column("posting_status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "posting_type IN ('receipt', 'correction')",
            name="ck_receipt_posting_type",
        ),
        sa.CheckConstraint(
            "(posting_type = 'receipt' AND quantity_delta_uom > 0) OR "
            "(posting_type = 'correction' AND quantity_delta_uom <> 0)",
            name="ck_receipt_posting_delta_type",
        ),
        sa.CheckConstraint(
            "posting_status IN ('pending', 'applied')",
            name="ck_receipt_posting_status",
        ),
        sa.CheckConstraint(
            "uom_to_base_factor > 0",
            name="ck_receipt_posting_factor_positive",
        ),
        sa.CheckConstraint(
            "prior_quantity_received >= 0 AND target_quantity_received >= 0",
            name="ck_receipt_posting_line_quantity_nonnegative",
        ),
        sa.CheckConstraint(
            "target_quantity_received - prior_quantity_received = quantity_delta_uom",
            name="ck_receipt_posting_line_delta",
        ),
        sa.CheckConstraint(
            "prior_line_received_base >= 0 AND target_line_received_base >= 0",
            name="ck_receipt_posting_line_base_nonnegative",
        ),
        sa.CheckConstraint(
            "target_line_received_base - prior_line_received_base = "
            "quantity_delta_uom * uom_to_base_factor",
            name="ck_receipt_posting_line_base_delta",
        ),
        sa.CheckConstraint(
            "(purchase_order_line_id IS NULL "
            "AND prior_po_received_base IS NULL AND target_po_received_base IS NULL) OR "
            "(purchase_order_line_id IS NOT NULL "
            "AND prior_po_received_base IS NOT NULL AND target_po_received_base IS NOT NULL)",
            name="ck_receipt_posting_po_counters_pair",
        ),
        sa.CheckConstraint(
            "(posting_status = 'pending' AND pending_line_id = inbound_shipment_line_id "
            "AND applied_at IS NULL) OR "
            "(posting_status = 'applied' AND pending_line_id IS NULL "
            "AND applied_at IS NOT NULL)",
            name="ck_receipt_posting_pending_state",
        ),
        sa.CheckConstraint(
            "pending_line_id IS NULL OR pending_line_id = inbound_shipment_line_id",
            name="ck_receipt_posting_pending_line",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_shipment_line_id"],
            ["inbound_shipment_line.inbound_shipment_line_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_line_id"],
            ["purchase_order_line.purchase_order_line_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_line_id"],
            ["inbound_shipment_line.inbound_shipment_line_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("inventory_receipt_posting_id"),
        sa.UniqueConstraint(
            "inbound_shipment_line_id",
            "pending_line_id",
            name="uq_receipt_posting_line_pending",
        ),
    )
    op.create_index(
        "ix_receipt_posting_line_created",
        "inventory_receipt_posting",
        ["inbound_shipment_line_id", "created_at"],
    )

    op.create_table(
        "inventory_transaction",
        sa.Column("inventory_transaction_id", UBIGINT, autoincrement=True, nullable=False),
        sa.Column("ledger_version", UBIGINT, nullable=False, unique=True),
        sa.Column("inventory_receipt_posting_id", UBIGINT, unique=True),
        sa.Column("inbound_shipment_line_id", UBIGINT),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("location_id", UBIGINT, nullable=False),
        sa.Column("transaction_type", sa.String(24), nullable=False),
        sa.Column("quantity_delta_base", sa.Numeric(38, 12), nullable=False),
        sa.Column("source_event_key", sa.String(160), nullable=False, unique=True),
        sa.Column(
            "posted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "transaction_type IN ('receipt', 'receipt_correction', 'issue', "
            "'adjustment', 'transfer_in', 'transfer_out')",
            name="ck_inventory_transaction_type",
        ),
        sa.CheckConstraint(
            "quantity_delta_base <> 0",
            name="ck_inventory_transaction_nonzero",
        ),
        sa.CheckConstraint(
            "ledger_version > 0",
            name="ck_inventory_transaction_version_positive",
        ),
        sa.CheckConstraint(
            "(transaction_type IN ('receipt', 'receipt_correction') "
            "AND inventory_receipt_posting_id IS NOT NULL "
            "AND inbound_shipment_line_id IS NOT NULL) OR "
            "(transaction_type NOT IN ('receipt', 'receipt_correction') "
            "AND inventory_receipt_posting_id IS NULL)",
            name="ck_inventory_transaction_receipt_source",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_receipt_posting_id"],
            ["inventory_receipt_posting.inventory_receipt_posting_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_shipment_line_id"],
            ["inbound_shipment_line.inbound_shipment_line_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.sku_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["location.location_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("inventory_transaction_id"),
    )
    op.create_index(
        "ix_inventory_transaction_sku_location_version",
        "inventory_transaction",
        ["sku_id", "location_id", "ledger_version"],
    )

    op.create_table(
        "supply_cancellation",
        sa.Column("supply_cancellation_id", UBIGINT, autoincrement=True, nullable=False),
        sa.Column("purchase_order_line_id", UBIGINT),
        sa.Column("inbound_shipment_line_id", UBIGINT),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("quantity_cancelled_base", sa.Numeric(28, 8), nullable=False),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.CheckConstraint(
            "(purchase_order_line_id IS NOT NULL AND inbound_shipment_line_id IS NULL) OR "
            "(purchase_order_line_id IS NULL AND inbound_shipment_line_id IS NOT NULL)",
            name="ck_supply_cancellation_single_source",
        ),
        sa.CheckConstraint(
            "quantity_cancelled_base > 0",
            name="ck_supply_cancellation_positive",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_line_id"],
            ["purchase_order_line.purchase_order_line_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_shipment_line_id"],
            ["inbound_shipment_line.inbound_shipment_line_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("supply_cancellation_id"),
    )
    op.create_index(
        "ix_supply_cancellation_po_time",
        "supply_cancellation",
        ["purchase_order_line_id", "cancelled_at"],
    )
    op.create_index(
        "ix_supply_cancellation_shipment_time",
        "supply_cancellation",
        ["inbound_shipment_line_id", "cancelled_at"],
    )

    op.create_table(
        "planning_run",
        sa.Column("planning_run_id", UBIGINT, autoincrement=True, nullable=False),
        sa.Column("run_key", sa.String(64), nullable=False, unique=True),
        sa.Column("scenario_key", sa.String(64), nullable=False),
        sa.Column("horizon_start_week", sa.Date(), nullable=False),
        sa.Column(
            "horizon_weeks",
            USMALLINT,
            server_default=sa.text("52"),
            nullable=False,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("inventory_snapshot_id", UBIGINT, nullable=False),
        sa.Column(
            "inventory_cutoff_version",
            UBIGINT,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("severity_policy_version", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("run_status", sa.String(20), nullable=False),
        sa.Column("created_by_user_id", UBIGINT),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("horizon_weeks = 52", name="ck_planning_run_horizon"),
        sa.CheckConstraint(
            "run_status IN ('draft', 'validating', 'calculating', 'completed', "
            "'published', 'failed', 'superseded')",
            name="ck_planning_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_snapshot_id"],
            ["inventory_snapshot.inventory_snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.user_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("planning_run_id"),
    )
    op.create_index(
        "ix_planning_run_status_created",
        "planning_run",
        ["run_status", "created_at"],
    )

    op.create_table(
        "planning_item",
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("location_id", UBIGINT, nullable=False),
        sa.ForeignKeyConstraint(
            ["planning_run_id"],
            ["planning_run.planning_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.sku_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["location.location_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("planning_run_id", "sku_id", "location_id"),
    )
    op.create_index(
        "ix_planning_item_run_location_sku",
        "planning_item",
        ["planning_run_id", "location_id", "sku_id"],
    )

    op.create_table(
        "forecast_demand_week",
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("location_id", UBIGINT, nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("forecast_quantity_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("source_staging_record_id", UBIGINT),
        sa.Column("source_fingerprint", sa.String(64)),
        sa.ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "forecast_quantity_base >= 0",
            name="ck_forecast_demand_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["source_staging_record_id"],
            ["staging_record.staging_record_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "planning_run_id",
            "sku_id",
            "location_id",
            "week_start",
        ),
    )
    op.create_index(
        "ix_forecast_demand_run_location_week",
        "forecast_demand_week",
        ["planning_run_id", "location_id", "week_start", "sku_id"],
    )

    op.create_table(
        "planning_po_residual_snapshot",
        sa.Column(
            "planning_po_residual_snapshot_id",
            UBIGINT,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("purchase_order_line_id", UBIGINT, nullable=False),
        sa.Column("ordered_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("received_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("scheduled_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("cancelled_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("residual_base", sa.Numeric(28, 8), nullable=False),
        sa.CheckConstraint(
            "ordered_base >= 0 AND received_base >= 0 AND scheduled_base >= 0 "
            "AND cancelled_base >= 0 AND residual_base >= 0",
            name="ck_planning_po_residual_nonnegative",
        ),
        sa.CheckConstraint(
            "ordered_base = received_base + scheduled_base + cancelled_base + residual_base",
            name="ck_planning_po_residual_reconciled",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id"],
            ["planning_run.planning_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_line_id"],
            ["purchase_order_line.purchase_order_line_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("planning_po_residual_snapshot_id"),
        sa.UniqueConstraint(
            "planning_run_id",
            "purchase_order_line_id",
            name="uq_planning_po_residual_run_line",
        ),
        sa.UniqueConstraint(
            "planning_po_residual_snapshot_id",
            "planning_run_id",
            name="uq_planning_po_residual_id_run",
        ),
    )
    op.create_index(
        "ix_planning_po_residual_line",
        "planning_po_residual_snapshot",
        ["purchase_order_line_id", "planning_run_id"],
    )

    op.create_table(
        "planning_supply_snapshot",
        sa.Column(
            "planning_supply_snapshot_id",
            UBIGINT,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("inbound_shipment_line_id", UBIGINT),
        sa.Column("planning_po_residual_snapshot_id", UBIGINT),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("destination_location_id", UBIGINT, nullable=False),
        sa.Column("allocation_no", USMALLINT, nullable=False),
        sa.Column("allocation_idempotency_key", sa.String(128), nullable=False),
        sa.Column("candidate_quantity_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("firm_quantity_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("unconfirmed_quantity_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("capacity_exceeded_quantity_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("expected_arrival_date", sa.Date()),
        sa.Column("expected_week_start", sa.Date()),
        sa.Column("supplier_capacity_reservation_id", UBIGINT),
        sa.Column("supply_status", sa.String(24), nullable=False),
        sa.UniqueConstraint(
            "planning_run_id",
            "inbound_shipment_line_id",
            name="uq_planning_supply_run_inbound_line",
        ),
        sa.UniqueConstraint(
            "planning_po_residual_snapshot_id",
            "destination_location_id",
            name="uq_planning_supply_po_destination",
        ),
        sa.UniqueConstraint(
            "planning_po_residual_snapshot_id",
            "allocation_no",
            name="uq_planning_supply_po_allocation_no",
        ),
        sa.UniqueConstraint(
            "planning_run_id",
            "allocation_idempotency_key",
            name="uq_planning_supply_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id"],
            ["planning_run.planning_run_id"],
            name="fk_planning_supply_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["planning_po_residual_snapshot_id", "planning_run_id"],
            [
                "planning_po_residual_snapshot.planning_po_residual_snapshot_id",
                "planning_po_residual_snapshot.planning_run_id",
            ],
            name="fk_planning_supply_po_residual_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "destination_location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            name="fk_planning_supply_item",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(source_kind = 'shipment' AND inbound_shipment_line_id IS NOT NULL "
            "AND planning_po_residual_snapshot_id IS NULL AND allocation_no = 0) OR "
            "(source_kind = 'po_allocation' AND inbound_shipment_line_id IS NULL "
            "AND planning_po_residual_snapshot_id IS NOT NULL AND allocation_no > 0)",
            name="ck_planning_supply_source_kind",
        ),
        sa.CheckConstraint(
            "source_kind IN ('shipment', 'po_allocation')",
            name="ck_planning_supply_source",
        ),
        sa.CheckConstraint(
            "candidate_quantity_base >= 0 AND firm_quantity_base >= 0 "
            "AND unconfirmed_quantity_base >= 0 "
            "AND capacity_exceeded_quantity_base >= 0",
            name="ck_planning_supply_quantities_nonnegative",
        ),
        sa.CheckConstraint(
            "candidate_quantity_base = firm_quantity_base + unconfirmed_quantity_base",
            name="ck_planning_supply_quantities_reconciled",
        ),
        sa.CheckConstraint(
            "capacity_exceeded_quantity_base <= unconfirmed_quantity_base",
            name="ck_planning_supply_capacity_excess",
        ),
        sa.CheckConstraint(
            "supply_status IN ('scheduled', 'overdue', 'outside_horizon', "
            "'undated', 'unconfirmed')",
            name="ck_planning_supply_status",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_shipment_line_id"],
            ["inbound_shipment_line.inbound_shipment_line_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_location_id"],
            ["location.location_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_capacity_reservation_id"],
            ["supplier_capacity_reservation.supplier_capacity_reservation_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("planning_supply_snapshot_id"),
    )
    op.create_index(
        "ix_planning_supply_run_week_location",
        "planning_supply_snapshot",
        ["planning_run_id", "expected_week_start", "destination_location_id"],
    )

    op.create_table(
        "pab_week",
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("location_id", UBIGINT, nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("opening_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("forecast_demand_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("firm_inbound_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("firm_po_remainder_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("unconfirmed_supply_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("ending_firm_base", sa.Numeric(38, 12), nullable=False),
        sa.Column("shortage_quantity_base", sa.Numeric(38, 12), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "opening_base + firm_inbound_base + firm_po_remainder_base "
            "- forecast_demand_base = ending_firm_base",
            name="ck_pab_week_equation",
        ),
        sa.CheckConstraint(
            "shortage_quantity_base >= 0",
            name="ck_pab_week_shortage_nonnegative",
        ),
        sa.CheckConstraint(
            "severity IN ('none', 'low', 'medium', 'high', 'critical')",
            name="ck_pab_week_severity",
        ),
        sa.CheckConstraint(
            "opening_base >= 0 AND forecast_demand_base >= 0 "
            "AND firm_inbound_base >= 0 AND firm_po_remainder_base >= 0 "
            "AND unconfirmed_supply_base >= 0",
            name="ck_pab_week_input_quantities_nonnegative",
        ),
        sa.PrimaryKeyConstraint(
            "planning_run_id",
            "sku_id",
            "location_id",
            "week_start",
        ),
    )
    op.create_index(
        "ix_pab_run_location_week",
        "pab_week",
        ["planning_run_id", "location_id", "week_start", "sku_id"],
    )

    op.create_table(
        "planning_run_finding",
        sa.Column(
            "planning_run_finding_id",
            UBIGINT,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("source_validation_finding_id", UBIGINT),
        sa.Column("finding_origin", sa.String(16), nullable=False),
        sa.Column("finding_code", sa.String(40)),
        sa.Column("issue_code", sa.String(64)),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("sku_id", UBIGINT),
        sa.Column("location_id", UBIGINT),
        sa.Column("week_start", sa.Date()),
        sa.Column("resolution_status", sa.String(20)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "planning_run_id",
            "source_validation_finding_id",
            name="uq_planning_run_source_finding",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            name="fk_planning_run_finding_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id", "week_start"],
            [
                "pab_week.planning_run_id",
                "pab_week.sku_id",
                "pab_week.location_id",
                "pab_week.week_start",
            ],
            name="fk_planning_run_finding_pab",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(finding_origin = 'rule' AND finding_code IN "
            "('DUPLICATE_INBOUND', 'UNAPPROVED_SUBSTITUTE', 'CAPACITY_EXCEEDED', "
            "'MISSING_LEAD_TIME', 'UOM_MISMATCH', 'CAPACITY_UNCONFIRMED') "
            "AND issue_code IS NULL) OR "
            "(finding_origin = 'readiness' AND finding_code IS NULL "
            "AND issue_code IS NOT NULL)",
            name="ck_planning_run_finding_code",
        ),
        sa.CheckConstraint(
            "finding_origin IN ('rule', 'readiness')",
            name="ck_planning_run_finding_origin",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'critical')",
            name="ck_planning_run_finding_severity",
        ),
        sa.CheckConstraint(
            "(resolution_status IS NULL AND resolved_at IS NULL) OR "
            "(resolution_status = 'open' AND resolved_at IS NULL) OR "
            "(resolution_status IN ('resolved', 'waived') AND resolved_at IS NOT NULL)",
            name="ck_planning_run_finding_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_id"],
            ["planning_run.planning_run_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_validation_finding_id"],
            ["validation_finding.validation_finding_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("planning_run_finding_id"),
    )
    op.create_index(
        "ix_planning_run_finding_code_severity",
        "planning_run_finding",
        ["planning_run_id", "finding_code", "severity"],
    )

    op.create_table(
        "shortage_cause",
        sa.Column("shortage_cause_id", UBIGINT, autoincrement=True, nullable=False),
        sa.Column("planning_run_id", UBIGINT, nullable=False),
        sa.Column("sku_id", UBIGINT, nullable=False),
        sa.Column("location_id", UBIGINT, nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("planning_run_finding_id", UBIGINT),
        sa.Column("cause_code", sa.String(48), nullable=False),
        sa.Column("contribution_quantity_base", sa.Numeric(28, 8), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id", "week_start"],
            [
                "pab_week.planning_run_id",
                "pab_week.sku_id",
                "pab_week.location_id",
                "pab_week.week_start",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "cause_code IN ('demand_exceeds_firm_supply', 'overdue_supply', "
            "'capacity_unconfirmed_supply', 'opening_inventory_deficit', "
            "'supply_outside_horizon')",
            name="ck_shortage_cause_code",
        ),
        sa.CheckConstraint(
            "contribution_quantity_base >= 0",
            name="ck_shortage_cause_quantity_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["planning_run_finding_id"],
            ["planning_run_finding.planning_run_finding_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("shortage_cause_id"),
    )
    op.create_index(
        "ix_shortage_cause_pab",
        "shortage_cause",
        ["planning_run_id", "location_id", "week_start", "sku_id"],
    )

    op.execute(
        "INSERT INTO inventory_ledger_control (ledger_key, committed_version) "
        "VALUES ('inventory', 0)"
    )

    op.execute("""
    CREATE TRIGGER trg_inventory_snapshot_bi
    BEFORE INSERT ON inventory_snapshot
    FOR EACH ROW
    BEGIN
        DECLARE v_current_version BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_missing TINYINT DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_missing = 1;
        SELECT committed_version INTO v_current_version
          FROM inventory_ledger_control
         WHERE ledger_key = 'inventory'
         FOR UPDATE;
        IF v_missing = 1 OR v_current_version IS NULL THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Inventory ledger control row is missing';
        END IF;
        SET NEW.ledger_watermark = v_current_version;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_planning_run_bi
    BEFORE INSERT ON planning_run
    FOR EACH ROW
    BEGIN
        DECLARE v_snapshot_watermark BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_snapshot_status VARCHAR(20) DEFAULT NULL;
        DECLARE v_current_version BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_missing TINYINT DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_missing = 1;
        SELECT ledger_watermark, snapshot_status
          INTO v_snapshot_watermark, v_snapshot_status
          FROM inventory_snapshot
         WHERE inventory_snapshot_id = NEW.inventory_snapshot_id;
        IF v_missing = 1 OR v_snapshot_status <> 'validated' THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Planning runs require a validated inventory snapshot';
        END IF;
        SET v_missing = 0;
        SELECT committed_version INTO v_current_version
          FROM inventory_ledger_control
         WHERE ledger_key = 'inventory'
         FOR UPDATE;
        IF v_missing = 1 OR v_current_version IS NULL
           OR v_current_version < v_snapshot_watermark THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Inventory snapshot watermark is ahead of the ledger';
        END IF;
        SET NEW.inventory_cutoff_version = v_current_version;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inventory_transaction_bi
    BEFORE INSERT ON inventory_transaction
    FOR EACH ROW
    BEGIN
        DECLARE v_current_version BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_posting_status VARCHAR(16) DEFAULT NULL;
        DECLARE v_posting_line_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_posting_type VARCHAR(16) DEFAULT NULL;
        DECLARE v_po_line_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_prior_line_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_target_line_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_prior_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_target_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_expected_delta DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_expected_sku_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_expected_location_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_missing TINYINT DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_missing = 1;

        IF NEW.inventory_receipt_posting_id IS NOT NULL THEN
            SELECT posting_status, inbound_shipment_line_id, posting_type,
                   purchase_order_line_id, prior_line_received_base,
                   target_line_received_base, prior_po_received_base,
                   target_po_received_base
              INTO v_posting_status, v_posting_line_id, v_posting_type, v_po_line_id,
                   v_prior_line_base, v_target_line_base, v_prior_po_base, v_target_po_base
              FROM inventory_receipt_posting
             WHERE inventory_receipt_posting_id = NEW.inventory_receipt_posting_id;
            IF v_missing = 1 OR v_posting_status <> 'pending'
               OR NEW.inbound_shipment_line_id <> v_posting_line_id THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Receipt transaction requires its matching pending posting';
            END IF;
            IF v_po_line_id IS NULL THEN
                SET v_expected_delta = v_target_line_base - v_prior_line_base;
            ELSE
                SET v_expected_delta = v_target_po_base - v_prior_po_base;
            END IF;
            IF NOT (NEW.quantity_delta_base <=> v_expected_delta)
               OR NOT (NEW.transaction_type <=> IF(
                   v_posting_type = 'correction',
                   'receipt_correction',
                   'receipt'
               ))
               OR NOT (NEW.source_event_key <=> CONCAT(
                   'receipt-posting:',
                   NEW.inventory_receipt_posting_id
               )) THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Receipt transaction does not reconcile to its posting';
            END IF;
            SET v_missing = 0;
            SELECT inbound_shipment_line.sku_id, inbound_shipment.location_id
              INTO v_expected_sku_id, v_expected_location_id
              FROM inbound_shipment_line
              JOIN inbound_shipment
                ON inbound_shipment.inbound_shipment_id =
                   inbound_shipment_line.inbound_shipment_id
             WHERE inbound_shipment_line.inbound_shipment_line_id =
                   v_posting_line_id;
            IF v_missing = 1
               OR NEW.sku_id <> v_expected_sku_id
               OR NEW.location_id <> v_expected_location_id THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Receipt transaction item or location does not match shipment';
            END IF;
        END IF;

        SET v_missing = 0;
        SELECT committed_version INTO v_current_version
          FROM inventory_ledger_control
         WHERE ledger_key = 'inventory'
         FOR UPDATE;
        IF v_missing = 1 OR v_current_version IS NULL THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Inventory ledger control row is missing';
        END IF;
        IF v_current_version >= 18446744073709551615 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Inventory ledger version is exhausted';
        END IF;
        SET NEW.ledger_version = v_current_version + 1;
        UPDATE inventory_ledger_control
           SET committed_version = NEW.ledger_version
         WHERE ledger_key = 'inventory';
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inventory_receipt_posting_bu
    BEFORE UPDATE ON inventory_receipt_posting
    FOR EACH ROW
    BEGIN
        DECLARE v_transaction_count BIGINT DEFAULT 0;
        DECLARE v_transaction_line_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_transaction_sku_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_transaction_location_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_transaction_type VARCHAR(24) DEFAULT NULL;
        DECLARE v_transaction_delta DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_transaction_event_key VARCHAR(160) DEFAULT NULL;
        DECLARE v_expected_sku_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_expected_location_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_expected_delta DECIMAL(38,12) DEFAULT NULL;

        IF OLD.posting_status <> 'pending'
           OR NEW.posting_status <> 'applied'
           OR OLD.pending_line_id <> OLD.inbound_shipment_line_id
           OR NEW.pending_line_id IS NOT NULL
           OR OLD.applied_at IS NOT NULL
           OR NEW.applied_at IS NULL
           OR NOT (NEW.inventory_receipt_posting_id <=> OLD.inventory_receipt_posting_id)
           OR NOT (NEW.inbound_shipment_line_id <=> OLD.inbound_shipment_line_id)
           OR NOT (NEW.purchase_order_line_id <=> OLD.purchase_order_line_id)
           OR NOT (NEW.idempotency_key <=> OLD.idempotency_key)
           OR NOT (NEW.payload_hash <=> OLD.payload_hash)
           OR NOT (NEW.posting_type <=> OLD.posting_type)
           OR NOT (NEW.prior_quantity_received <=> OLD.prior_quantity_received)
           OR NOT (NEW.target_quantity_received <=> OLD.target_quantity_received)
           OR NOT (NEW.quantity_delta_uom <=> OLD.quantity_delta_uom)
           OR NOT (NEW.uom_to_base_factor <=> OLD.uom_to_base_factor)
           OR NOT (NEW.prior_line_received_base <=> OLD.prior_line_received_base)
           OR NOT (NEW.target_line_received_base <=> OLD.target_line_received_base)
           OR NOT (NEW.prior_po_received_base <=> OLD.prior_po_received_base)
           OR NOT (NEW.target_po_received_base <=> OLD.target_po_received_base)
           OR NOT (NEW.created_at <=> OLD.created_at) THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Receipt postings are immutable except for first application';
        END IF;

        SELECT COUNT(*), MAX(inbound_shipment_line_id), MAX(sku_id), MAX(location_id),
               MAX(transaction_type), MAX(quantity_delta_base), MAX(source_event_key)
          INTO v_transaction_count, v_transaction_line_id, v_transaction_sku_id,
               v_transaction_location_id, v_transaction_type, v_transaction_delta,
               v_transaction_event_key
          FROM inventory_transaction
         WHERE inventory_receipt_posting_id = OLD.inventory_receipt_posting_id;

        IF OLD.purchase_order_line_id IS NULL THEN
            SET v_expected_delta = OLD.target_line_received_base - OLD.prior_line_received_base;
        ELSE
            SET v_expected_delta = OLD.target_po_received_base - OLD.prior_po_received_base;
        END IF;

        SELECT inbound_shipment_line.sku_id, inbound_shipment.location_id
          INTO v_expected_sku_id, v_expected_location_id
          FROM inbound_shipment_line
          JOIN inbound_shipment
            ON inbound_shipment.inbound_shipment_id =
               inbound_shipment_line.inbound_shipment_id
         WHERE inbound_shipment_line.inbound_shipment_line_id =
               OLD.inbound_shipment_line_id;

        IF v_transaction_count <> 1
           OR v_transaction_line_id <> OLD.inbound_shipment_line_id
           OR v_transaction_sku_id <> v_expected_sku_id
           OR v_transaction_location_id <> v_expected_location_id
           OR NOT (v_transaction_delta <=> v_expected_delta)
           OR NOT (v_transaction_type <=> IF(
               OLD.posting_type = 'correction',
               'receipt_correction',
               'receipt'
           ))
           OR NOT (v_transaction_event_key <=> CONCAT(
               'receipt-posting:',
               OLD.inventory_receipt_posting_id
           )) THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Applied receipt posting must have one matching ledger transaction';
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inventory_receipt_posting_bd
    BEFORE DELETE ON inventory_receipt_posting
    FOR EACH ROW
    BEGIN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Receipt postings are append-only';
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inventory_transaction_bu
    BEFORE UPDATE ON inventory_transaction
    FOR EACH ROW
    BEGIN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Inventory transactions are append-only';
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inventory_transaction_bd
    BEFORE DELETE ON inventory_transaction
    FOR EACH ROW
    BEGIN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Inventory transactions are append-only';
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inbound_shipment_line_a4_bi
    BEFORE INSERT ON inbound_shipment_line
    FOR EACH ROW FOLLOWS trg_inbound_shipment_line_bi
    BEGIN
        IF NEW.quantity_received <> 0 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Receipts must be posted through inventory_receipt_posting';
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inbound_shipment_line_a4_bu
    BEFORE UPDATE ON inbound_shipment_line
    FOR EACH ROW FOLLOWS trg_inbound_shipment_line_bu
    BEGIN
        DECLARE v_posting_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_posting_line_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_posting_po_line_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_posting_type VARCHAR(16) DEFAULT NULL;
        DECLARE v_prior_quantity DECIMAL(18,4) DEFAULT NULL;
        DECLARE v_target_quantity DECIMAL(18,4) DEFAULT NULL;
        DECLARE v_delta_uom DECIMAL(18,4) DEFAULT NULL;
        DECLARE v_factor DECIMAL(18,8) DEFAULT NULL;
        DECLARE v_prior_line_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_target_line_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_prior_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_target_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_actual_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_expected_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_missing TINYINT DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_missing = 1;

        IF NOT (NEW.quantity_received <=> OLD.quantity_received) THEN
            SELECT inventory_receipt_posting_id, inbound_shipment_line_id,
                   purchase_order_line_id, posting_type, prior_quantity_received,
                   target_quantity_received, quantity_delta_uom, uom_to_base_factor,
                   prior_line_received_base, target_line_received_base,
                   prior_po_received_base, target_po_received_base
              INTO v_posting_id, v_posting_line_id, v_posting_po_line_id, v_posting_type,
                   v_prior_quantity, v_target_quantity, v_delta_uom, v_factor,
                   v_prior_line_base, v_target_line_base, v_prior_po_base, v_target_po_base
              FROM inventory_receipt_posting
             WHERE pending_line_id = OLD.inbound_shipment_line_id
               AND posting_status = 'pending';

            IF v_missing = 1 OR v_posting_id IS NULL THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'No pending inventory receipt posting matches this update';
            END IF;
            IF v_posting_line_id <> OLD.inbound_shipment_line_id
               OR NOT (v_posting_po_line_id <=> OLD.purchase_order_line_id)
               OR NOT (NEW.purchase_order_line_id <=> OLD.purchase_order_line_id)
               OR NOT (v_prior_quantity <=> OLD.quantity_received)
               OR NOT (v_target_quantity <=> NEW.quantity_received)
               OR NOT (v_delta_uom <=> NEW.quantity_received - OLD.quantity_received)
               OR NOT (v_factor <=> OLD.uom_to_base_factor)
               OR NOT (v_factor <=> NEW.uom_to_base_factor)
               OR NOT (v_prior_line_base <=> OLD.quantity_received * OLD.uom_to_base_factor)
               OR NOT (v_target_line_base <=> NEW.quantity_received * NEW.uom_to_base_factor) THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Receipt posting does not match the inbound line update';
            END IF;

            IF OLD.purchase_order_line_id IS NULL THEN
                IF v_prior_po_base IS NOT NULL OR v_target_po_base IS NOT NULL THEN
                    SIGNAL SQLSTATE '45000'
                        SET MESSAGE_TEXT = 'Unlinked receipt cannot carry PO counter values';
                END IF;
            ELSE
                SET v_missing = 0;
                SELECT quantity_received_base INTO v_actual_po_base
                  FROM purchase_order_line
                 WHERE purchase_order_line_id = OLD.purchase_order_line_id
                 FOR UPDATE;
                IF v_missing = 1 OR v_actual_po_base IS NULL
                   OR NOT (v_actual_po_base <=> v_prior_po_base) THEN
                    SIGNAL SQLSTATE '45000'
                        SET MESSAGE_TEXT = 'PO receipt counter differs from the captured prior value';
                END IF;
                SET v_expected_po_base = CAST(
                    v_prior_po_base
                    + ((NEW.quantity_received * NEW.uom_to_base_factor)
                       - (OLD.quantity_received * OLD.uom_to_base_factor))
                    AS DECIMAL(28,8)
                );
                IF NOT (v_expected_po_base <=> v_target_po_base) THEN
                    SIGNAL SQLSTATE '45000'
                        SET MESSAGE_TEXT = 'Receipt posting target PO counter is inconsistent';
                END IF;
            END IF;
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inbound_shipment_line_a4_au
    AFTER UPDATE ON inbound_shipment_line
    FOR EACH ROW FOLLOWS trg_inbound_shipment_line_au
    BEGIN
        DECLARE v_posting_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_posting_type VARCHAR(16) DEFAULT NULL;
        DECLARE v_po_line_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_prior_line_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_target_line_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_prior_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_target_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_actual_po_base DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_delta_base DECIMAL(38,12) DEFAULT NULL;
        DECLARE v_sku_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_location_id BIGINT UNSIGNED DEFAULT NULL;
        DECLARE v_missing TINYINT DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_missing = 1;

        IF NOT (NEW.quantity_received <=> OLD.quantity_received) THEN
            SELECT inventory_receipt_posting_id, posting_type, purchase_order_line_id,
                   prior_line_received_base, target_line_received_base,
                   prior_po_received_base, target_po_received_base
              INTO v_posting_id, v_posting_type, v_po_line_id, v_prior_line_base,
                   v_target_line_base, v_prior_po_base, v_target_po_base
              FROM inventory_receipt_posting
             WHERE pending_line_id = NEW.inbound_shipment_line_id
               AND posting_status = 'pending';
            IF v_missing = 1 OR v_posting_id IS NULL THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Pending receipt posting disappeared before ledger creation';
            END IF;

            IF v_po_line_id IS NOT NULL THEN
                SET v_missing = 0;
                SELECT quantity_received_base INTO v_actual_po_base
                  FROM purchase_order_line
                 WHERE purchase_order_line_id = v_po_line_id;
                IF v_missing = 1 OR NOT (v_actual_po_base <=> v_target_po_base) THEN
                    SIGNAL SQLSTATE '45000'
                        SET MESSAGE_TEXT = 'A3 PO receipt counter did not reach the expected target';
                END IF;
                SET v_delta_base = v_target_po_base - v_prior_po_base;
            ELSE
                SET v_delta_base = v_target_line_base - v_prior_line_base;
            END IF;
            IF v_delta_base = 0 THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Receipt posting must produce a nonzero base-unit delta';
            END IF;

            SELECT inbound_shipment_line.sku_id, inbound_shipment.location_id
              INTO v_sku_id, v_location_id
              FROM inbound_shipment_line
              JOIN inbound_shipment
                ON inbound_shipment.inbound_shipment_id =
                   inbound_shipment_line.inbound_shipment_id
             WHERE inbound_shipment_line.inbound_shipment_line_id =
                   NEW.inbound_shipment_line_id;

            INSERT INTO inventory_transaction (
                inventory_receipt_posting_id,
                inbound_shipment_line_id,
                sku_id,
                location_id,
                transaction_type,
                quantity_delta_base,
                source_event_key
            ) VALUES (
                v_posting_id,
                NEW.inbound_shipment_line_id,
                v_sku_id,
                v_location_id,
                IF(v_posting_type = 'correction', 'receipt_correction', 'receipt'),
                v_delta_base,
                CONCAT('receipt-posting:', v_posting_id)
            );

            UPDATE inventory_receipt_posting
               SET posting_status = 'applied',
                   pending_line_id = NULL,
                   applied_at = CURRENT_TIMESTAMP
             WHERE inventory_receipt_posting_id = v_posting_id
               AND posting_status = 'pending';
            IF ROW_COUNT() <> 1 THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Receipt posting status could not be finalized';
            END IF;
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_inbound_shipment_line_a4_bd
    BEFORE DELETE ON inbound_shipment_line
    FOR EACH ROW
    BEGIN
        IF OLD.quantity_received <> 0
           OR EXISTS (
               SELECT 1
                 FROM inventory_receipt_posting
                WHERE inbound_shipment_line_id = OLD.inbound_shipment_line_id
           ) THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'A receipt-linked inbound line cannot be deleted';
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_planning_supply_snapshot_bi
    BEFORE INSERT ON planning_supply_snapshot
    FOR EACH ROW
    BEGIN
        DECLARE v_run_status VARCHAR(20) DEFAULT NULL;
        DECLARE v_residual DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_allocated DECIMAL(38,8) DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_run_status = NULL;
        SELECT run_status INTO v_run_status
          FROM planning_run
         WHERE planning_run_id = NEW.planning_run_id
         FOR UPDATE;
        IF v_run_status IS NULL OR v_run_status NOT IN ('draft', 'validating') THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Planning supply may only be changed in an editable run';
        END IF;
        IF NEW.source_kind = 'po_allocation' THEN
            SELECT residual_base INTO v_residual
              FROM planning_po_residual_snapshot
             WHERE planning_po_residual_snapshot_id =
                   NEW.planning_po_residual_snapshot_id
               AND planning_run_id = NEW.planning_run_id
             FOR UPDATE;
            IF v_residual IS NULL THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'PO residual snapshot was not found';
            END IF;
            SELECT COALESCE(SUM(candidate_quantity_base), 0)
              INTO v_allocated
              FROM planning_supply_snapshot
             WHERE planning_po_residual_snapshot_id =
                   NEW.planning_po_residual_snapshot_id;
            IF v_allocated + NEW.candidate_quantity_base > v_residual THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'PO destination allocations exceed the eligible residual';
            END IF;
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_planning_supply_snapshot_bu
    BEFORE UPDATE ON planning_supply_snapshot
    FOR EACH ROW
    BEGIN
        DECLARE v_run_status VARCHAR(20) DEFAULT NULL;
        DECLARE v_residual DECIMAL(28,8) DEFAULT NULL;
        DECLARE v_allocated DECIMAL(38,8) DEFAULT 0;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_run_status = NULL;
        IF NEW.planning_run_id <> OLD.planning_run_id
           OR NOT (NEW.source_kind <=> OLD.source_kind)
           OR NOT (NEW.inbound_shipment_line_id <=> OLD.inbound_shipment_line_id)
           OR NOT (NEW.planning_po_residual_snapshot_id
                   <=> OLD.planning_po_residual_snapshot_id)
           OR NEW.allocation_no <> OLD.allocation_no THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Planning supply source identity is immutable';
        END IF;
        SELECT run_status INTO v_run_status
          FROM planning_run
         WHERE planning_run_id = OLD.planning_run_id
         FOR UPDATE;
        IF v_run_status IS NULL OR v_run_status NOT IN ('draft', 'validating') THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Planning supply may only be changed in an editable run';
        END IF;
        IF NEW.source_kind = 'po_allocation' THEN
            SELECT residual_base INTO v_residual
              FROM planning_po_residual_snapshot
             WHERE planning_po_residual_snapshot_id =
                   NEW.planning_po_residual_snapshot_id
               AND planning_run_id = NEW.planning_run_id
             FOR UPDATE;
            IF v_residual IS NULL THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'PO residual snapshot was not found';
            END IF;
            SELECT COALESCE(SUM(candidate_quantity_base), 0)
              INTO v_allocated
              FROM planning_supply_snapshot
             WHERE planning_po_residual_snapshot_id =
                   NEW.planning_po_residual_snapshot_id
               AND planning_supply_snapshot_id <> OLD.planning_supply_snapshot_id;
            IF v_allocated + NEW.candidate_quantity_base > v_residual THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'PO destination allocations exceed the eligible residual';
            END IF;
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_planning_supply_snapshot_bd
    BEFORE DELETE ON planning_supply_snapshot
    FOR EACH ROW
    BEGIN
        DECLARE v_run_status VARCHAR(20) DEFAULT NULL;
        DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_run_status = NULL;
        SELECT run_status INTO v_run_status
          FROM planning_run
         WHERE planning_run_id = OLD.planning_run_id
         FOR UPDATE;
        IF v_run_status IS NULL OR v_run_status NOT IN ('draft', 'validating') THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Planning supply may only be changed in an editable run';
        END IF;
    END
    """)
    op.execute("""
    CREATE TRIGGER trg_planning_run_bu
    BEFORE UPDATE ON planning_run
    FOR EACH ROW
    BEGIN
        DECLARE v_mismatched_allocations BIGINT DEFAULT 0;
        DECLARE v_blocking_findings BIGINT DEFAULT 0;
        IF NOT (NEW.run_key <=> OLD.run_key)
           OR NOT (NEW.scenario_key <=> OLD.scenario_key)
           OR NOT (NEW.horizon_start_week <=> OLD.horizon_start_week)
           OR NOT (NEW.horizon_weeks <=> OLD.horizon_weeks)
           OR NOT (NEW.captured_at <=> OLD.captured_at)
           OR NOT (NEW.inventory_snapshot_id <=> OLD.inventory_snapshot_id)
           OR NOT (NEW.inventory_cutoff_version <=> OLD.inventory_cutoff_version)
           OR NOT (NEW.algorithm_version <=> OLD.algorithm_version)
           OR NOT (NEW.severity_policy_version <=> OLD.severity_policy_version)
           OR NOT (NEW.input_fingerprint <=> OLD.input_fingerprint)
           OR NOT (NEW.created_by_user_id <=> OLD.created_by_user_id)
           OR NOT (NEW.created_at <=> OLD.created_at)
           OR (OLD.published_at IS NOT NULL
               AND NOT (NEW.published_at <=> OLD.published_at))
           OR (NEW.run_status <> 'published'
               AND OLD.published_at IS NULL
               AND NEW.published_at IS NOT NULL) THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Planning run inputs and version are immutable';
        END IF;
        IF OLD.run_status = 'published'
           AND NEW.run_status NOT IN ('published', 'superseded') THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'A published planning run cannot return to a draft state';
        END IF;
        IF NEW.run_status = 'published' AND OLD.run_status <> 'published' THEN
            IF OLD.run_status <> 'completed' THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Only completed planning runs can be published';
            END IF;
            SELECT COUNT(*) INTO v_mismatched_allocations
              FROM planning_po_residual_snapshot AS r
              LEFT JOIN (
                    SELECT planning_po_residual_snapshot_id,
                           SUM(candidate_quantity_base) AS allocated_base
                      FROM planning_supply_snapshot
                     WHERE source_kind = 'po_allocation'
                     GROUP BY planning_po_residual_snapshot_id
              ) AS a
                ON a.planning_po_residual_snapshot_id =
                   r.planning_po_residual_snapshot_id
             WHERE r.planning_run_id = OLD.planning_run_id
               AND COALESCE(a.allocated_base, 0) <> r.residual_base;
            IF v_mismatched_allocations <> 0 THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'PO destination allocations do not reconcile to residuals';
            END IF;
            SELECT COUNT(*) INTO v_blocking_findings
              FROM planning_run_finding
             WHERE planning_run_id = OLD.planning_run_id
               AND severity IN ('error', 'critical')
               AND COALESCE(resolution_status, 'open') = 'open';
            IF v_blocking_findings <> 0 THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Blocking planning findings must be resolved before publication';
            END IF;
            SET NEW.published_at = COALESCE(NEW.published_at, CURRENT_TIMESTAMP);
        END IF;
    END
    """)


def downgrade() -> None:
    for trigger_name in (
        "trg_planning_run_bu",
        "trg_planning_run_bi",
        "trg_planning_supply_snapshot_bd",
        "trg_planning_supply_snapshot_bu",
        "trg_planning_supply_snapshot_bi",
        "trg_inbound_shipment_line_a4_bd",
        "trg_inbound_shipment_line_a4_au",
        "trg_inbound_shipment_line_a4_bu",
        "trg_inbound_shipment_line_a4_bi",
        "trg_inventory_receipt_posting_bd",
        "trg_inventory_receipt_posting_bu",
        "trg_inventory_transaction_bd",
        "trg_inventory_transaction_bu",
        "trg_inventory_transaction_bi",
        "trg_inventory_snapshot_bi",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    for table_name in (
        "shortage_cause",
        "planning_run_finding",
        "pab_week",
        "planning_supply_snapshot",
        "planning_po_residual_snapshot",
        "forecast_demand_week",
        "planning_item",
        "planning_run",
        "supply_cancellation",
        "inventory_transaction",
        "inventory_receipt_posting",
        "inventory_balance",
        "inventory_snapshot",
        "inventory_ledger_control",
    ):
        op.drop_table(table_name)
