"""
models package — business logic (no Qt imports allowed in this layer).

Modules land progressively:
    user.py       Phase 1 (PIN hashing) → Phase 2 (full auth model)
    product.py    Phase 3
    order.py      Phase 3 (cart + tax-inclusive totals)
    shift.py      Phase 4
    inventory.py  Phase 5
"""
