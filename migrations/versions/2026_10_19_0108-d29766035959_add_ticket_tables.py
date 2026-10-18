"""add ticket tables

Revision ID: d29766035959
Revises: 66a27c1f8fdc
Create Date: 2026-10-19 01:08:38.132857

"""

from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401  (tipo Vector en migraciones autogeneradas)
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d29766035959"
down_revision: str | Sequence[str] | None = "66a27c1f8fdc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Revisada a mano: el autogenerate añadía, para cada columna sa.Enum, dos
# CheckConstraint explícitos que repetían el que ya crea el propio sa.Enum
# (create_constraint=True). Con la convención de nombres, los tres se llaman
# igual (p. ej. ck_tickets_route) y Postgres rechaza el segundo. Quitados.


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "received",
                "processing",
                "answered",
                "in_human_queue",
                "contacted",
                "resolved",
                name="ticket_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="received",
            nullable=False,
        ),
        sa.Column("intent", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("ticket_type", sa.String(length=50), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=True),
        sa.Column("priority_reason", sa.Text(), nullable=True),
        sa.Column(
            "route",
            sa.Enum(
                "ai", "human", name="route", native_enum=False, create_constraint=True
            ),
            nullable=True,
        ),
        sa.Column("route_reason", sa.Text(), nullable=True),
        sa.Column("classifier_version", sa.String(length=50), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_agent_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_tickets_confidence_range")
        ),
        sa.CheckConstraint(
            "length(text) <= 2000", name=op.f("ck_tickets_text_max_length")
        ),
        sa.CheckConstraint(
            "length(trim(text)) > 0", name=op.f("ck_tickets_text_not_empty")
        ),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 3", name=op.f("ck_tickets_priority_range")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"],
            ["users.id"],
            name=op.f("fk_tickets_assigned_agent_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["users.id"],
            name=op.f("fk_tickets_customer_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
    )
    op.create_index(
        op.f("ix_tickets_assigned_agent_id"),
        "tickets",
        ["assigned_agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_customer_id_created_at",
        "tickets",
        ["customer_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_status_due_at", "tickets", ["status", "due_at"], unique=False
    )
    op.create_table(
        "ticket_corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("predicted_intent", sa.String(length=100), nullable=True),
        sa.Column("corrected_intent", sa.String(length=100), nullable=True),
        sa.Column("predicted_priority", sa.SmallInteger(), nullable=True),
        sa.Column("corrected_priority", sa.SmallInteger(), nullable=True),
        sa.Column(
            "predicted_route",
            sa.Enum(
                "ai",
                "human",
                name="predicted_route",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "corrected_route",
            sa.Enum(
                "ai",
                "human",
                name="corrected_route",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "corrected_priority BETWEEN 1 AND 3",
            name=op.f("ck_ticket_corrections_corrected_priority_range"),
        ),
        sa.CheckConstraint(
            "predicted_priority BETWEEN 1 AND 3",
            name=op.f("ck_ticket_corrections_predicted_priority_range"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["users.id"],
            name=op.f("fk_ticket_corrections_agent_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_corrections_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_corrections")),
    )
    op.create_index(
        op.f("ix_ticket_corrections_ticket_id"),
        "ticket_corrections",
        ["ticket_id"],
        unique=False,
    )
    op.create_table(
        "ticket_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event",
            sa.Enum(
                "created",
                "status_changed",
                "assigned",
                "unassigned",
                "call_failed",
                "escalated",
                "customer_replied",
                name="event",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "from_status",
            sa.Enum(
                "received",
                "processing",
                "answered",
                "in_human_queue",
                "contacted",
                "resolved",
                name="from_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum(
                "received",
                "processing",
                "answered",
                "in_human_queue",
                "contacted",
                "resolved",
                name="to_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(event = 'status_changed' AND from_status IS NOT NULL AND to_status IS NOT NULL) OR (event <> 'status_changed' AND from_status IS NULL AND to_status IS NULL)",
            name=op.f("ck_ticket_events_statuses_only_on_status_changed"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_ticket_events_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_events_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_events")),
    )
    op.create_index(
        "ix_ticket_events_ticket_id_created_at",
        "ticket_events",
        ["ticket_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column(
            "author",
            sa.Enum(
                "customer",
                "system",
                "ai",
                "agent",
                name="author",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "sent",
                name="message_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="sent",
            nullable=False,
        ),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("judge_passed", sa.Boolean(), nullable=True),
        sa.Column("judge_reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "author <> 'customer' OR length(content) <= 2000",
            name=op.f("ck_ticket_messages_customer_content_max_length"),
        ),
        sa.CheckConstraint(
            "author = 'agent' OR agent_id IS NULL",
            name=op.f("ck_ticket_messages_agent_only_for_agent"),
        ),
        sa.CheckConstraint(
            "author = 'ai' OR status = 'sent'",
            name=op.f("ck_ticket_messages_draft_only_for_ai"),
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name=op.f("ck_ticket_messages_content_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["users.id"],
            name=op.f("fk_ticket_messages_agent_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name=op.f("fk_ticket_messages_ticket_id_tickets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_messages")),
    )
    op.create_index(
        "ix_ticket_messages_ticket_id_created_at",
        "ticket_messages",
        ["ticket_id", "created_at"],
        unique=False,
    )
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.Enum(
                "customer",
                "agent",
                name="user_role",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="customer",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("phone", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "phone")
    op.drop_column("users", "role")
    op.drop_index(
        "ix_ticket_messages_ticket_id_created_at", table_name="ticket_messages"
    )
    op.drop_table("ticket_messages")
    op.drop_index("ix_ticket_events_ticket_id_created_at", table_name="ticket_events")
    op.drop_table("ticket_events")
    op.drop_index(
        op.f("ix_ticket_corrections_ticket_id"), table_name="ticket_corrections"
    )
    op.drop_table("ticket_corrections")
    op.drop_index("ix_tickets_status_due_at", table_name="tickets")
    op.drop_index("ix_tickets_customer_id_created_at", table_name="tickets")
    op.drop_index(op.f("ix_tickets_assigned_agent_id"), table_name="tickets")
    op.drop_table("tickets")
