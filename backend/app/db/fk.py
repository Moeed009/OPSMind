from sqlalchemy import ForeignKeyConstraint


def tenant_fk(column: str, parent_table: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent_table}.tenant_id", f"{parent_table}.id"],
        ondelete=ondelete,
    )